"""Pretrained local YOLO proposals, never a source of independent ground truth."""

from pathlib import Path
from typing import Any

from traffic_vision.contracts import BoundingBox
from traffic_vision.dataset.files import digest_file, inside, write_new_json
from traffic_vision.dataset.labels import validate_boxes
from traffic_vision.dataset.schema import DatasetSpec, FrameAnnotation, Layer, ObjectAnnotation
from traffic_vision.manifest import ModelManifest
from traffic_vision.settings import VisionSettings


def propose_annotations(path: Path, destination: Path) -> dict[str, Any]:
    import cv2
    import numpy as np
    import torch
    from ultralytics import YOLO  # type: ignore[attr-defined]

    if destination.exists():
        raise FileExistsError("Proposals cannot overwrite labels; choose a new destination")
    dataset = DatasetSpec.model_validate_json(path.read_text(encoding="utf-8"))
    settings = VisionSettings(device="cpu")
    torch.set_num_threads(settings.torch_threads)
    manifest = ModelManifest.load(settings.manifest_path)
    model: Any = YOLO(str(settings.model_path))
    manifest.validate_model(model.names, settings.model_path, settings.image_size)
    classes = {kind: key for key, kind in dataset.class_mapping.items()}
    if len(classes) != len(dataset.class_mapping):
        raise ValueError("Domain class mapping must be unambiguous")
    # Validate immutable image references before inference; annotations remain untouched.
    images = []
    for frame in dataset.frames:
        image_path = inside(path.parent, frame.image.path)
        if digest_file(image_path) != frame.image.sha256:
            raise ValueError("Input image hash mismatch")
        images.append(image_path)
    destination.mkdir(parents=True)
    rows = []
    for frame, image_path in zip(dataset.frames, images, strict=True):
        image = cv2.imdecode(
            np.frombuffer(image_path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR
        )
        if image is None:
            raise ValueError("Corrupt proposal image")
        height, width = image.shape[:2]
        result = model.predict(
            image, device="cpu", imgsz=settings.image_size, conf=settings.confidence, verbose=False
        )[0]
        objects = []
        for index, box in enumerate(result.boxes):
            kind = manifest.domain_mapping[manifest.class_names[int(box.cls.item())]]
            if kind not in classes:
                continue
            x1, y1, x2, y2 = map(float, box.xyxy[0].tolist())
            objects.append(
                ObjectAnnotation(
                    annotation_id=f"proposal-{index}",
                    class_id=classes[kind],
                    domain_class=kind,
                    bbox=BoundingBox(
                        x1=max(0, x1), y1=max(0, y1), x2=min(width, x2), y2=min(height, y2)
                    ),
                )
            )
        annotation = FrameAnnotation(
            frame_id=frame.frame_id,
            layers=(Layer.DETECTION,),
            objects=tuple(objects),
            provenance="model_predictions",
        )
        validate_boxes(annotation, width, height, dataset.class_mapping)
        label_path = destination / f"{frame.frame_id}.json"
        write_new_json(label_path, annotation.model_dump(mode="json"))
        rows.append(
            {
                "frame_id": frame.frame_id,
                "image_sha256": frame.image.sha256,
                "annotation_sha256": digest_file(label_path),
                "annotation_path": label_path.name,
            }
        )
    report = {
        "status": "UNVERIFIED",
        "ground_truth": False,
        "model_weights_sha256": manifest.weights_hash,
        "frames": rows,
        "review_required": "Every proposal including empty frames requires attributed human review",
    }
    write_new_json(destination / "proposals.json", report)
    return report

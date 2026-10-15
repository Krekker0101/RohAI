"""Local detector diagnostics; human labels optional, independent validation never assumed."""

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from traffic_core.models import ObjectKind
from traffic_vision.dataset.files import digest_file
from traffic_vision.dataset.labels import validate_boxes
from traffic_vision.dataset.schema import FrameAnnotation
from traffic_vision.evaluation.metrics import Prediction, PredictionFrame, detection_metrics
from traffic_vision.evaluation.vision import small_object_metrics
from traffic_vision.manifest import ModelManifest
from traffic_vision.settings import VisionSettings

from scripts.import_review_labels import CLASSES


def score(
    truth: dict[str, FrameAnnotation], predictions: dict[str, PredictionFrame], confidence: float
) -> dict[str, Any]:
    if not 0.001 <= confidence <= 1:
        raise ValueError("Confidence must be within .001..1")
    if not truth:
        return {"status": "UNSCORED", "reason": "No human-reviewed labels"}
    if not set(truth).issubset(predictions):
        raise ValueError("Missing predictions for reviewed frames")
    for annotation in truth.values():
        if annotation.provenance != "human" or set(annotation.exhaustive_classes) != set(range(6)):
            raise ValueError("Complete human detection review required")
    selected = {key: predictions[key] for key in truth}
    ids = tuple(range(len(CLASSES)))
    return {
        "status": "REVIEW_DIAGNOSTIC_NOT_INDEPENDENT_ACCEPTANCE",
        "reviewed_frames": len(truth),
        "detection": detection_metrics(truth, selected, ids, operating_confidence=confidence),
        "small_objects": small_object_metrics(truth, selected, ids, confidence),
        "independent_test": False,
        "training_started": False,
    }


def run(review: Path, output: Path, labels: Path | None, confidence: float) -> dict[str, Any]:
    import cv2
    import numpy as np

    if output.exists():
        raise FileExistsError("Choose a new diagnostics directory")
    if not 0.001 <= confidence <= 1:
        raise ValueError("Confidence must be within .001..1")
    manifest = json.loads((review / "review.json").read_text(encoding="utf-8"))
    if not manifest["frames"]:
        raise ValueError("No review frames")
    classes = {index: ObjectKind(name) for index, name in enumerate(CLASSES)}
    truth: dict[str, FrameAnnotation] = {}
    if labels is not None:
        metadata = json.loads((labels / "import.json").read_text(encoding="utf-8"))
        if metadata["source_sha256"] != manifest["source_sha256"] or metadata["classes"] != {
            str(key): value.value for key, value in classes.items()
        }:
            raise ValueError("Label source or class mapping mismatch")
    images = []
    for frame in manifest["frames"]:
        path = (review / frame["image"]).resolve()
        if not path.is_relative_to(review.resolve()) or digest_file(path) != frame["sha256"]:
            raise ValueError("Frame path or hash mismatch")
        key = f"frame-{frame['frame_index']:08d}"
        if any(row[0] == key for row in images):
            raise ValueError("Duplicate review frame")
        images.append((key, frame, path))
        if labels is not None and (labels / f"{key}.json").exists():
            annotation = FrameAnnotation.model_validate_json((labels / f"{key}.json").read_text())
            if annotation.frame_id != key:
                raise ValueError("Annotation frame identity mismatch")
            validate_boxes(annotation, frame["width"], frame["height"], classes)
            truth[key] = annotation
    if labels is not None and not truth:
        raise ValueError("Label directory contains no reviewed frames")
    settings = VisionSettings()
    model_manifest = ModelManifest.load(settings.manifest_path)
    model_manifest.validate_model(
        model_manifest.class_id_mapping, settings.model_path, settings.image_size
    )
    settings.runtime_directory.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YOLO_CONFIG_DIR", str(settings.runtime_directory.resolve()))
    os.environ.setdefault("YOLO_OFFLINE", "true")
    os.environ.setdefault("YOLO_AUTOINSTALL", "false")
    import torch
    from ultralytics import YOLO

    torch.set_num_threads(settings.torch_threads)
    model = YOLO(str(settings.model_path), task="detect")
    model_manifest.validate_model(dict(model.names), settings.model_path, settings.image_size)
    domain_ids = {value: key for key, value in classes.items()}
    model_ids = [
        key
        for key, name in model_manifest.class_id_mapping.items()
        if model_manifest.domain_mapping[name] in domain_ids
    ]
    arguments = dict(
        imgsz=settings.image_size,
        device="cpu",
        verbose=False,
        conf=0.001,
        iou=settings.iou,
        classes=model_ids,
        max_det=settings.max_detections,
    )
    model.predict(np.zeros((640, 640, 3), dtype=np.uint8), **arguments)
    torch.set_num_threads(settings.torch_threads)
    predictions = {}
    elapsed = []
    for key, frame, path in images:
        image = cv2.imread(str(path))
        if image is None or image.shape[:2] != (frame["height"], frame["width"]):
            raise ValueError("Image decode or dimensions mismatch")
        started = time.perf_counter()
        result = model.predict(image, **arguments)[0]
        elapsed.append((time.perf_counter() - started) * 1000)
        detections = []
        if result.boxes is not None:
            for box in result.boxes:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                name = model_manifest.class_id_mapping[int(box.cls.item())]
                detections.append(
                    Prediction(
                        bbox={"x1": x1, "y1": y1, "x2": x2, "y2": y2},
                        class_id=domain_ids[model_manifest.domain_mapping[name]],
                        confidence=float(box.conf.item()),
                    )
                )
        predictions[key] = PredictionFrame(frame_id=key, detections=tuple(detections))
    report = {
        "source_sha256": manifest["source_sha256"],
        "model_sha256": model_manifest.weights_hash,
        "frames": len(images),
        "operating_confidence": confidence,
        "settings": {
            "image_size": settings.image_size,
            "device": "cpu",
            "torch_threads": settings.torch_threads,
            "prediction_floor": 0.001,
        },
        "detector_ms": {"p50": float(np.median(elapsed)), "p95": float(np.percentile(elapsed, 95))},
        "timing_scope": "Warmed predict only; no decode, tracking, analytics or camera delay",
        "quality": score(truth, predictions, confidence),
        "note": "Detections are predictions, never human labels; no physical readiness claim",
    }
    output.mkdir(parents=True)
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (output / "predictions.json").write_text(
        json.dumps(
            {
                "source_sha256": manifest["source_sha256"],
                "model_sha256": model_manifest.weights_hash,
                "classes": list(CLASSES),
                "frames": [
                    prediction.model_dump(mode="json") for prediction in predictions.values()
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path)
    parser.add_argument("--labels", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confidence", type=float, default=0.25)
    args = parser.parse_args()
    print(json.dumps(run(args.review, args.output, args.labels, args.confidence)))


if __name__ == "__main__":
    main()

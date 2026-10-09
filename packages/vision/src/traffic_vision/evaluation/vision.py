"""Pretrained inference only. Held-out ground truth never enters the detector."""

import importlib.metadata
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field
from traffic_core.models import Model

from traffic_vision.contracts import BoundingBox
from traffic_vision.dataset.files import canonical_hash, digest_file, inside
from traffic_vision.dataset.release import open_release, select_frames
from traffic_vision.dataset.schema import DatasetSpec, FrameAnnotation, FrameRecord, Layer, Split
from traffic_vision.dataset.validation import read_annotations, validate_dataset
from traffic_vision.evaluation.metrics import (
    Prediction,
    PredictionFrame,
    detection_metrics,
    tracking_metrics,
)
from traffic_vision.manifest import ModelManifest
from traffic_vision.settings import VisionSettings


class PredictionRun(Model):
    schema_version: str = "1.0"
    dataset_manifest_sha256: str
    model_weights_sha256: str
    split: Split
    run_kind: str
    frames: tuple[PredictionFrame, ...]
    settings: dict[str, Any] = Field(default_factory=dict)
    run_sha256: str

    def verify(self) -> None:
        if canonical_hash(self.model_dump(mode="json", exclude={"run_sha256"})) != self.run_sha256:
            raise ValueError("Prediction run integrity failure")
        if len({f.frame_id for f in self.frames}) != len(self.frames):
            raise ValueError("Duplicate prediction frame IDs")


def code_fingerprint() -> str:
    import traffic_core.models as core_models

    packages = {
        "traffic_vision": Path(__file__).resolve().parents[1],
        "traffic_core": Path(core_models.__file__).resolve().parent,
    }
    return canonical_hash(
        {
            f"{name}/{str(p.relative_to(root)).replace(chr(92), '/')}": digest_file(p)
            for name, root in packages.items()
            for p in sorted(root.rglob("*.py"))
        }
    )


def strata(dataset: DatasetSpec, frames: tuple[FrameRecord, ...]) -> dict[str, tuple[str, ...]]:
    sources = {s.source_id: s for s in dataset.sources}
    groups: dict[str, list[str]] = {"all": []}
    for frame in frames:
        source = sources[frame.source_id]
        values = {
            "camera": source.camera_id,
            "day_night": source.conditions.day_night,
            "weather": source.conditions.weather,
            "traffic_density": source.conditions.traffic_density,
            "occlusion": source.conditions.occlusion,
            "scene_domain": source.scene_domain,
            "intersection": source.intersection_id,
            "camera_angle": source.conditions.camera_angle,
            "lighting": source.conditions.lighting,
        }
        groups["all"].append(frame.frame_id)
        for key, value in values.items():
            groups.setdefault(f"{key}={value}", []).append(frame.frame_id)
    return {key: tuple(value) for key, value in groups.items()}


def evaluate_predictions(
    dataset: DatasetSpec,
    frames: tuple[FrameRecord, ...],
    annotations: dict[str, FrameAnnotation],
    predictions: tuple[PredictionFrame, ...],
    *,
    operating_confidence: float = 0.1,
) -> dict[str, Any]:
    predicted = {p.frame_id: p for p in predictions}
    if len(predicted) != len(predictions) or set(predicted) != {f.frame_id for f in frames}:
        raise ValueError("Prediction frame coverage differs from selected split")
    classes = tuple(sorted(dataset.class_mapping))
    if any(
        p.class_id not in dataset.class_mapping
        for frame in predictions
        for p in (*frame.detections, *frame.tracks)
    ):
        raise ValueError("Prediction contains unknown domain class")
    groups: dict[str, Any] = {}
    ordering = {f.frame_id: (f.sequence_id or f.source_id, f.timestamp) for f in frames}
    for stratum, ids in strata(dataset, frames).items():
        detection_ids = [key for key in ids if Layer.DETECTION in annotations[key].layers]
        detection = (
            detection_metrics(
                {k: annotations[k] for k in detection_ids},
                {k: predicted[k] for k in detection_ids},
                classes,
                operating_confidence=operating_confidence,
            )
            if detection_ids
            else None
        )
        tracking_ids = sorted(
            (key for key in ids if Layer.TRACKING in annotations[key].layers),
            key=lambda key: ordering[key],
        )
        tracking_frames = tuple(
            (ordering[k][0], annotations[k], predicted[k]) for k in tracking_ids
        )
        tracking = tracking_metrics(tracking_frames, classes) if tracking_frames else None
        groups[stratum] = {
            "detection": detection,
            "tracking": tracking,
            "tracking_per_class": {
                dataset.class_mapping[c].value: tracking_metrics(tracking_frames, (c,))
                for c in classes
            }
            if tracking_frames
            else {},
            "missing_layers": [
                name
                for name, value in (("detection", detection), ("tracking", tracking))
                if value is None
            ],
        }
    return {
        "detection": groups["all"]["detection"],
        "small_objects": small_object_metrics(
            {key: annotations[key] for key in predicted}, predicted, classes, operating_confidence
        ),
        "tracking": groups["all"]["tracking"],
        "strata": groups,
        "class_mapping": {str(k): v.value for k, v in dataset.class_mapping.items()},
    }


def small_object_metrics(
    annotations: dict[str, FrameAnnotation],
    predictions: dict[str, PredictionFrame],
    classes: tuple[int, ...],
    confidence: float,
) -> dict[str, Any]:
    """Pixel-area stratum: small <32^2 at original resolution; larger boxes ignored."""

    def small(bbox: BoundingBox) -> bool:
        return (bbox.x2 - bbox.x1) * (bbox.y2 - bbox.y1) < 32 * 32

    truth = {
        key: ann.model_copy(
            update={
                "objects": tuple(
                    obj.model_copy(update={"ignore": obj.ignore or not small(obj.bbox)})
                    for obj in ann.objects
                )
            }
        )
        for key, ann in annotations.items()
        if Layer.DETECTION in ann.layers
    }
    predicted = {
        key: predictions[key].model_copy(
            update={"detections": tuple(p for p in predictions[key].detections if small(p.bbox))}
        )
        for key in truth
    }
    return {
        "area_protocol": "Original-image bbox area <1024 px2; larger GT ignored",
        **detection_metrics(truth, predicted, classes, operating_confidence=confidence),
    }


def run_pretrained(
    release_path: Path, *, split: Split = Split.TEST, settings: VisionSettings | None = None
) -> tuple[dict[str, Any], PredictionRun | None]:
    settings = settings or VisionSettings()
    pinned = ModelManifest.load(Path("configs/models/yolo26n.json"))
    if ModelManifest.load(settings.manifest_path) != pinned:
        raise ValueError("Baseline requires the pinned pretrained model")
    return run_model(release_path, split=split, settings=settings, run_kind="pretrained_yolo26n")


def run_model(
    release_path: Path, *, split: Split, settings: VisionSettings, run_kind: str
) -> tuple[dict[str, Any], PredictionRun | None]:
    settings = settings or VisionSettings()
    manifest = ModelManifest.load(settings.manifest_path)
    report: dict[str, Any] = {
        "schema_version": "1.0",
        "created_at": datetime.now(UTC).isoformat(),
        "run_kind": run_kind,
        "split": split.value,
        "status": "BLOCKED",
        "model_id": manifest.model_id,
        "model_manifest_sha256": digest_file(settings.manifest_path),
        "model_weights_sha256": manifest.weights_hash,
        "code_sha256": code_fingerprint(),
        "independent_test": False,
        "detection": None,
        "tracking": None,
        "fine_tuning_allowed": False,
        "acceptance_thresholds": None,
    }
    if manifest.architecture != "YOLO26n":
        raise ValueError("This baseline command requires the current YOLO26n model manifest")
    if (
        not settings.model_path.is_file()
        or digest_file(settings.model_path) != manifest.weights_hash
    ):
        report["blocked_reasons"] = ["model_missing_or_hash_mismatch"]
        return finish_report(report), None
    if not release_path.exists():
        report["blocked_reasons"] = ["independent_labeled_release_missing"]
        return finish_report(report), None
    release = open_release(release_path)
    root = release_path if release_path.is_dir() else release_path.parent
    validation = validate_dataset(release.dataset, root)
    report["dataset_manifest_sha256"] = release.manifest_sha256
    report["validation"] = validation.model_dump(mode="json")
    if not validation.valid:
        report["blocked_reasons"] = ["invalid_dataset"]
        return finish_report(report), None
    frames = select_frames(release, split)
    annotations = read_annotations(release.dataset, root)
    detection_frames = [
        f
        for f in frames
        if Layer.DETECTION in annotations[f.frame_id].layers
        and annotations[f.frame_id].exhaustive_classes
    ]
    if not detection_frames or not any(annotations[f.frame_id].objects for f in detection_frames):
        report["blocked_reasons"] = ["no_exhaustive_detection_ground_truth_in_selected_split"]
        return finish_report(report), None
    # It is safe to evaluate fixtures, but they must never open the field baseline training gate.
    independent = (
        split == Split.TEST
        and release.dataset.purpose == "field"
        and all(
            s.usage == "field" and s.pretrained_exposure == "audited_no_known_overlap"
            for s in release.dataset.sources
            if release.dataset.split.assignments[s.source_id] == split
        )
        and validation.near_duplicate_candidates == 0
    )

    import cv2
    import numpy as np

    os.environ.setdefault("YOLO_OFFLINE", "true")
    os.environ.setdefault("YOLO_AUTOINSTALL", "false")
    settings.runtime_directory.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YOLO_CONFIG_DIR", str(settings.runtime_directory.resolve()))
    import torch
    from ultralytics import YOLO  # type: ignore[attr-defined]

    from traffic_vision.detector import YOLODetector
    from traffic_vision.sources import VideoFrame

    torch.manual_seed(0)
    torch.set_num_threads(settings.torch_threads)
    detector: Any = YOLO(str(settings.model_path), task="detect")
    manifest.validate_model(dict(detector.names), settings.model_path, settings.image_size)
    tracker = YOLODetector(settings)
    domain_ids = {kind: class_id for class_id, kind in release.dataset.class_mapping.items()}
    model_ids = [
        i
        for i, name in manifest.class_id_mapping.items()
        if manifest.domain_mapping[name] in domain_ids
    ]
    predictions: list[PredictionFrame] = []
    ordered = sorted(frames, key=lambda f: (f.sequence_id or f.source_id, f.timestamp))
    sequence_ids = {
        key: index
        for index, key in enumerate(sorted({f.sequence_id or f.source_id for f in frames}))
    }
    tracking_sequences = {
        f.sequence_id or f.source_id
        for f in frames
        if Layer.TRACKING in annotations[f.frame_id].layers
        or Layer.ANALYTICS in annotations[f.frame_id].layers
    }
    try:
        if tracking_sequences:
            tracker.open()
        for frame in ordered:
            image = cv2.imdecode(
                np.frombuffer(inside(root, frame.image.path).read_bytes(), dtype=np.uint8),
                cv2.IMREAD_COLOR,
            )
            if image is None:
                raise ValueError(f"Cannot decode {frame.image.path}")
            outputs = detector.predict(
                image,
                conf=0.001,
                iou=settings.iou,
                imgsz=settings.image_size,
                device=settings.device,
                classes=model_ids,
                max_det=settings.max_detections,
                verbose=False,
            )
            boxes = outputs[0].boxes
            detections: list[Prediction] = []
            if boxes is not None:
                for coords, confidence, class_id in zip(
                    boxes.xyxy.cpu().tolist(),
                    boxes.conf.cpu().tolist(),
                    boxes.cls.cpu().tolist(),
                    strict=True,
                ):
                    kind = manifest.domain_mapping[manifest.class_id_mapping[int(class_id)]]
                    if kind in domain_ids:
                        detections.append(
                            Prediction(
                                bbox=BoundingBox(
                                    x1=coords[0], y1=coords[1], x2=coords[2], y2=coords[3]
                                ),
                                class_id=domain_ids[kind],
                                confidence=confidence,
                            )
                        )
            tracks: tuple[Prediction, ...] = ()
            if (frame.sequence_id or frame.source_id) in tracking_sequences:
                output = tracker.process(
                    VideoFrame(
                        np.asarray(image, dtype=np.uint8),
                        frame.timestamp,
                        frame.frame_index,
                        frame.source_id,
                        frame.timestamp,
                        sequence_ids[frame.sequence_id or frame.source_id],
                    )
                )
                tracks = tuple(
                    Prediction(
                        bbox=d.bbox,
                        class_id=domain_ids[d.kind],
                        confidence=d.confidence,
                        track_id=str(d.track_id) if d.track_id is not None else None,
                    )
                    for d in output
                    if d.kind in domain_ids
                )
            predictions.append(
                PredictionFrame(
                    frame_id=frame.frame_id, detections=tuple(detections), tracks=tracks
                )
            )
    finally:
        tracker.close()
    measured = evaluate_predictions(
        release.dataset,
        frames,
        annotations,
        tuple(predictions),
        operating_confidence=settings.confidence,
    )
    independent = (
        independent
        and measured["detection"] is not None
        and measured["detection"]["ground_truth"] > 0
    )
    options = {
        "image_size": settings.image_size,
        "device": settings.device,
        "detection_confidence_floor": 0.001,
        "operating_confidence": settings.confidence,
        "iou": settings.iou,
        "max_detections": settings.max_detections,
        "seed": 0,
        "model_manifest_sha256": digest_file(settings.manifest_path),
        "model_domain_mapping": {
            name: kind.value for name, kind in manifest.domain_mapping.items()
        },
        "ultralytics_version": importlib.metadata.version("ultralytics"),
        "torch_version": torch.__version__,
        "tracker_config_sha256": digest_file(Path(__file__).parents[1] / "bytetrack.yaml"),
        "vision_settings": settings.model_dump(mode="json"),
    }
    payload = {
        "schema_version": "1.0",
        "dataset_manifest_sha256": release.manifest_sha256,
        "model_weights_sha256": manifest.weights_hash,
        "split": split.value,
        "run_kind": run_kind,
        "frames": [p.model_dump(mode="json") for p in predictions],
        "settings": options,
    }
    run = PredictionRun.model_validate({**payload, "run_sha256": canonical_hash(payload)})
    report.update(measured)
    report.update(
        {
            "status": "MEASURED" if independent else "MEASURED_NON_INDEPENDENT",
            "independent_test": independent,
            "settings": options,
            "prediction_run_sha256": run.run_sha256,
            "frames": len(frames),
            "unsupported_domain_classes": [
                kind.value
                for kind in domain_ids
                if kind not in set(manifest.domain_mapping.values())
            ],
            "fine_tuning_allowed": False,
            "next_gate": "Frozen downstream regression gates are required before promotion.",
        }
    )
    return finish_report(report), run


def finish_report(report: dict[str, Any]) -> dict[str, Any]:
    report["report_sha256"] = canonical_hash(
        {k: v for k, v in report.items() if k != "report_sha256"}
    )
    return report

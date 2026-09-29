import os
from pathlib import Path
from typing import Any, Protocol

import cv2
import numpy as np
from traffic_core.models import ObjectKind

from traffic_vision.contracts import BoundingBox
from traffic_vision.manifest import ModelManifest
from traffic_vision.models import Detection
from traffic_vision.settings import VisionSettings
from traffic_vision.sources import SYNTHETIC_COLORS, VideoFrame


class Detector(Protocol):
    name: str

    def open(self) -> None: ...

    def process(self, frame: VideoFrame) -> tuple[Detection, ...]: ...

    def close(self) -> None: ...


class YOLODetector:
    """One model and ByteTrack state per stream, owned exclusively by the inference worker."""

    name = "YOLO26 + ByteTrack"

    def __init__(self, settings: VisionSettings) -> None:
        self.settings = settings
        self.manifest: ModelManifest | None = None
        self._model: Any = None
        self._stream: tuple[str, int] | None = None
        self._last_timestamp: float | None = None
        self._identities: dict[int, int] = {}
        self._next_identity = 1

    def open(self) -> None:
        if self.settings.model_registry is not None:
            from traffic_vision.training.registry import ModelRegistry

            self.settings = self.settings.model_copy(
                update={
                    "model_package": ModelRegistry(self.settings.model_registry).production(),
                    "model_registry": None,
                }
            )
        if self.settings.model_package is not None:
            from traffic_vision.training.package import verify_package

            package = verify_package(self.settings.model_package)
            if package.supported_input.image_size != self.settings.image_size:
                raise ValueError("ModelPackage input size mismatch")
            self.settings = self.settings.model_copy(
                update={
                    "model_path": self.settings.model_package / "weights.pt",
                    "manifest_path": self.settings.model_package / "model.json",
                }
            )
        if not self.settings.model_path.is_file():
            raise FileNotFoundError(
                f"Model missing: {self.settings.model_path}; run scripts/fetch_vision_demo.py"
            )
        self.manifest = ModelManifest.load(self.settings.manifest_path)
        # Verify integrity before deserializing any trusted checkpoint.
        self.manifest.validate_model(
            self.manifest.class_id_mapping, self.settings.model_path, self.settings.image_size
        )
        config_root = Path(os.environ.get("YOLO_CONFIG_DIR", self.settings.runtime_directory))
        config_root.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("YOLO_CONFIG_DIR", str(config_root.resolve()))
        os.environ.setdefault("YOLO_AUTOINSTALL", "false")
        os.environ.setdefault("YOLO_OFFLINE", "true")
        import torch
        from ultralytics import YOLO  # type: ignore[attr-defined]  # dynamic public export

        torch.set_num_threads(self.settings.torch_threads)
        self._model = YOLO(str(self.settings.model_path), task="detect")
        self.manifest.validate_model(
            dict(self._model.names), self.settings.model_path, self.settings.image_size
        )
        self._model.predict(
            np.zeros((self.settings.image_size, self.settings.image_size, 3), dtype=np.uint8),
            imgsz=self.settings.image_size,
            device=self.settings.device,
            verbose=False,
        )

    def process(self, frame: VideoFrame) -> tuple[Detection, ...]:
        if self._model is None or self.manifest is None:
            raise RuntimeError("Detector is not open")
        stream = (frame.source_id, frame.stream_epoch)
        discontinuity = self._last_timestamp is not None and (
            frame.timestamp <= self._last_timestamp
            or frame.timestamp - self._last_timestamp > self.settings.tracker_reset_gap_seconds
        )
        if stream != self._stream or discontinuity:
            # Ultralytics exposes BYTETracker.reset(); this adapter contains the library coupling.
            for tracker in getattr(self._model.predictor, "trackers", []):
                tracker.reset()
            self._identities.clear()
        self._stream = stream
        self._last_timestamp = frame.timestamp
        results = self._model.track(
            source=frame.frame,
            persist=True,
            tracker=str(Path(__file__).with_name("bytetrack.yaml")),
            conf=self.settings.confidence,
            iou=self.settings.iou,
            classes=[
                i
                for i, name in self.manifest.class_id_mapping.items()
                if self.manifest.domain_mapping[name] != ObjectKind.UNKNOWN
            ],
            imgsz=self.settings.image_size,
            device=self.settings.device,
            max_det=self.settings.max_detections,
            verbose=False,
        )
        boxes = results[0].boxes
        if boxes is None:
            return ()
        coordinates = boxes.xyxy.cpu().numpy()
        confidences = boxes.conf.cpu().numpy()
        classes = boxes.cls.cpu().numpy()
        identities = boxes.id.cpu().numpy() if boxes.is_track else None
        height, width = frame.frame.shape[:2]
        detections = []
        for index, (xyxy, confidence, class_id) in enumerate(
            zip(coordinates, confidences, classes, strict=True)
        ):
            name = self.manifest.class_id_mapping.get(int(class_id))
            if name is None:
                raise ValueError("Unexpected inference class ID")
            kind = self.manifest.domain_mapping[name]
            x1, y1, x2, y2 = (float(v) for v in xyxy)
            x1, x2 = max(0, min(width, x1)), max(0, min(width, x2))
            y1, y2 = max(0, min(height, y1)), max(0, min(height, y2))
            if kind is None or x2 <= x1 or y2 <= y1:
                continue
            identity = None
            if identities is not None:
                raw_id = int(identities[index])
                if raw_id not in self._identities:
                    if len(self._identities) >= self.settings.analytics.max_session_tracks:
                        raise RuntimeError("Track identity capacity reached; start a new session")
                    self._identities[raw_id] = self._next_identity
                    self._next_identity += 1
                identity = self._identities[raw_id]
            detections.append(
                Detection(
                    kind=kind,
                    confidence=float(confidence),
                    timestamp=frame.timestamp,
                    bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
                    track_id=identity,
                )
            )
        return tuple(detections)

    def close(self) -> None:
        self._model = None


class CalibrationDetector:
    name = "CALIBRATION / no inference"

    def open(self) -> None:
        pass

    def process(self, frame: VideoFrame) -> tuple[Detection, ...]:
        return ()

    def close(self) -> None:
        pass


class SyntheticDetector(CalibrationDetector):
    """Explicit deterministic fixture detector for the synthetic scene, not a YOLO fallback."""

    name = "SYNTHETIC FIXTURE"

    def process(self, frame: VideoFrame) -> tuple[Detection, ...]:
        detections = []
        kinds = (ObjectKind.CAR, ObjectKind.BUS, ObjectKind.TRUCK, ObjectKind.MOTORCYCLE)
        for identity, (color, kind) in enumerate(zip(SYNTHETIC_COLORS, kinds, strict=True), 1):
            lower = np.maximum(np.asarray(color, dtype=np.int16) - 20, 0).astype(np.uint8)
            upper = np.minimum(np.asarray(color, dtype=np.int16) + 20, 255).astype(np.uint8)
            mask = cv2.inRange(frame.frame, lower, upper)
            points = cv2.findNonZero(mask)
            if points is None:
                continue
            x, y, w, h = cv2.boundingRect(points)
            detections.append(
                Detection(
                    kind=kind,
                    confidence=1,
                    timestamp=frame.timestamp,
                    track_id=identity,
                    bbox=BoundingBox(x1=x, y1=y, x2=x + w, y2=y + h),
                )
            )
        return tuple(detections)

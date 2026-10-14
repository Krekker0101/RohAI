"""Sliced vehicle inference with full-frame deduplication and one global tracker."""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import yaml  # type: ignore[import-untyped]
from numpy.typing import NDArray
from traffic_core.models import ObjectKind

from traffic_vision.contracts import BoundingBox
from traffic_vision.detector import YOLODetector
from traffic_vision.models import Detection
from traffic_vision.settings import AerialSettings, VisionSettings
from traffic_vision.sources import VideoFrame


def positions(length: int, size: int, overlap: float) -> list[int]:
    if length <= size:
        return [0]
    step = max(1, round(size * (1 - overlap)))
    return sorted(set([*range(0, length - size + 1, step), length - size]))


def deduplicate(boxes: NDArray[np.float32], iou: float = 0.45) -> NDArray[np.float32]:
    # All classes here are vehicles. Keep the strongest class for overlapping boxes,
    # including a car/truck disagreement on the same physical object.
    order = np.argsort(-boxes[:, 4], kind="stable")
    keep = []
    while order.size:
        index = int(order[0])
        keep.append(index)
        others = order[1:]
        a, b = boxes[index, :4], boxes[others, :4]
        intersection = np.maximum(0, np.minimum(a[2:], b[:, 2:]) - np.maximum(a[:2], b[:, :2]))
        area = intersection[:, 0] * intersection[:, 1]
        area_a = (a[2] - a[0]) * (a[3] - a[1])
        area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
        overlap = area / np.maximum(area_a + area_b - area, 1e-6)
        order = others[overlap <= iou]
    return np.asarray(boxes[keep], dtype=np.float32)


class AerialDetector(YOLODetector):
    name = "YOLO26 sliced vehicles + global ByteTrack"

    def __init__(self, settings: VisionSettings, aerial: AerialSettings | None = None) -> None:
        super().__init__(settings)
        self.aerial = aerial or AerialSettings()
        self.tracker: Any = None
        self.tiles_per_frame = 0
        self.filtered_large = self.filtered_edges = 0
        self.on_tile_progress: Callable[[int, int], None] | None = None

    def open(self) -> None:
        super().open()
        from ultralytics.trackers.byte_tracker import BYTETracker

        args = yaml.safe_load(Path(__file__).with_name("bytetrack.yaml").read_text())
        self.tracker = BYTETracker(SimpleNamespace(**args))  # type: ignore[no-untyped-call]

    def process(self, frame: VideoFrame) -> tuple[Detection, ...]:
        if self._model is None or self.manifest is None or self.tracker is None:
            raise RuntimeError("Aerial detector is not open")
        from ultralytics.engine.results import Boxes

        stream = (frame.source_id, frame.stream_epoch)
        if stream != self._stream or (
            self._last_timestamp is not None
            and (
                frame.timestamp <= self._last_timestamp
                or frame.timestamp - self._last_timestamp > self.settings.tracker_reset_gap_seconds
            )
        ):
            self.tracker.reset()
            self._identities.clear()
        self._stream, self._last_timestamp = stream, frame.timestamp
        height, width = frame.frame.shape[:2]
        size = self.aerial.tile_size
        views = [(frame.frame, False)]
        if self.aerial.rotated_pass:
            views.append((np.rot90(frame.frame), True))
        offsets = [
            (image, rotated, x, y)
            for image, rotated in views
            for y in positions(image.shape[0], size, self.aerial.overlap)
            for x in positions(image.shape[1], size, self.aerial.overlap)
        ]
        self.tiles_per_frame = len(offsets)
        vehicles = {
            ObjectKind.CAR,
            ObjectKind.TRUCK,
            ObjectKind.BUS,
            ObjectKind.MOTORCYCLE,
            ObjectKind.BICYCLE,
        }
        classes = [
            index
            for index, name in self.manifest.class_id_mapping.items()
            if self.manifest.domain_mapping[name] in vehicles
        ]
        candidates: list[list[float]] = []
        for start in range(0, len(offsets), self.aerial.batch_size):
            batch = offsets[start : start + self.aerial.batch_size]
            crops = [image[y : y + size, x : x + size] for image, _, x, y in batch]
            results = self._model.predict(
                crops,
                imgsz=self.settings.image_size,
                conf=self.aerial.confidence,
                classes=classes,
                device=self.settings.device,
                max_det=self.settings.max_detections,
                verbose=False,
            )
            for (image, rotated, x, y), crop, result in zip(batch, crops, results, strict=True):
                if result.boxes is None:
                    continue
                crop_h, crop_w = crop.shape[:2]
                for values in result.boxes.data.cpu().numpy():
                    x1, y1, x2, y2, confidence, class_id = (float(v) for v in values)
                    if (
                        x1 <= 2
                        and x > 0
                        or y1 <= 2
                        and y > 0
                        or x2 >= crop_w - 2
                        and x + crop_w < image.shape[1]
                        or y2 >= crop_h - 2
                        and y + crop_h < image.shape[0]
                    ):
                        self.filtered_edges += 1
                        continue
                    if max(x2 - x1, y2 - y1) > min(width, height) * self.aerial.max_object_fraction:
                        self.filtered_large += 1
                        continue
                    full_box = [x1 + x, y1 + y, x2 + x, y2 + y]
                    if rotated:
                        a, b, c, d = full_box
                        full_box = [width - d, a, width - b, c]
                    candidates.append([*full_box, confidence, class_id])
            if self.on_tile_progress is not None:
                self.on_tile_progress(min(start + len(batch), len(offsets)), len(offsets))
        boxes = np.asarray(candidates, dtype=np.float32).reshape(-1, 6)
        boxes = deduplicate(boxes)[: self.settings.max_detections]
        native_boxes: Any = Boxes(boxes, (height, width))
        tracked = self.tracker.update(native_boxes.numpy(), frame.frame)
        identities = {int(track[-1]): int(track[4]) for track in tracked}
        detections = []
        for index, values in enumerate(boxes):
            x1, y1, x2, y2, confidence, class_id = (float(v) for v in values)
            raw_id = identities.get(index)
            identity = None
            if raw_id is not None:
                if raw_id not in self._identities:
                    if len(self._identities) >= self.settings.analytics.max_session_tracks:
                        raise ValueError("VIDEO_TRACK_LIMIT")
                    self._identities[raw_id] = self._next_identity
                    self._next_identity += 1
                identity = self._identities[raw_id]
            kind = self.manifest.domain_mapping[self.manifest.class_id_mapping[int(class_id)]]
            if kind is None:
                continue
            detections.append(
                Detection(
                    kind=kind,
                    confidence=confidence,
                    timestamp=frame.timestamp,
                    bbox=BoundingBox(x1=x1, y1=y1, x2=x2, y2=y2),
                    track_id=identity,
                )
            )
        return tuple(detections)

    def close(self) -> None:
        self.tracker = None
        super().close()

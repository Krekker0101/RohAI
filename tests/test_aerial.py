from types import SimpleNamespace
from typing import Any

import numpy as np
from numpy.typing import NDArray
from traffic_vision.aerial import AerialDetector, deduplicate, positions
from traffic_vision.manifest import ModelManifest
from traffic_vision.settings import AerialSettings, VisionSettings
from traffic_vision.sources import VideoFrame


def test_slices_cover_edges_without_duplicate_end_tiles() -> None:
    for length in (100, 1024, 1080, 2160, 3840):
        origins = positions(length, 1024, 0.2)
        assert origins == sorted(set(origins)) and origins[0] == 0
        assert origins[-1] + 1024 >= length
        assert all(b - a <= 1024 for a, b in zip(origins, origins[1:], strict=False))


def test_full_frame_merge_removes_tile_and_class_duplicates_not_nearby_cars() -> None:
    boxes = np.asarray(
        [
            [0, 0, 20, 20, 0.7, 2],
            [1, 1, 21, 21, 0.8, 7],
            [22, 0, 42, 20, 0.6, 2],
        ],
        dtype=np.float32,
    )
    merged = deduplicate(boxes)
    assert len(merged) == 2 and merged[0, 5] == 7 and merged[1, 0] == 22
    assert deduplicate(np.empty((0, 6), dtype=np.float32)).shape == (0, 6)


class NativeData:
    def __init__(self, rows: list[list[float]]) -> None:
        self.rows = np.asarray(rows, dtype=np.float32).reshape(-1, 6)

    def cpu(self) -> "NativeData":
        return self

    def numpy(self) -> NDArray[np.float32]:
        return self.rows


class FixtureModel:
    def __init__(self, large: bool = False) -> None:
        self.large = large

    def predict(self, crops: list[Any], **kwargs: Any) -> list[Any]:
        results = []
        for image in crops:
            if self.large:
                rows = [[0, 0, 90, 60, 0.95, 2]]
            elif image.shape[:2] == (80, 100):
                rows = [[10, 20, 30, 40, 0.8, 2]]
            else:
                rows = [[20, 70, 40, 90, 0.75, 2]]
            results.append(SimpleNamespace(boxes=SimpleNamespace(data=NativeData(rows))))
        return results


class FixtureTracker:
    def __init__(self) -> None:
        self.calls = 0
        self.inputs: Any = None

    def reset(self) -> None:
        pass

    def update(self, boxes: Any, image: Any) -> NDArray[np.float32]:
        self.calls += 1
        self.inputs = boxes.data
        return np.asarray([[10, 20, 30, 40, 42, 0.8, 2, 0]], dtype=np.float32)


def test_rotated_coordinates_merge_before_one_global_tracking_update() -> None:
    from pathlib import Path

    detector = AerialDetector(
        VisionSettings(), AerialSettings(max_object_fraction=0.3, rotated_pass=True)
    )
    detector.manifest = ModelManifest.load(Path("configs/models/yolo26n.json"))
    tracker = FixtureTracker()
    detector.tracker, detector._model = tracker, FixtureModel()
    frame = VideoFrame(np.zeros((80, 100, 3), dtype=np.uint8), 0, 0, "fixture", 0)
    detections = detector.process(frame)
    assert tracker.calls == 1 and len(tracker.inputs) == 1
    assert len(detections) == 1 and detections[0].track_id == 1
    assert detections[0].bbox.x1 == 10 and detections[0].bbox.y2 == 40
    assert detector.tiles_per_frame == 2


def test_oversized_house_candidate_is_filtered_even_with_high_confidence() -> None:
    from pathlib import Path

    detector = AerialDetector(VisionSettings(), AerialSettings(rotated_pass=False))
    detector.manifest = ModelManifest.load(Path("configs/models/yolo26n.json"))
    tracker = FixtureTracker()
    detector.tracker, detector._model = tracker, FixtureModel(large=True)
    frame = VideoFrame(np.zeros((80, 100, 3), dtype=np.uint8), 0, 0, "fixture", 0)
    assert not detector.process(frame)
    assert detector.filtered_large == 1 and len(tracker.inputs) == 0

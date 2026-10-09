"""Measured software behavior on controlled fixtures, not physical acceptance."""

import hashlib
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from smart_traffic_backend.maket.assisted import project_geometry, suggest_geometry
from smart_traffic_backend.maket.calibration import camera_signature, save_calibration
from smart_traffic_backend.maket.config import MaketCalibration, MaketConfig
from smart_traffic_backend.maket.vision_state import estimate
from test_maket_mode import observed_car
from traffic_vision.analytics import TrafficAnalyzer
from traffic_vision.detector import CalibrationDetector
from traffic_vision.geometry import IntersectionGeometry, load_geometry
from traffic_vision.models import PipelineStatus
from traffic_vision.pipeline import VisionPipeline
from traffic_vision.settings import AnalyticsSettings, VisionSettings
from traffic_vision.sources import VideoFrame, VideoSource


def config(tmp_path: Path) -> MaketConfig:
    original = MaketConfig.load(Path("configs/maket/maket.json"))
    return original.model_copy(
        update={
            "calibration_path": tmp_path / "calibration.json",
            "reference_image_path": tmp_path / "reference.png",
        }
    )


def calibration(configuration: MaketConfig, reference: bytes) -> MaketCalibration:
    return MaketCalibration(
        intersection_id="software-fixture",
        version=1,
        created_at=datetime.now(UTC),
        orientation_degrees=0,
        camera_pose_verified=True,
        camera_signature=camera_signature(configuration.camera),
        geometry=load_geometry(Path("configs/maket/geometry-draft.json")),
        reference_image_sha256=hashlib.sha256(reference).hexdigest(),
    )


def test_atomic_calibration_roundtrip_and_rejection(tmp_path: Path) -> None:
    c = config(tmp_path)
    reference = b"controlled-reference-fixture"
    saved = calibration(c, reference)
    save_calibration(c, saved, reference)
    assert MaketCalibration.model_validate_json(c.calibration_path.read_text()) == saved
    assert c.reference_image_path.read_bytes() == reference
    for bad in (
        saved.model_copy(update={"camera_pose_verified": False}),
        saved.model_copy(update={"camera_signature": "0" * 64}),
    ):
        with pytest.raises(ValueError):
            save_calibration(c, bad, reference)
    with pytest.raises(ValueError, match="hash"):
        save_calibration(c, saved, b"corrupted")
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(
    "field,value",
    [
        ("webcam_index", 4),
        ("camera_rotation", 90),
        ("camera_mirror", True),
        ("camera_crop", (0.1, 0.1, 0.8, 0.8)),
        ("camera_width", 640),
        ("source_id", "different"),
    ],
)
def test_camera_binding_changes(field: str, value: object) -> None:
    camera = VisionSettings()
    assert camera_signature(camera) != camera_signature(camera.model_copy(update={field: value}))


def test_fps_does_not_change_geometry_binding_and_custom_settings_survive(tmp_path: Path) -> None:
    c = config(tmp_path)
    modified = c.camera.model_copy(update={"camera_fps": 15, "camera_width": 800})
    assert camera_signature(c.camera) == camera_signature(
        c.camera.model_copy(update={"camera_fps": 15})
    )
    result = c.model_copy(update={"performance_preset": "CUSTOM", "camera": modified}).with_preset()
    assert result.camera.camera_fps == 15 and result.camera.camera_width == 800


def test_perspective_seed_scaling_and_orientation() -> None:
    corners = [
        {"x": 0.2, "y": 0.1},
        {"x": 0.9, "y": 0.2},
        {"x": 0.8, "y": 0.9},
        {"x": 0.1, "y": 0.8},
    ]
    first = project_geometry(np.zeros((480, 640, 3), np.uint8), "live", corners)
    second = project_geometry(np.zeros((720, 1280, 3), np.uint8), "live", corners)
    assert first["verified"] is False and first["confidence"] is None
    assert first["geometry"]["detection_zone"] == second["geometry"]["detection_zone"]
    assert first["geometry"]["detection_zone"]["points"][0]["x"] == pytest.approx(0.2)
    assert second["geometry"]["reference_width"] == 1280
    IntersectionGeometry.model_validate(first["geometry"])


@pytest.mark.parametrize(
    "corners",
    [
        [{"x": 0.1, "y": 0.1}] * 4,
        [{"x": 0, "y": 0}, {"x": 1, "y": 1}, {"x": 1, "y": 0}, {"x": 0, "y": 1}],
        [{"x": -1, "y": 0}, {"x": 1, "y": 0}, {"x": 1, "y": 1}, {"x": 0, "y": 1}],
        [],
    ],
)
def test_invalid_perspective_is_rejected(corners: list[dict[str, float]]) -> None:
    with pytest.raises(ValueError):
        project_geometry(np.zeros((480, 640, 3), np.uint8), "live", corners)


def test_image_edges_are_only_hypotheses_and_blank_frame_has_no_road() -> None:
    frame = np.zeros((480, 640, 3), np.uint8)
    assert suggest_geometry(frame, "live")["available"] is False
    for x in (230, 410):
        cv2.line(frame, (x, 0), (x, 479), (255, 255, 255), 2)
    for y in (175, 305):
        cv2.line(frame, (0, y), (639, y), (255, 255, 255), 2)
    candidate = suggest_geometry(frame, "live")
    assert candidate["available"] and candidate["verified"] is False
    assert candidate["geometry"]["calibration_validated_at"] is None
    with pytest.raises(ValueError):
        suggest_geometry(np.zeros((2, 2), np.uint8), "live")


def test_motion_queue_and_frame_provenance_are_separate(tmp_path: Path) -> None:
    analyzer = TrafficAnalyzer(
        load_geometry(Path("configs/maket/geometry-draft.json")),
        AnalyticsSettings(coordinate_domain="maket_relative"),
    )
    for index in range(7):
        result = observed_car(analyzer, index / 10, y=80 + index * 18)
    assert result.traffic.directions["north"].queue_count == 0
    c = calibration(config(tmp_path), b"reference")
    status = PipelineStatus(
        state="running",
        captured=7,
        inferred=7,
        analyzed=7,
        published=7,
        dropped_capture=0,
        dropped_inference=0,
        dropped_analysis=0,
        processing_fps=10,
        latency_ms=0,
        error=None,
    )
    for confirmed, age in ((True, 0.1), (False, 0.1), (True, 10)):
        state = estimate(
            result,
            captured_at=100,
            now=100 + age,
            width=1280,
            height=720,
            calibration=c,
            confirmed=confirmed,
            status=status,
            slow_speed=12,
            max_age=1.5,
        )
        north = next(d for d in state.directions if d.direction_id == "north")
        assert (
            state.frame_id == result.traffic.frame_id
            and state.provenance == "FRAME_DERIVED_ESTIMATE"
        )
        if confirmed and age < 1.5:
            assert north.moving_count == 1 and north.stopped_count == 0 and north.queue_count == 0
        else:
            assert north.queue_count is None and north.confidence == 0


class RecoveringSource(VideoSource):
    def __init__(self, *, permanent: bool = False) -> None:
        super().__init__("recovery-fixture")
        self.stream_epoch = -1
        self.opens = 0
        self.closes = 0
        self.index = 0
        self.permanent = permanent

    def open(self) -> None:
        assert not self.connected
        self.opens += 1
        self.stream_epoch += 1
        self.connected = True

    def read(self) -> VideoFrame | None:
        if self.permanent or self.opens == 1:
            self.connected = False
            raise OSError("Injected stream interruption")
        if self.index >= 5:
            return None
        self.index += 1
        time.sleep(0.02)
        return VideoFrame(
            np.zeros((120, 160, 3), np.uint8),
            self.index / 20,
            self.index,
            self.source_id,
            time.monotonic(),
            self.stream_epoch,
        )

    def close(self) -> None:
        self.connected = False
        self.closes += 1


def test_pipeline_recovers_one_reader_and_resets_epoch() -> None:
    source = RecoveringSource()
    settings = VisionSettings(
        source="builtin_camera", camera_backend="ANY", reconnect_delay_seconds=0.1
    )
    pipeline = VisionPipeline(
        source,
        CalibrationDetector(),
        TrafficAnalyzer(
            load_geometry(Path("configs/cameras/demo.json")).model_copy(
                update={
                    "source_id": source.source_id,
                    "reference_width": 160,
                    "reference_height": 120,
                }
            ),
            AnalyticsSettings(),
        ),
        settings,
    )
    pipeline.start()
    deadline = time.monotonic() + 3
    while pipeline.status().state not in {"ended", "failed"} and time.monotonic() < deadline:
        time.sleep(0.01)
    pipeline.stop()
    assert source.opens == 2 and source.closes >= 2
    assert pipeline.performance()["camera_recoveries"] == 1
    assert pipeline.latest_analysis and pipeline.latest_analysis.video.stream_epoch == 1


def test_permanently_broken_camera_has_bounded_recovery() -> None:
    source = RecoveringSource(permanent=True)
    settings = VisionSettings(
        source="builtin_camera",
        camera_backend="ANY",
        reconnect_attempts=2,
        reconnect_delay_seconds=0.1,
    )
    pipeline = VisionPipeline(
        source,
        CalibrationDetector(),
        TrafficAnalyzer(load_geometry(Path("configs/cameras/demo.json")), AnalyticsSettings()),
        settings,
    )
    pipeline.start()
    try:
        deadline = time.monotonic() + 3
        while pipeline.status().state != "failed" and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pipeline.status().state == "failed"
        assert source.opens == 3
        assert pipeline.latest_capture is None and pipeline.observation() is None
    finally:
        pipeline.stop()


def waiting_worker(
    settings: str, name: str, header: Any, lock: Any, ready: Any, stop: Any, errors: Any
) -> None:
    stop.wait(10)


def test_cancelled_camera_start_releases_process_and_memory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from threading import Timer

    from traffic_vision import camera

    monkeypatch.setattr(camera, "_capture_worker", waiting_worker)
    source = camera.LocalCameraSource(VisionSettings(source="builtin_camera", camera_backend="ANY"))
    timer = Timer(0.05, source.cancel)
    timer.start()
    started = time.monotonic()
    try:
        with pytest.raises(OSError, match="INTERRUPTED"):
            source.open()
        assert time.monotonic() - started < 2
        assert source._memory is None and source._process is None
    finally:
        timer.join()
        source.close()


def test_local_capture_reopen_releases_memory_and_changes_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from test_laptop_camera import stream_fixture
    from traffic_vision import camera

    monkeypatch.setattr(camera, "_capture_worker", stream_fixture)
    source = camera.LocalCameraSource(VisionSettings(source="builtin_camera", camera_backend="ANY"))
    epochs = []
    for _ in range(2):
        source.open()
        try:
            frame = source.read()
            assert frame is not None
            epochs.append(frame.stream_epoch)
        finally:
            source.close()
        assert source._memory is None and source._process is None
    assert epochs == [0, 1]


class DamagedFrameSource(VideoSource):
    def __init__(self) -> None:
        super().__init__("demo")
        self.index = 0

    def open(self) -> None:
        self.connected = True

    def read(self) -> VideoFrame | None:
        if self.index >= 3:
            return None
        images = [
            np.zeros((0, 0, 3), np.uint8),
            np.zeros((10, 10), np.uint8),
            np.zeros((720, 1280, 3), np.uint8),
        ]
        image = images[self.index]
        self.index += 1
        return VideoFrame(image, self.index / 30, self.index, self.source_id, time.monotonic())

    def close(self) -> None:
        self.connected = False


@pytest.mark.parametrize("slow", [False, True])
def test_corrupt_and_late_inference_never_become_current(slow: bool) -> None:
    class Detector(CalibrationDetector):
        def process(self, frame: VideoFrame) -> tuple[()]:
            if slow:
                time.sleep(0.15)
            return ()

    pipeline = VisionPipeline(
        DamagedFrameSource(),
        Detector(),
        TrafficAnalyzer(load_geometry(Path("configs/cameras/demo.json")), AnalyticsSettings()),
        VisionSettings(max_frame_age_seconds=0.1),
    )
    pipeline.start()
    try:
        deadline = time.monotonic() + 3
        while pipeline.status().state not in {"ended", "failed"} and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pipeline.performance()["corrupt_frames"] == 2
        if slow:
            assert pipeline.latest_analysis is None and pipeline.observation() is None
            drops = pipeline.performance()["stale_frames"]
            assert isinstance(drops, int) and drops >= 1
        else:
            assert pipeline.latest_analysis and pipeline.latest_analysis.video.frame_id == 3
    finally:
        pipeline.stop()

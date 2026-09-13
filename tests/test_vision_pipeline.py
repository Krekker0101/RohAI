# ruff: noqa: E402 -- optional native dependencies must be checked before pipeline imports.
import asyncio
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

cv2 = pytest.importorskip("cv2", reason="Install the vision extra")

from fastapi.testclient import TestClient
from smart_traffic_backend.config import Settings
from smart_traffic_backend.hardware import MockHardwareController
from smart_traffic_backend.main import create_app
from smart_traffic_backend.vision_runtime import VisionRuntime, VisionTelemetry
from traffic_core.models import SignalStage
from traffic_vision.analytics import TrafficAnalyzer
from traffic_vision.detector import CalibrationDetector, SyntheticDetector, YOLODetector
from traffic_vision.geometry import load_geometry
from traffic_vision.models import Detection
from traffic_vision.pipeline import LatestSlot, VisionPipeline
from traffic_vision.settings import AnalyticsSettings, VisionSettings
from traffic_vision.sources import SyntheticSource, VideoFileSource, VideoFrame


def wait_until(predicate: Callable[[], bool], seconds: float = 5) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("Condition did not become true before deadline")
        time.sleep(0.01)


def test_latest_slot_drops_old_work_and_drains_before_eof() -> None:
    slot = LatestSlot[int]()
    for value in range(100):
        slot.put(value)
    assert slot.dropped == 99
    slot.close()
    assert slot.take() == 99
    with pytest.raises(EOFError):
        slot.take()
    with pytest.raises(EOFError):
        slot.put(100)


def test_synthetic_and_recorded_source_metadata_and_eof(tmp_path: Path) -> None:
    source = SyntheticSource(frames=5, fps=10)
    path = tmp_path / "fixture.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 10, (1280, 720))
    assert writer.isOpened()
    source.open()
    try:
        for index in range(5):
            frame = source.read()
            assert frame is not None and frame.frame_id == index
            assert frame.timestamp == index / 10
            assert frame.source_id == "demo"
            writer.write(frame.frame)
        assert source.read() is None
    finally:
        writer.release()
        source.close()
    recorded = VideoFileSource(path, "recording")
    recorded.open()
    try:
        for index in range(5):
            frame = recorded.read()
            assert frame is not None and frame.frame_id == index
            assert frame.timestamp == pytest.approx(index / 10)
            assert frame.source_id == "recording"
        assert recorded.read() is None
    finally:
        recorded.close()


class SlowDetector(SyntheticDetector):
    def process(self, frame: VideoFrame) -> tuple[Detection, ...]:
        time.sleep(0.04)
        return super().process(frame)


class BrokenDetector(CalibrationDetector):
    def open(self) -> None:
        raise RuntimeError("Injected model failure")


def make_pipeline(
    source: SyntheticSource, detector: CalibrationDetector, settings: VisionSettings | None = None
) -> VisionPipeline:
    return VisionPipeline(
        source,
        detector,
        TrafficAnalyzer(load_geometry(Path("configs/cameras/demo.json")), AnalyticsSettings()),
        settings or VisionSettings(),
    )


def test_pipeline_overload_stays_bounded_and_drains_last_frame() -> None:
    source = SyntheticSource(frames=40)
    source.paced = False
    pipeline = make_pipeline(source, SlowDetector())
    pipeline.start()
    try:
        wait_until(lambda: pipeline.status().state in ("ended", "failed"))
        status = pipeline.status()
        assert status.state == "ended", status.error
        assert status.captured == 40 and status.dropped_capture > 0
        assert 0 < status.published <= status.inferred < status.captured
        assert pipeline.latest is not None
        assert pipeline.latest.analysis.traffic.frame_id == 39
        assert pipeline.latest.jpeg.startswith(b"\xff\xd8")
    finally:
        pipeline.stop()
    assert not any(thread.is_alive() for thread in pipeline._threads)


def test_model_failure_unblocks_capture_and_shuts_down() -> None:
    pipeline = make_pipeline(SyntheticSource(), BrokenDetector())
    pipeline.start()
    wait_until(lambda: pipeline.status().state == "failed")
    pipeline.stop()
    assert pipeline.status().captured == 0
    assert not any(thread.is_alive() for thread in pipeline._threads)


def test_vision_api_calibration_and_eof_all_red(tmp_path: Path) -> None:
    hardware = MockHardwareController()
    settings = Settings(
        mode="vision",
        tick_seconds=0.05,
        vision=VisionSettings(
            synthetic_frames=18, synthetic_fps=15, calibration_directory=tmp_path
        ),
    )
    app = create_app(settings, hardware)
    with TestClient(app) as client:
        wait_until(lambda: client.get("/health/ready").status_code == 200)
        assert client.get("/api/v1/vision/frame.jpg").content.startswith(b"\xff\xd8")
        assert client.get("/api/v1/system").json()["mode"] == "vision"
        assert client.put("/api/v1/control/policy", json={"policy": "fixed"}).status_code == 200
        assert client.get("/api/v1/system").json()["policy"] == "fixed"
        state = client.get("/api/v1/vision/state").json()
        assert set(state["directions"]) == {"north", "south", "east", "west"}
        with client.websocket_connect("/ws/telemetry") as socket:
            sample = VisionTelemetry.model_validate(socket.receive_json())
            assert sample.traffic is not None
        geometry = load_geometry(Path("configs/cameras/demo.json"))
        assert (
            client.put(
                "/api/v1/vision/geometry/demo", json=geometry.model_dump(mode="json")
            ).status_code
            == 409
        )
        wait_until(lambda: client.get("/health/ready").status_code == 503)
        wait_until(lambda: app.state.runtime.latest.failure is not None)
        assert client.get("/health/live").status_code == 200
        assert client.get("/api/v1/vision/frame.jpg").status_code == 503
        assert hardware.last_signal.stage == SignalStage.ALL_RED
    assert hardware.closed


def test_calibration_saves_geometry_and_never_enables_green(tmp_path: Path) -> None:
    hardware = MockHardwareController()
    app = create_app(
        Settings(
            mode="calibration",
            tick_seconds=0.05,
            vision=VisionSettings(calibration_directory=tmp_path),
        ),
        hardware,
    )
    with TestClient(app) as client:
        wait_until(lambda: client.get("/health/ready").status_code == 200)
        geometry = load_geometry(Path("configs/cameras/demo.json"))
        response = client.put("/api/v1/vision/geometry/demo", json=geometry.model_dump(mode="json"))
        assert response.status_code == 200
        assert client.get("/api/v1/vision/geometry/demo").json() == geometry.model_dump(mode="json")
        assert client.post("/api/v1/emergency", json={"direction": "north"}).status_code == 409
        assert client.get("/api/v1/system").json()["mode"] == "calibration"
        assert client.put("/api/v1/control/policy", json={"policy": "fixed"}).status_code == 409
        assert hardware.last_signal.stage == SignalStage.ALL_RED
    assert hardware.closed


def test_startup_failure_is_reported_and_hardware_closes() -> None:
    async def exercise() -> None:
        hardware = MockHardwareController()
        runtime = VisionRuntime(
            Settings(mode="vision", tick_seconds=0.05),
            hardware,
            make_pipeline(SyntheticSource(), BrokenDetector()),
        )
        await runtime.start()
        assert runtime.task is not None
        await asyncio.wait_for(runtime.task, 5)
        assert runtime.failure == "vision_failed"
        assert not runtime.ready
        assert hardware.last_signal.stage == SignalStage.ALL_RED
        await runtime.stop()
        assert hardware.closed

    asyncio.run(exercise())


class TensorFixture:
    def __init__(self, values: list[Any]) -> None:
        self.values = values

    def cpu(self) -> "TensorFixture":
        return self

    def numpy(self) -> list[Any]:
        return self.values


def test_yolo_adapter_uses_persistent_tracking_and_maps_reset_ids() -> None:
    resets: list[bool] = []
    calls: list[dict[str, Any]] = []
    boxes = SimpleNamespace(
        xyxy=TensorFixture([[10, 20, 110, 120]]),
        conf=TensorFixture([0.85]),
        cls=TensorFixture([2]),
        id=TensorFixture([7]),
        is_track=True,
    )

    def track(**kwargs: Any) -> list[SimpleNamespace]:
        calls.append(kwargs)
        return [SimpleNamespace(boxes=boxes)]

    detector = YOLODetector(VisionSettings())
    detector._model = SimpleNamespace(
        predictor=SimpleNamespace(trackers=[SimpleNamespace(reset=lambda: resets.append(True))]),
        track=track,
    )
    source = SyntheticSource(frames=3)
    source.open()
    first = source.read()
    second = source.read()
    assert first is not None and second is not None
    detected = detector.process(first)[0]
    assert detected.kind.value == "car" and detected.confidence == 0.85
    assert detected.centroid.x == 60 and detected.centroid.y == 70
    assert detected.track_id == detector.process(second)[0].track_id
    reconnected = VideoFrame(second.frame, 4, 2, "demo", time.monotonic(), stream_epoch=1)
    assert detector.process(reconnected)[0].track_id != detected.track_id
    assert len(resets) == 2
    assert all(call["persist"] is True for call in calls)
    assert calls[0]["classes"] == [0, 1, 2, 3, 5, 7]
    assert Path(calls[0]["tracker"]).name == "bytetrack.yaml"
    detector.close()
    source.close()


class StallingSource(SyntheticSource):
    def read(self) -> VideoFrame | None:
        if self._index >= 2:
            self.cancelled.wait(5)
            return None
        return super().read()


def test_stale_observation_cancels_blocked_capture_and_latches_all_red() -> None:
    async def exercise() -> None:
        hardware = MockHardwareController()
        settings = Settings(
            mode="vision", tick_seconds=0.05, vision=VisionSettings(stale_seconds=0.5)
        )
        pipeline = make_pipeline(StallingSource(), SyntheticDetector(), settings.vision)
        runtime = VisionRuntime(settings, hardware, pipeline)
        await runtime.start()
        assert runtime.task is not None
        await asyncio.wait_for(runtime.task, 3)
        assert runtime.failure == "vision_stale"
        assert hardware.last_signal.stage == SignalStage.ALL_RED
        assert not any(thread.is_alive() for thread in pipeline._threads)
        await runtime.stop()
        assert hardware.closed

    asyncio.run(exercise())

"""Regression/invariant suite for UNKNOWN != EMPTY, with explicit evidence fixtures."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import numpy as np
import pytest
from pydantic import ValidationError
from traffic_core.models import (
    ApproachState,
    Direction,
    ObjectKind,
    ObservationEvidence,
    ObservationStatus,
    Phase,
    Policy,
    SignalStage,
)
from traffic_core.observed_controller import ObservedTrafficController
from traffic_core.settings import Timing
from traffic_vision.analytics import TrafficAnalyzer
from traffic_vision.contracts import BoundingBox
from traffic_vision.detector import SyntheticDetector, YOLODetector
from traffic_vision.geometry import IntersectionGeometry, Polygon, load_geometry
from traffic_vision.health import PerceptionHealthMonitor
from traffic_vision.manifest import ModelManifest
from traffic_vision.models import (
    Detection,
    DirectionMetrics,
    PedestrianState,
    Point,
    TrackVisibility,
)
from traffic_vision.pipeline import VisionPipeline
from traffic_vision.settings import AnalyticsSettings, PerceptionHealthSettings, VisionSettings
from traffic_vision.sources import Image, RTSPSource, SyntheticSource, VideoFrame


def rectangle(x1: float, y1: float, x2: float, y2: float) -> Polygon:
    return Polygon(
        points=(Point(x=x1, y=y1), Point(x=x2, y=y1), Point(x=x2, y=y2), Point(x=x1, y=y2))
    )


@pytest.fixture
def geometry() -> IntersectionGeometry:
    original = load_geometry(Path("configs/cameras/demo.json"))
    waiting = [
        rectangle(0.36, 0.30, 0.50, 0.34),
        rectangle(0.51, 0.68, 0.64, 0.72),
        rectangle(0.70, 0.36, 0.75, 0.49),
        rectangle(0.25, 0.51, 0.30, 0.64),
    ]
    exits = [
        rectangle(0.36, 0.41, 0.50, 0.45),
        rectangle(0.51, 0.55, 0.64, 0.59),
        rectangle(0.55, 0.36, 0.59, 0.49),
        rectangle(0.41, 0.51, 0.45, 0.64),
    ]
    approaches = tuple(
        a.model_copy(
            update={
                "waiting_zone": waiting[i],
                "crosswalk_zone": a.pedestrian_crossing,
                "exit_zone": exits[i],
            }
        )
        for i, a in enumerate(original.approaches)
    )
    return original.model_copy(
        update={
            "approaches": approaches,
            "calibration_validated_at": datetime.now(UTC) - timedelta(seconds=1),
        }
    )


def detection(
    t: float,
    identity: int = 1,
    kind: ObjectKind = ObjectKind.CAR,
    y: float = 0.20,
    x: float = 0.43,
    confidence: float = 0.9,
) -> Detection:
    return Detection(
        kind=kind,
        confidence=confidence,
        timestamp=t,
        track_id=identity,
        bbox=BoundingBox(x1=x * 1280 - 20, y1=y * 720 - 40, x2=x * 1280 + 20, y2=y * 720),
    )


def feed(
    analyzer: TrafficAnalyzer,
    t: float,
    objects: tuple[Detection, ...],
    *,
    epoch: int = 0,
    evidence: dict[str, ObservationEvidence] | None = None,
) -> DirectionMetrics:
    health = (
        evidence
        if evidence is not None
        else {d.value: ObservationEvidence.simulated(t) for d in Direction}
    )
    return analyzer.process(
        objects,
        source_id="demo",
        timestamp=t,
        frame_id=round(t * 10000),
        width=1280,
        height=720,
        stream_epoch=epoch,
        health=health,
    ).traffic.directions["north"]


def prepared(geometry: IntersectionGeometry) -> TrafficAnalyzer:
    a = TrafficAnalyzer(geometry, AnalyticsSettings())
    for t in (0.0, 0.1, 0.2, 0.3, 0.4):
        feed(a, t, (detection(t),))
    return a


@pytest.mark.parametrize("status", list(ObservationStatus))
@pytest.mark.parametrize("value", [0, 1, 12])
def test_invalid_measurement_never_becomes_valid_zero(
    status: ObservationStatus, value: int
) -> None:
    evidence = ObservationEvidence(observation_status=status)
    metric = DirectionMetrics(observation=evidence, queue_count=value, average_wait_seconds=0)
    core = ApproachState(direction=Direction.NORTH, observation=evidence, queue_length=value)
    assert metric.queue_count is None and metric.average_wait_seconds is None
    assert core.queue_length is None
    assert json.loads(metric.model_dump_json())["queue_count"] is None


def test_empty_observed_vs_unobserved_direction(geometry: IntersectionGeometry) -> None:
    a = TrafficAnalyzer(geometry, AnalyticsSettings())
    assert feed(a, 0, ()).queue_count == 0
    assert feed(a, 1, (), evidence={}).queue_count is None
    disabled = geometry.approaches[0].model_copy(update={"enabled": False})
    g = geometry.model_copy(update={"approaches": (disabled, *geometry.approaches[1:])})
    m = feed(TrafficAnalyzer(g, AnalyticsSettings()), 0, ())
    assert m.observation.observation_status == ObservationStatus.UNOBSERVED
    assert m.queue_count is None


@pytest.mark.parametrize("times", [(0.5,), (0.5, 0.7, 1.0)])
def test_missed_detections_preserve_bounded_estimate(
    geometry: IntersectionGeometry, times: tuple[float, ...]
) -> None:
    a = prepared(geometry)
    previous = 1.0
    for t in times:
        m = feed(a, t, ())
        tr = a.tracks[1]
        assert not tr.visible
        assert tr.state in (TrackVisibility.TEMPORARILY_OCCLUDED, TrackVisibility.PREDICTED)
        assert m.estimated_queue_count == 1 and m.queue_count is None
        assert tr.current_wait == pytest.approx(t)
        assert tr.tracking_confidence < previous
        previous = tr.tracking_confidence
    recovery = times[-1] + 0.1
    m = feed(a, recovery, (detection(recovery),))
    assert m.queue_count == 1
    assert a.tracks[1].current_wait == pytest.approx(recovery)
    assert a.vehicle_arrivals == 0


def test_occlusion_expires_without_unbounded_phantom(geometry: IntersectionGeometry) -> None:
    a = prepared(geometry)
    m = feed(a, 2, ())
    assert a.tracks[1].state == TrackVisibility.LOST
    assert not a.tracks[1].queued and m.queue_count is None
    m = feed(a, 4, ())
    assert not a.tracks and m.queue_count is None
    m = feed(a, 10, ())
    assert m.queue_count == 0  # Fresh reliable observations after configured uncertainty window.


def test_id_recovery_does_not_create_arrival(geometry: IntersectionGeometry) -> None:
    a = prepared(geometry)
    feed(a, 0.5, ())
    m = feed(a, 0.6, (detection(0.6, identity=77),))
    assert a.vehicle_arrivals == 0 and m.queue_count == 1
    assert set(a.tracks) == {1}
    assert a.tracks[1].current_wait == pytest.approx(0.6)
    feed(a, 0.7, (detection(0.7, identity=77),))
    assert a.vehicle_arrivals == 0


def test_ambiguous_id_recovery_is_not_invented(geometry: IntersectionGeometry) -> None:
    a = prepared(geometry)
    feed(a, 0.5, (detection(0.5, 2, x=0.431), detection(0.5, 3, x=0.429)))
    assert len(a.tracks) == 3
    assert a.tracks[1].visible is False


@pytest.mark.parametrize("fps", [30, 15, 10, 5, 2, 1])
def test_stationary_vehicle_time_independent_of_fps(
    geometry: IntersectionGeometry, fps: int
) -> None:
    a = TrafficAnalyzer(geometry, AnalyticsSettings())
    for i in range(fps * 4 + 1):
        t = i / fps
        m = feed(a, t, (detection(t),))
        assert a.tracks[1].current_wait >= 0
    assert m.queue_count == 1
    assert m.max_wait_seconds == pytest.approx(4)


def test_irregular_dropped_frames_and_camera_jitter(geometry: IntersectionGeometry) -> None:
    a = TrafficAnalyzer(geometry, AnalyticsSettings())
    for i, t in enumerate([0.0, 0.03, 0.2, 0.25, 0.8, 1.8, 2.0, 3.0]):
        m = feed(a, t, (detection(t, x=0.43 + (i % 2) / 1280),))
    assert m.queue_count == 1 and m.max_wait_seconds == pytest.approx(3)
    m = feed(a, 6, (detection(6),))
    assert m.queue_count is None and m.observation.observation_status == ObservationStatus.DEGRADED


def test_pedestrian_waiting_crossing_clearing_and_separate_arrivals(
    geometry: IntersectionGeometry,
) -> None:
    a = TrafficAnalyzer(geometry, AnalyticsSettings())
    for t in (0.0, 1.0, 2.0):
        m = feed(a, t, (detection(t, kind=ObjectKind.PEDESTRIAN, y=0.32),))
    assert m.pedestrian_count_waiting == 1
    assert m.pedestrian_oldest_wait_seconds == 2
    assert m.pedestrian_mean_wait_seconds == 2
    assert m.arrival_rate == 0 and m.pedestrian_arrival_rate == 0
    assert a.vehicle_arrivals == 0 and a.pedestrian_total == 0
    assert a._latest is not None
    assert a.to_core_state(a._latest).pedestrian_oldest_wait_seconds == 2
    m = feed(a, 3, (detection(3, kind=ObjectKind.PEDESTRIAN, y=0.38),))
    assert m.pedestrian_count_waiting == 0 and m.pedestrian_crossing_count == 1
    assert a.tracks[1].pedestrian_state == PedestrianState.CROSSING
    m = feed(a, 4, (detection(4, kind=ObjectKind.PEDESTRIAN, y=0.43),))
    assert m.pedestrian_clearing_count == 1


def test_low_confidence_cannot_claim_empty(geometry: IntersectionGeometry) -> None:
    a = TrafficAnalyzer(geometry, AnalyticsSettings())
    m = feed(a, 0, (detection(0, confidence=0.1),))
    assert m.queue_count is None
    assert "unreliable_detection" in m.observation.failure_reasons


def image(seed: int = 0) -> Image:
    return np.random.default_rng(seed).integers(40, 210, (720, 1280, 3), dtype=np.uint8)


def packet(t: float, pixels: Image, epoch: int = 0) -> VideoFrame:
    return VideoFrame(pixels, t, round(t * 1000), "demo", t, epoch)


def inspect_pair(
    g: IntersectionGeometry, second: Image, *, elapsed: float = 0.1
) -> ObservationEvidence:
    h = PerceptionHealthMonitor(PerceptionHealthSettings())
    h.inspect(packet(0, image()), g, 0)
    return h.inspect(packet(elapsed, second), g, elapsed)["north"]


@pytest.mark.parametrize(
    "level,reason", [(0, "not_black"), (8, "not_underexposed"), (255, "not_overexposed")]
)
def test_bad_exposure(geometry: IntersectionGeometry, level: int, reason: str) -> None:
    evidence = inspect_pair(geometry, np.full((720, 1280, 3), level, dtype=np.uint8))
    assert not evidence.reliable and reason in evidence.failure_reasons


def test_blur_and_major_occlusion_and_roi_visibility(geometry: IntersectionGeometry) -> None:
    evidence = inspect_pair(geometry, np.full((720, 1280, 3), 120, dtype=np.uint8))
    assert evidence.camera_health["not_blurred"] is False
    assert evidence.camera_health["no_major_occlusion_suspected"] is False
    assert evidence.camera_health["roi_visible"] is True


def test_frozen_image(geometry: IntersectionGeometry) -> None:
    h = PerceptionHealthMonitor(PerceptionHealthSettings(frozen_seconds=2))
    for i in range(21):
        t = i / 10
        evidence = h.inspect(packet(t, image()), geometry, t)["north"]
    assert "image_not_frozen" in evidence.failure_reasons
    assert not evidence.reliable


def test_stale_and_inference_analytics_liveness(geometry: IntersectionGeometry) -> None:
    h = PerceptionHealthMonitor(PerceptionHealthSettings())
    e = h.inspect(packet(0, image()), geometry, 4)["north"]
    assert e.observation_status == ObservationStatus.STALE
    e = h.inspect(packet(1, image(1)), geometry, 1, inference_alive=False)["north"]
    assert e.observation_status == ObservationStatus.FAILED
    e = h.inspect(packet(2, image(2)), geometry, 2, analytics_alive=False)["north"]
    assert e.observation_status == ObservationStatus.FAILED


def test_camera_disconnect_reconnect_and_health_epoch(geometry: IntersectionGeometry) -> None:
    h = PerceptionHealthMonitor(PerceptionHealthSettings())
    e = h.inspect(packet(0, image()), geometry, 0, camera_connected=False)["north"]
    assert e.observation_status == ObservationStatus.FAILED
    e = h.inspect(packet(1, image(1), 1), geometry, 1)["north"]
    assert not e.reliable  # cadence must be re-established after reconnect
    e = h.inspect(packet(1.1, image(2), 1), geometry, 1.1)["north"]
    assert e.reliable


def test_rtsp_reconnect_increments_epoch(monkeypatch: pytest.MonkeyPatch) -> None:
    source = RTSPSource("rtsp://fixture", "demo", reconnect_delay=0.001)
    capture = SimpleNamespace(
        isOpened=lambda: True, read=lambda: (True, image()), release=lambda: None
    )
    monkeypatch.setattr(source, "_open_capture", lambda: capture)
    frame = source._on_read_failure()
    assert frame is not None and frame.stream_epoch == 1
    source.close()


@pytest.mark.parametrize("age", [None, 31 * 86400])
def test_missing_and_stale_calibration(geometry: IntersectionGeometry, age: int | None) -> None:
    stamp = None if age is None else datetime.now(UTC) - timedelta(seconds=age)
    g = geometry.model_copy(update={"calibration_validated_at": stamp})
    e = inspect_pair(g, image(1))
    assert e.calibration_status == ("missing" if age is None else "stale")
    assert not e.reliable


def test_health_empty_textured_road_is_observed(geometry: IntersectionGeometry) -> None:
    e = inspect_pair(geometry, image(1))
    assert e.reliable
    a = TrafficAnalyzer(geometry, AnalyticsSettings())
    m = feed(a, 0.1, (), evidence={d.value: e for d in Direction})
    assert m.queue_count is None  # Image health alone cannot validate road calibration.


def manifest(weights: Path) -> ModelManifest:
    return ModelManifest(
        model_id="test",
        model_version="1",
        architecture="fixture",
        weights_hash=hashlib.sha256(weights.read_bytes()).hexdigest(),
        dataset_version="fixture-1",
        class_names=("automobile", "person"),
        class_id_mapping={0: "automobile", 1: "person"},
        domain_mapping={"automobile": ObjectKind.CAR, "person": ObjectKind.PEDESTRIAN},
        input_size=640,
        training_commit="fixture",
        created_at=datetime.now(UTC),
        supported_features=("detect",),
        calibration_requirements=("validated-roi",),
    )


@pytest.mark.parametrize(
    "names", [{0: "car", 1: "person"}, {0: "automobile"}, {0: "automobile", 1: "person", 2: "bus"}]
)
def test_wrong_missing_unexpected_model_class(tmp_path: Path, names: dict[int, str]) -> None:
    weights = tmp_path / "test.pt"
    weights.write_bytes(b"fixture")
    with pytest.raises(ValueError, match="class names"):
        manifest(weights).validate_model(names, weights, 640)


def test_manifest_mapping_and_weight_integrity(tmp_path: Path) -> None:
    weights = tmp_path / "test.pt"
    weights.write_bytes(b"fixture")
    m = manifest(weights)
    m.validate_model({0: "automobile", 1: "person"}, weights, 640)
    assert m.domain_mapping[m.class_id_mapping[0]] == ObjectKind.CAR
    data = m.model_dump()
    data["domain_mapping"] = {"automobile": "car"}
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(data)
    data = m.model_dump()
    data["class_id_mapping"] = {0: "person", 1: "automobile"}
    with pytest.raises(ValidationError):
        ModelManifest.model_validate(data)
    weights.write_bytes(b"wrong")
    with pytest.raises(ValueError, match="hash"):
        m.validate_model({0: "automobile", 1: "person"}, weights, 640)


def test_missing_manifest_refuses_start(tmp_path: Path) -> None:
    weights = tmp_path / "test.pt"
    weights.write_bytes(b"fixture")
    detector = YOLODetector(
        VisionSettings(model_path=weights, manifest_path=tmp_path / "missing.json")
    )
    with pytest.raises(FileNotFoundError):
        detector.open()


@pytest.mark.parametrize(
    "status", [s for s in ObservationStatus if s != ObservationStatus.OBSERVED]
)
def test_safety_independent_fallback_latches(
    geometry: IntersectionGeometry, status: ObservationStatus
) -> None:
    a = prepared(geometry)
    assert a._latest is not None
    good = a.to_core_state(a._latest)
    controller = ObservedTrafficController(Timing(), Policy.ADAPTIVE)
    controller.advance(good, 2)
    assert controller.safety.snapshot(2).stage == SignalStage.GREEN
    evidence = {d.value: ObservationEvidence(observation_status=status) for d in Direction}
    feed(a, 0.5, (), evidence=evidence)
    assert a._latest is not None
    controller.set_emergency(Phase.EW, 30)
    controller.advance(a.to_core_state(a._latest), 3)
    assert controller.safety.faulted and controller.safety.stage == SignalStage.ALL_RED
    controller.advance(good, 10)
    assert controller.safety.stage == SignalStage.ALL_RED


def test_independent_uncertainty(geometry: IntersectionGeometry) -> None:
    a = prepared(geometry)
    feed(a, 0.5, ())
    assert a._latest is not None
    t = a._latest.tracks[0]
    assert t.uncertainty.detection_confidence == 0.9
    assert t.uncertainty.observation_confidence == 0
    assert 0 < t.uncertainty.tracking_confidence < 1
    assert t.queue_confidence != t.confidence


def test_pipeline_ages_evidence_without_new_frames(geometry: IntersectionGeometry) -> None:
    from traffic_vision.pipeline import AnalyzedFrame

    a = prepared(geometry)
    assert a._latest is not None
    p = VisionPipeline(SyntheticSource(), SyntheticDetector(), a, VisionSettings())
    p._state = "running"
    p._connected = True
    p._heartbeats = {"inference": 0, "analytics": 0}
    p._latest_analysis = AnalyzedFrame(
        VideoFrame(image(), 0, 0, "demo", 0), a._latest, a.to_core_state(a._latest)
    )
    result = p.observation(now=4)
    assert result is not None
    assert result.traffic.directions["north"].queue_count is None
    assert result.traffic.directions["north"].observation.measurement_age_ms == 4000
    assert (
        result.traffic.directions["north"].observation.observation_status
        == ObservationStatus.FAILED
    )


def test_incomplete_observed_payload_stays_unknown() -> None:
    evidence = ObservationEvidence.simulated()
    metric = DirectionMetrics(observation=evidence, queue_count=0)
    core = ApproachState(direction=Direction.NORTH, observation=evidence, queue_length=0)
    assert metric.queue_count is None and core.queue_length is None
    assert metric.observation.observation_status == ObservationStatus.DEGRADED
    assert core.observation.failure_reasons == ("measurement_incomplete",)


def test_evidence_from_other_frame_is_not_reused(geometry: IntersectionGeometry) -> None:
    e = inspect_pair(geometry, image(1))
    assert e.reliable
    a = TrafficAnalyzer(geometry, AnalyticsSettings())
    m = feed(a, 5, (), evidence={d.value: e for d in Direction})
    assert m.queue_count is None
    assert "evidence_frame_mismatch" in m.observation.failure_reasons


def test_negative_wait_rejected() -> None:
    from traffic_core.state import build_approach_state

    with pytest.raises(ValueError, match="negative"):
        build_approach_state(
            Direction.NORTH,
            [(ObjectKind.CAR, -1)],
            0,
            waiting_weight=0.05,
            moderate_queue=5,
            high_queue=15,
        )


def test_missing_calibration_file_refuses_start(tmp_path: Path) -> None:
    from smart_traffic_backend.config import Settings
    from smart_traffic_backend.hardware import MockHardwareController
    from smart_traffic_backend.vision_runtime import VisionRuntime

    with pytest.raises(FileNotFoundError):
        VisionRuntime(
            Settings(mode="vision", vision=VisionSettings(geometry_path=tmp_path / "missing.json")),
            MockHardwareController(),
        )


def test_adapter_custom_class_zero_is_car(tmp_path: Path) -> None:
    class Tensor:
        def __init__(self, values: list[object]) -> None:
            self.values = values

        def cpu(self) -> "Tensor":
            return self

        def numpy(self) -> list[object]:
            return self.values

    weights = tmp_path / "test.pt"
    weights.write_bytes(b"fixture")
    detector = YOLODetector(VisionSettings())
    detector.manifest = manifest(weights)
    boxes = SimpleNamespace(
        xyxy=Tensor([[1, 1, 20, 20]]),
        conf=Tensor([0.9]),
        cls=Tensor([0]),
        id=Tensor([1]),
        is_track=True,
    )
    detector._model = SimpleNamespace(
        predictor=SimpleNamespace(trackers=[]), track=lambda **kw: [SimpleNamespace(boxes=boxes)]
    )
    assert detector.process(packet(0, image()))[0].kind == ObjectKind.CAR
    boxes.cls = Tensor([9])
    with pytest.raises(ValueError, match="Unexpected"):
        detector.process(packet(0.1, image()))


def test_live_runtime_black_frame_latches_all_red(geometry: IntersectionGeometry) -> None:
    import asyncio

    from smart_traffic_backend.config import Settings
    from smart_traffic_backend.hardware import MockHardwareController
    from smart_traffic_backend.vision_runtime import VisionRuntime
    from traffic_vision.detector import CalibrationDetector

    class PatternSource(SyntheticSource):
        def __init__(self, frames: int, fps: float) -> None:
            super().__init__(frames=frames, fps=fps)
            self.analysis_ready = Event()

        def read(self) -> VideoFrame | None:
            if self._index >= 2 and not self.analysis_ready.wait(5):
                raise TimeoutError("Healthy analytics did not arrive before black-frame fault")
            frame = super().read()
            if frame is None:
                return None
            pixels = image(frame.frame_id) if frame.frame_id < 2 else np.zeros_like(frame.frame)
            return VideoFrame(
                pixels, frame.timestamp, frame.frame_id, frame.source_id, frame.captured_at
            )

    class StationaryDetector(CalibrationDetector):
        def process(self, frame: VideoFrame) -> tuple[Detection, ...]:
            return (detection(frame.timestamp),)

    async def exercise() -> None:
        settings = Settings(mode="vision", tick_seconds=0.05)
        source = PatternSource(frames=38, fps=10)
        pipeline = VisionPipeline(
            source,
            StationaryDetector(),
            TrafficAnalyzer(geometry, AnalyticsSettings()),
            settings.vision,
        )
        hardware = MockHardwareController()
        runtime = VisionRuntime(settings, hardware, pipeline)
        await runtime.start()
        saw_green = False
        try:
            assert runtime.task is not None
            async with asyncio.timeout(10):
                while not runtime.task.done():
                    saw_green |= hardware.last_signal.stage == SignalStage.GREEN
                    if pipeline.latest_analysis is not None:
                        source.analysis_ready.set()
                    await asyncio.sleep(0.02)
            assert not saw_green  # Legacy geometry cannot arm metric control.
            assert runtime.engine.safety.faulted
            assert hardware.last_signal.stage == SignalStage.ALL_RED
            assert not runtime.control_ready
            current = runtime._snapshot()
            assert current.traffic is not None
            assert current.traffic.directions["north"].queue_count is None
        finally:
            await runtime.stop()
        assert hardware.closed

    asyncio.run(exercise())


@pytest.mark.parametrize("fps", [30, 15, 10, 5, 2, 1])
def test_motion_speed_is_not_scaled_by_frame_count(
    geometry: IntersectionGeometry, fps: int
) -> None:
    a = TrafficAnalyzer(geometry, AnalyticsSettings())
    for i in range(2 * fps + 1):
        t = i / fps
        feed(a, t, (detection(t, x=0.40 + 30 * t / 1280),))
    assert a.tracks[1].speed == pytest.approx(30, abs=0.02)
    assert not a.tracks[1].queued


def test_roi_damage_does_not_hide_in_whole_frame_average(geometry: IntersectionGeometry) -> None:
    pixels = image(1)
    pixels[0:330, 440:650] = 0
    e = inspect_pair(geometry, pixels)
    assert not e.reliable


def test_insufficient_fps_health_reports_degradation(geometry: IntersectionGeometry) -> None:
    e = inspect_pair(geometry, image(1), elapsed=2)
    assert not e.reliable
    assert "frame_rate_adequate" in e.failure_reasons


def test_untracked_detection_is_not_an_empty_road(geometry: IntersectionGeometry) -> None:
    a = TrafficAnalyzer(geometry, AnalyticsSettings())
    m = feed(a, 0, (detection(0).model_copy(update={"track_id": None}),))
    assert m.queue_count is None
    assert m.observation.observation_status == ObservationStatus.DEGRADED


def test_observed_safety_checks_measurement_completeness(geometry: IntersectionGeometry) -> None:
    a = prepared(geometry)
    assert a._latest is not None
    broken = a._latest.traffic.directions["north"].model_copy(update={"queue_count": None})
    snapshot = a._latest.model_copy(
        update={
            "traffic": a._latest.traffic.model_copy(
                update={"directions": {**a._latest.traffic.directions, "north": broken}}
            )
        }
    )
    state = a.to_core_state(snapshot)
    assert state.approaches[0].queue_length is None
    assert not state.approaches[0].observation.reliable
    controller = ObservedTrafficController(Timing(), Policy.ADAPTIVE)
    controller.advance(state, 2)
    assert controller.safety.stage == SignalStage.ALL_RED


def test_temporal_settings_reject_unbounded_or_incoherent_intervals() -> None:
    with pytest.raises(ValidationError):
        AnalyticsSettings(occlusion_hold_seconds=0)
    with pytest.raises(ValidationError):
        AnalyticsSettings(occlusion_hold_seconds=4, lost_track_seconds=3)
    with pytest.raises(ValidationError):
        AnalyticsSettings(prediction_after_seconds=2, occlusion_hold_seconds=1)
    with pytest.raises(ValidationError):
        PerceptionHealthSettings(frozen_seconds=0)

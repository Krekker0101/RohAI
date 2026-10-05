"""New principal-audit probes, independent of historical report assertions."""

import asyncio
import time
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError
from smart_traffic_backend.device_emulator import DeviceKernel
from smart_traffic_backend.safety_architecture import PolicyCandidate, capture_hardware_state
from traffic_core.hardware_protocol import DeviceRequirements, HardwareCommand, authenticate
from traffic_core.models import Phase, SignalStage, SignalState
from traffic_core.settings import Timing
from traffic_simulator.learning.environment import LearningControlEnvironment
from traffic_simulator.scenarios import catalog
from traffic_vision.analytics import TrafficAnalyzer
from traffic_vision.geometry import load_geometry
from traffic_vision.health import PerceptionHealthMonitor
from traffic_vision.manifest import ModelManifest
from traffic_vision.settings import AnalyticsSettings, PerceptionHealthSettings
from traffic_vision.sources import VideoFrame


@pytest.mark.parametrize(
    "condition",
    [
        "nothing",
        "blocked",
        "night",
        "frozen",
        "low-fps",
        "variable-fps",
        "out-of-order",
        "disconnected",
        "stale",
    ],
)
def test_camera_diagnostics_under_adversarial_inputs(condition: str) -> None:
    geometry = load_geometry(Path("configs/cameras/demo.json"))
    monitor = PerceptionHealthMonitor(PerceptionHealthSettings())
    rng = np.random.default_rng(81377)
    image = rng.integers(40, 210, (720, 1280, 3), dtype=np.uint8)
    monitor.inspect(VideoFrame(image, 1, 0, geometry.source_id, 100), geometry, 100)
    timestamp, now, connected = 1.1, 100.1, True
    if condition in ("nothing", "blocked"):
        image = np.zeros_like(image)
    elif condition == "night":
        image = np.full_like(image, 3)
    elif condition == "frozen":
        timestamp = monitor.settings.frozen_seconds + 2
    elif condition in ("low-fps", "variable-fps"):
        timestamp = 10
    elif condition == "out-of-order":
        timestamp = 0.5
    elif condition == "disconnected":
        connected = False
    elif condition == "stale":
        now = 110
    evidence = monitor.inspect(
        VideoFrame(image, timestamp, 1, geometry.source_id, 100),
        geometry,
        now,
        camera_connected=connected,
    )
    key = {
        "nothing": "not_black",
        "blocked": "not_black",
        "night": "not_underexposed",
        "frozen": "image_not_frozen",
        "low-fps": "frame_rate_adequate",
        "variable-fps": "frame_rate_adequate",
        "out-of-order": "frame_rate_adequate",
        "disconnected": "camera_connected",
        "stale": "frame_fresh",
    }[condition]
    assert all(e.camera_health[key] is False for e in evidence.values())
    assert not any(e.reliable for e in evidence.values())


def test_analyzer_rejects_reversed_time_and_wrong_camera() -> None:
    geometry = load_geometry(Path("configs/cameras/demo.json"))
    analyzer = TrafficAnalyzer(geometry, AnalyticsSettings())

    def frame(timestamp: float, frame_id: int, source: str) -> None:
        analyzer.process(
            (), source_id=source, timestamp=timestamp, frame_id=frame_id, width=1280, height=720
        )

    frame(1, 1, geometry.source_id)
    with pytest.raises(ValueError):
        frame(0.5, 2, geometry.source_id)
    with pytest.raises(ValueError):
        frame(2, 2, "wrong-camera")


def test_manifest_rejects_class_mapping_mismatch() -> None:
    manifest = ModelManifest.load(Path("configs/models/yolo26n.json"))
    with pytest.raises(ValueError, match="class"):
        manifest.validate_model({0: "pedestrian"}, Path("datasets/models/yolo26n.pt"), 640)


@pytest.mark.parametrize("action", [float("nan"), float("inf"), -1, 9, True, "0"])
def test_rl_rejects_invalid_actions(action: object) -> None:
    env = LearningControlEnvironment(catalog(917002, 15)["balanced"])
    try:
        env.reset(seed=917002)
        with pytest.raises(ValueError):
            env.step(action)  # type: ignore[arg-type]
        assert env.world is not None and env.world.now == 0
    finally:
        env.close()


def test_nan_policy_candidate_rejected_even_when_validation_bypassed() -> None:
    with pytest.raises(ValidationError):
        PolicyCandidate.model_validate(
            PolicyCandidate.model_construct(phase=Phase.NS, green_seconds=float("nan"))
        )


def test_enter_safe_does_not_claim_a_signal_was_sent() -> None:
    from types import SimpleNamespace

    now = time.time()
    command = HardwareCommand(
        device_id="device",
        device_boot_id="boot",
        controller_instance="controller",
        sequence=1,
        kind="ENTER_SAFE",
        safe_mode_id="site-approved-program",
        issued_at=now,
        expires_at=now + 2,
    )
    signal = SignalState(phase=Phase.NS, stage=SignalStage.ALL_RED, elapsed_seconds=0)
    stages = capture_hardware_state(
        SimpleNamespace(delivery=SimpleNamespace(command_sent=command)), signal, signal
    )
    assert stages.command_sent is None
    assert stages.physically_confirmed is None


def test_device_watchdog_network_partition_and_replay() -> None:
    req = DeviceRequirements(
        device_id="audit",
        safe_mode_id="emulator-all-red",
        requirements_reference="audit-fixture-only",
        max_lease_s=8,
        ack_timeout_s=1,
        feedback_timeout_s=1,
        max_clock_skew_s=0.1,
    )
    key, utc = b"principal-audit-local-key-000000000", time.time()
    device = DeviceKernel(req, "audit", key, Timing(), 100)
    command = authenticate(
        HardwareCommand(
            device_id="audit",
            device_boot_id=device.boot_id,
            controller_instance="audit",
            sequence=1,
            kind="SIGNAL",
            requested_state=SignalState(phase=Phase.NS, stage=SignalStage.GREEN, elapsed_seconds=0),
            issued_at=utc,
            expires_at=utc + 8,
        ),
        key,
    )
    assert device.receive(command, 103, utc).accepted
    deadline = device.lease_deadline
    assert device.receive(command, 104, utc + 1).accepted
    assert device.lease_deadline == deadline
    device.tick(deadline)
    assert device.watchdog_latched
    device.tick(deadline + Timing().yellow_seconds)
    assert device.signal.stage == SignalStage.ALL_RED
    replay = authenticate(command.model_copy(update={"command_id": "new-id"}), key)
    assert not device.receive(replay, deadline + 4, utc + 2).accepted


def test_shadow_timeout_does_not_reach_mock_hardware(tmp_path: Path) -> None:
    from smart_traffic_backend.config import Settings
    from smart_traffic_backend.hardware import MockHardwareController
    from smart_traffic_backend.runtime import Runtime
    from smart_traffic_backend.safety_architecture import PolicyMode

    class SlowPolicy:
        async def propose(self, observation: object) -> PolicyCandidate:
            await asyncio.sleep(1)
            return PolicyCandidate(phase=Phase.EW, green_seconds=15)

    async def exercise() -> None:
        runtime = Runtime(
            Settings(
                tick_seconds=0.05,
                hardware_timeout_seconds=0.03,
                improvement_log_path=tmp_path / "audit.sqlite3",
            ),
            MockHardwareController(),
        )
        runtime.configure_policy(PolicyMode.SHADOW, SlowPolicy())
        await runtime.start()
        await asyncio.sleep(0.21)
        await runtime.stop()
        assert any(r.policy_error == "policy_timeout" for r in runtime.decision_journal.snapshot())
        assert runtime.health.last_control_tick_at is not None
        assert runtime.hardware_state.physically_confirmed is None

    asyncio.run(exercise())


def test_release_rejects_self_certified_gate_booleans(tmp_path: Path) -> None:
    from traffic_vision.training.improvement_release import ImprovementReleaseRegistry

    artifact, evaluation = tmp_path / "model.bin", tmp_path / "evaluation.json"
    artifact.write_bytes(b"unmeasured candidate")
    evaluation.write_text("{}")
    with pytest.raises(ValueError, match="Measured independent evaluation"):
        ImprovementReleaseRegistry(tmp_path / "registry").candidate(
            artifact=artifact,
            evaluation_report=evaluation,
            code_commit="a" * 40,
            dataset="dataset",
            training_config={"seed": 1},
            regression_gates={"passed": True},
            benchmark_id="benchmark",
        )


def test_vision_fault_uses_device_program_and_cannot_confirm_recovery(tmp_path: Path) -> None:
    from smart_traffic_backend.config import Settings
    from smart_traffic_backend.hardware import MockHardwareController
    from smart_traffic_backend.vision_runtime import VisionRuntime

    class ProgramHardware(MockHardwareController):
        safe_calls = 0

        async def enter_safe(self) -> None:
            self.safe_calls += 1

    async def exercise() -> None:
        hardware = ProgramHardware()
        runtime = VisionRuntime(
            Settings(mode="vision", improvement_log_path=tmp_path / "vision.sqlite3"), hardware
        )
        signal = runtime.engine.safety.fail_safe(0)
        await runtime._apply(signal)
        assert hardware.safe_calls == 1
        with pytest.raises(RuntimeError, match="Latched safety"):
            runtime.confirm_recovery()
        runtime.improvement_writer.close()

    asyncio.run(exercise())


def test_non_simulation_operator_api_fails_closed_without_token() -> None:
    from types import SimpleNamespace

    from fastapi import HTTPException
    from smart_traffic_backend.config import Settings
    from smart_traffic_backend.main import require_operator

    for mode in ("vision", "calibration"):
        runtime = SimpleNamespace(settings=Settings(mode=mode))
        with pytest.raises(HTTPException) as error:
            require_operator(runtime)  # type: ignore[arg-type]
        assert error.value.status_code == 503


def test_camera_move_and_calibration_identity_rejected() -> None:
    from datetime import UTC, datetime

    from traffic_vision.alignment import AlignmentEvidence, LandmarkAlignmentMonitor
    from traffic_vision.calibration import (
        CalibrationMetadata,
        CalibrationReference,
        CalibrationStatus,
        CalibrationValidator,
    )
    from traffic_vision.models import Point

    coordinates = (
        (10, 10),
        (630, 10),
        (630, 470),
        (10, 470),
        (100, 100),
        (500, 100),
        (500, 350),
        (100, 350),
    )
    points = tuple(
        CalibrationReference(
            point_id=str(i), image=Point(x=x, y=y), world=Point(x=x / 10, y=y / 10)
        )
        for i, (x, y) in enumerate(coordinates)
    )
    calibration = CalibrationValidator.fit(
        width=640,
        height=480,
        references=points[:4],
        validation=points[4:],
        metadata=CalibrationMetadata(
            camera_id="audit-camera",
            version="v1",
            timestamp=datetime.now(UTC),
            image_model="rectified",
            world_frame="audit-metric",
            validation_error_m=0,
            validation_max_error_m=0,
        ),
    )
    assert (
        CalibrationValidator.validate(
            calibration, camera_id="other-camera", width=640, height=480
        ).status
        != CalibrationStatus.VALID
    )
    monitor = LandmarkAlignmentMonitor()
    reference = tuple(p.image for p in points[:4])
    monitor.submit(
        AlignmentEvidence(
            camera_id="audit-camera",
            calibration_version="v1",
            timestamp=1,
            width=640,
            height=480,
            reference=reference,
            observed=tuple(Point(x=p.x + 30, y=p.y) for p in reference),
            source="synthetic-static-landmarks",
        )
    )
    assert monitor.check(calibration, 1, 640, 480).status == CalibrationStatus.SUSPECT


def test_blocking_policy_timeout_keeps_event_loop_and_single_worker(tmp_path: Path) -> None:
    from smart_traffic_backend.safety_architecture import PolicyWorker

    class BlockingPolicy:
        async def propose(self, observation: object) -> PolicyCandidate:
            time.sleep(0.2)  # noqa: ASYNC251 - deliberate fault injection
            return PolicyCandidate(phase=Phase.EW, green_seconds=15)

    async def exercise() -> None:
        from traffic_core.models import Policy
        from traffic_simulator.engine import SimulationEngine
        from traffic_simulator.simulator import Scenario

        traffic = SimulationEngine(Scenario(), Timing(), Policy.ADAPTIVE).snapshot().traffic
        worker = PolicyWorker()
        try:
            started = time.perf_counter()
            with pytest.raises(TimeoutError):
                await worker.propose(BlockingPolicy(), traffic, 0.02)
            assert time.perf_counter() - started < 0.15
            assert worker.busy
            with pytest.raises(RuntimeError, match="still running"):
                await worker.propose(BlockingPolicy(), traffic, 0.02)
        finally:
            worker.close()

    asyncio.run(exercise())

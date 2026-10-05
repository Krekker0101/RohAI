"""Contract/fixture tests; none are physical hardware or real-camera validation."""

import asyncio
import hashlib
import json
import time
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from smart_traffic_backend.maket.app import create_maket_app
from smart_traffic_backend.maket.config import MaketCalibration, MaketConfig, MaketScaleConfig
from smart_traffic_backend.maket.metrics import MaketEvents
from smart_traffic_backend.maket.policy import CameraPolicy, approved_stand_release
from smart_traffic_backend.maket.runtime import MaketRuntime
from smart_traffic_backend.maket.serial import MASKS, Esp32TrafficLightController, checksum
from traffic_core.models import Direction, ObjectKind, ObservationEvidence
from traffic_simulator.learning.safety import SafetySupervisor
from traffic_vision.analytics import TrafficAnalyzer
from traffic_vision.contracts import BoundingBox
from traffic_vision.geometry import IntersectionGeometry, load_geometry
from traffic_vision.manifest import ModelManifest
from traffic_vision.models import Detection
from traffic_vision.settings import AnalyticsSettings


def config() -> MaketConfig:
    c = MaketConfig.load(Path("configs/maket/maket.json"))
    return c.model_copy(
        update={
            "output_mode": "PHYSICAL_OUTPUT",
            "camera_selected": True,
            "camera": c.camera.model_copy(update={"source": "webcam"}),
        }
    )


@pytest.mark.parametrize("source", ["file", "synthetic"])
def test_maket_rejects_non_live_sources(source: str) -> None:
    data = config().model_dump(mode="json")
    data["camera"].update(source=source, uri="recordings/car-detection.mp4")
    with pytest.raises(ValueError, match="live"):
        MaketConfig.model_validate(data)


def test_maket_explicit_units_and_domain() -> None:
    with pytest.raises(ValueError, match="dimensions"):
        MaketScaleConfig(coordinate_unit="cm")
    data = config().model_dump(mode="json")
    data["camera"]["deployment_domain"] = "REAL_ROAD"
    with pytest.raises(ValueError, match="domain"):
        MaketConfig.model_validate(data)
    assert MaketScaleConfig().coordinate_unit == "relative"


def test_calibration_cannot_label_road_meters_as_maket() -> None:
    geometry = load_geometry(Path("configs/maket/geometry-draft.json")).model_dump(mode="json")
    geometry["approaches"][0]["meters_per_pixel"] = 1
    with pytest.raises(ValueError, match="Road-meter"):
        MaketCalibration(
            intersection_id="stand",
            version=1,
            created_at=datetime.now(UTC),
            orientation_degrees=0,
            geometry=IntersectionGeometry.model_validate(geometry),
            reference_image_sha256="0" * 64,
        )


def evidence(timestamp: float) -> dict[str, ObservationEvidence]:
    checks = dict.fromkeys(
        (
            "camera_connected",
            "frame_fresh",
            "frame_rate_adequate",
            "image_not_frozen",
            "not_black",
            "not_underexposed",
            "not_overexposed",
            "not_blurred",
            "no_major_occlusion_suspected",
            "roi_visible",
            "inference_alive",
            "analytics_alive",
            "calibration_available",
            "calibration_fresh",
        ),
        True,
    )
    return {
        d.value: ObservationEvidence.simulated(timestamp).model_copy(
            update={
                "source_camera_ids": ("maket-camera",),
                "camera_health": checks,
            }
        )
        for d in Direction
    }


def observed_car(
    analyzer: TrafficAnalyzer, timestamp: float, identity: int = 1, y: float = 140
) -> Any:
    detection = Detection(
        track_id=identity,
        kind=ObjectKind.CAR,
        confidence=0.98,
        bbox=BoundingBox(x1=530, y1=y - 30, x2=570, y2=y),
        timestamp=timestamp,
    )
    return analyzer.process(
        (detection,),
        source_id="maket-camera",
        timestamp=timestamp,
        frame_id=int(timestamp * 100),
        width=1280,
        height=720,
        health=evidence(timestamp),
    )


def test_relative_queue_wait_and_short_occlusion_are_camera_estimates() -> None:
    a = TrafficAnalyzer(
        load_geometry(Path("configs/maket/geometry-draft.json")),
        AnalyticsSettings(coordinate_domain="maket_relative"),
    )
    for t in (0, 0.1, 0.2, 0.3, 0.5, 1, 2):
        result = observed_car(a, t)
    metrics = result.traffic.directions["north"]
    assert metrics.observation.reliable, metrics.observation.failure_reasons
    assert metrics.queue_count == 1
    assert metrics.max_wait_seconds is not None and metrics.max_wait_seconds > 1
    assert metrics.queue_length_unit == "px"
    later = a.process(
        (),
        source_id="maket-camera",
        timestamp=2.2,
        frame_id=220,
        width=1280,
        height=720,
        health=evidence(2.2),
    )
    assert later.tracks and later.tracks[0].track_uid == result.tracks[0].track_uid
    assert later.tracks[0].current_wait_time >= result.tracks[0].current_wait_time


def test_toy_class_names_use_explicit_domain_mapping() -> None:
    data = json.loads(Path("configs/models/yolo26n.json").read_text())
    names = (
        "CAR",
        "BUS",
        "TRUCK",
        "MOTORCYCLE",
        "PEDESTRIAN_MODEL",
        "EMERGENCY_VEHICLE",
        "UNKNOWN",
    )
    kinds = ("car", "bus", "truck", "motorcycle", "pedestrian", "emergency_vehicle", "unknown")
    data.update(
        deployment_domain="PHYSICAL_MAKET",
        class_names=names,
        class_id_mapping=dict(enumerate(names)),
        domain_mapping=dict(zip(names, kinds, strict=True)),
    )
    m = ModelManifest.model_validate(data)
    assert m.domain_mapping["PEDESTRIAN_MODEL"] == ObjectKind.PEDESTRIAN
    assert m.domain_mapping["UNKNOWN"] == ObjectKind.UNKNOWN


class WireFixture:
    """Host ACK fault injection, NOT an ESP32 emulator/readiness proof."""

    def __init__(self, fault: str | None = None) -> None:
        self.fault = fault
        self.response = b""
        self.commands: list[dict[str, Any]] = []

    def write(self, data: bytes) -> int:
        p = json.loads(data)
        assert p["crc32"] == checksum(p)
        self.commands.append(p)
        phase = p["phase"]
        a = {
            "protocol": 1,
            "boot": 123,
            "session": p["session"],
            "command_id": p["command_id"],
            "sequence": p["sequence"],
            "accepted": True,
            "reason": "fixture",
            "uptime_ms": 100,
            "device_id": "rohai-maket-1",
            "domain": "PHYSICAL_MAKET",
            "watchdog_present": True,
            "actual_phase": phase,
            "output_mask": MASKS[phase],
            "sense_mask": MASKS[phase],
            "watchdog": "LATCHED" if p["kind"] == "HELLO" else "OK",
            "sense_fault": False,
            "estop": False,
            "min_green_ms": 8000,
            "max_green_ms": 45000,
            "yellow_ms": 3000,
            "all_red_ms": 2000,
            "ped_green_ms": 10000,
            "ped_clearance_ms": 5000,
        }
        if self.fault == "old_ack":
            a["command_id"] = "0" * 32
        if self.fault == "restart" and p["kind"] != "HELLO":
            a["boot"] = 124
        if self.fault == "invalid_phase":
            a["actual_phase"] = "NS_GREEN+EW_GREEN"
        if self.fault == "schema":
            a["accepted"] = "true"
        payload = json.dumps(a)
        outer = {"payload": payload, "crc32": f"{zlib.crc32(payload.encode()) & 0xFFFFFFFF:08x}"}
        if self.fault == "garbled":
            outer["crc32"] = "00000000"
        self.response = (json.dumps(outer) + "\n").encode()
        return len(data)

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        if self.fault == "missing":
            time.sleep(0.01)
            return b""
        result, self.response = self.response, b""
        return result

    def close(self) -> None:
        pass


def test_real_adapter_hello_ack_sequence_boot_binding_and_stop() -> None:
    async def run() -> None:
        port = WireFixture()
        adapter = Esp32TrafficLightController(config().hardware, port)
        await adapter.connect()
        await adapter.command("STATUS")
        await adapter.enter_safe()
        assert adapter.connected and adapter.boot == 123
        assert [p["sequence"] for p in port.commands] == [1, 2, 3]
        assert port.commands[1]["boot"] == 123 and port.commands[1]["deadline_ms"] > 0
        assert adapter.snapshot()["feedback_kind"].startswith("electrical")

    asyncio.run(run())


@pytest.mark.parametrize(
    "fault", ["missing", "old_ack", "garbled", "restart", "invalid_phase", "schema"]
)
def test_serial_failures_cannot_be_success_or_mock(fault: str) -> None:
    async def run() -> None:
        port = WireFixture()
        adapter = Esp32TrafficLightController(config().hardware, port)
        await adapter.connect()
        port.fault = fault
        with pytest.raises((TimeoutError, ValueError, RuntimeError, ValidationError)):
            await adapter.command("STATUS")
        assert not adapter.connected
        assert adapter.fault is not None

    asyncio.run(run())


def test_shadow_uses_real_camera_contract_not_truth() -> None:
    a = TrafficAnalyzer(
        load_geometry(Path("configs/maket/geometry-draft.json")),
        AnalyticsSettings(coordinate_domain="maket_relative"),
    )
    for t in (0, 0.1, 0.2, 0.3, 0.5, 1, 2):
        result = observed_car(a, t)
    policy = CameraPolicy(SafetySupervisor(config().timing), None)
    observation = a.to_core_state(result)
    decision = asyncio.run(policy.decide(observation))
    assert decision.green_seconds >= config().timing.min_green_seconds
    frame = policy.stack.frames[-1]
    assert frame.physical("north.queue_length_m") is None
    assert frame.physical("north.queue_count") == 1


def test_no_port_does_not_create_mock() -> None:
    async def run() -> None:
        c = config().hardware.model_copy(update={"port": "COM9999"})
        with pytest.raises(OSError):
            await Esp32TrafficLightController(c).connect()

    asyncio.run(run())


def test_model_centimeters_homography_never_become_road_meters() -> None:
    scale = MaketScaleConfig(
        coordinate_unit="cm",
        model_width_cm=100,
        model_height_cm=50,
        normalized_to_model_homography=((100, 0, 0), (0, 50, 0), (0, 0, 1)),
    )
    assert scale.model_point(0.25, 0.5) == (25, 25)
    with pytest.raises(ValueError, match="Singular"):
        MaketScaleConfig(
            coordinate_unit="cm",
            model_width_cm=100,
            model_height_cm=50,
            normalized_to_model_homography=((0, 0, 0), (0, 0, 0), (0, 0, 0)),
        )


def test_green_budget_rejects_no_margin_before_device_maximum() -> None:
    c = config().model_dump(mode="json")
    c["timing"].update(
        min_green_seconds=8,
        max_green_seconds=10,
        fixed_green_seconds=8,
        pedestrian_green_seconds=10,
    )
    with pytest.raises(ValueError, match="margin"):
        MaketConfig.model_validate(c)


def test_approval_requires_bound_measured_reports_not_passed_flags(tmp_path: Path) -> None:
    digest = "a" * 64
    assert not approved_stand_release(
        tmp_path, {"offline_evaluation_passed": True, "approval_id": "unit"}, digest
    )
    bindings = {}
    for stage in ("OFFLINE", "SHADOW", "STAND"):
        data = json.dumps(
            {
                "status": "MEASURED",
                "stage": stage,
                "deployment_domain": "PHYSICAL_MAKET",
                "artifact_sha256": digest,
                "independent_evaluation": True,
                "samples": 2,
                "gates": [
                    {
                        "metric": "unit_fixture_failures",
                        "measured": 0,
                        "threshold": 0,
                        "direction": "max",
                    }
                ],
            }
        )
        (tmp_path / (stage.lower() + ".json")).write_text(data)
        bindings[stage] = hashlib.sha256(data.encode()).hexdigest()
    manifest = {
        "approval": {
            "artifact_sha256": digest,
            "operator_id": "UNIT_FIXTURE",
            "approved_at": "2026-10-04",
            "evidence_sha256": bindings,
        }
    }
    assert approved_stand_release(tmp_path, manifest, digest)
    (tmp_path / "stand.json").write_text("{}")
    assert not approved_stand_release(tmp_path, manifest, digest)


def test_stop_crossing_is_not_disappearance_or_new_detector_id() -> None:
    geometry = load_geometry(Path("configs/maket/geometry-draft.json"))
    a = TrafficAnalyzer(geometry, AnalyticsSettings(coordinate_domain="maket_relative"))
    calibration = MaketCalibration(
        intersection_id="UNIT_FIXTURE",
        version=1,
        created_at=datetime.now(UTC),
        orientation_degrees=0,
        geometry=geometry,
        reference_image_sha256="0" * 64,
    )
    events = MaketEvents()
    for t in (0, 0.1, 0.2, 0.3, 0.5):
        result = observed_car(a, t, y=200)
        events.update(result, calibration)
    crossed = observed_car(a, 1, y=280)
    # Footpoint crosses normalized stop y=.35*720=252 even if approach becomes unknown.
    counted = events.update(crossed, calibration)
    assert sum(e["event"] == "stop" for e in counted["events"]) == 1
    assert not events.update(crossed, calibration)["events"]
    empty = a.process(
        (),
        source_id="maket-camera",
        timestamp=1.1,
        frame_id=110,
        width=1280,
        height=720,
        health=evidence(1.1),
    )
    assert not events.update(empty, calibration)["events"]


def test_authenticated_api_fails_closed_on_missing_camera_model_calibration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROHAI_MAKET_OPERATOR_TOKEN", "unit-fixture-operator-secret")
    c = config().model_copy(update={"session_directory": tmp_path / "sessions"})
    wire = WireFixture()

    async def start_fixture(self: MaketRuntime) -> None:
        self.hardware = Esp32TrafficLightController(c.hardware, wire)
        await self.hardware.connect()

    monkeypatch.setattr(MaketRuntime, "start", start_fixture)
    with TestClient(create_maket_app(c)) as client:
        assert client.get("/api/maket/state").status_code == 401
        client.headers["X-Operator-Token"] = "unit-fixture-operator-secret"
        snapshot = client.get("/api/maket/state").json()
        assert snapshot["traffic"] is None
        assert snapshot["physical_lamp_optical_confirmation"] is False
        response = client.post("/api/maket/control", json={"action": "start"})
        assert response.status_code == 409 and "Preflight failed" in response.text
        assert all(p["kind"] != "SIGNAL" for p in wire.commands)
        assert (
            client.post(
                "/api/maket/control", json={"action": "manual", "phase": "NS_GREEN+EW_GREEN"}
            ).status_code
            == 422
        )
        assert client.get("/maket").status_code == 200
        assert client.put("/api/maket/calibration", json={}).status_code == 409


def test_hardware_test_loop_has_operator_authority_and_core_clearances(tmp_path: Path) -> None:
    from traffic_core.models import Decision, Phase

    async def run() -> None:
        c = config().model_copy(update={"session_directory": tmp_path / "sessions"})
        runtime = MaketRuntime(c)
        port = WireFixture()
        runtime.hardware = Esp32TrafficLightController(c.hardware, port)
        await runtime.hardware.connect()
        try:
            await runtime.arm_hardware_test()
            runtime.manual = Decision(phase=Phase.NS, green_seconds=20, reason="unit_operator")
            runtime.task = asyncio.create_task(runtime._run())
            await asyncio.sleep(2.35)
            assert runtime.hardware_test and runtime.running
            signals = [p["phase"] for p in port.commands if p["kind"] == "SIGNAL"]
            assert signals[0] == "ALL_RED" and "NS_GREEN" in signals
            runtime.manual = Decision(phase=Phase.EW, green_seconds=20, reason="unit_operator")
            await asyncio.sleep(0.25)
            assert not any(p["phase"] == "EW_GREEN" for p in port.commands if p["kind"] == "SIGNAL")
            await runtime.emergency_stop("unit_stop")
            assert not runtime.running and runtime.state == "SAFE"
            assert port.commands[-1]["kind"] == "ESTOP"
        finally:
            await runtime.stop()

    asyncio.run(run())


def test_baseline_is_explicit_and_cannot_substitute_arbitrary_artifacts() -> None:
    assert config().vision_profile == "COCO_BASELINE"
    data = config().model_dump(mode="json")
    data["camera"]["model_package"] = "unverified-package"
    with pytest.raises(ValueError, match="pinned upstream"):
        MaketConfig.model_validate(data)
    data["vision_profile"] = "MAKET_MODEL"
    with pytest.raises(ValueError, match="domain"):
        MaketConfig.model_validate(data)


def test_serial_auto_requires_unique_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    from smart_traffic_backend.maket import discovery

    monkeypatch.setattr(discovery, "serial_ports", lambda: [])
    with pytest.raises(OSError, match="unique"):
        discovery.automatic_port()
    monkeypatch.setattr(
        discovery,
        "serial_ports",
        lambda: [
            {"port": "COM8", "esp32_candidate": True},
            {"port": "COM9", "esp32_candidate": True},
        ],
    )
    with pytest.raises(OSError, match="unique"):
        discovery.automatic_port()
    monkeypatch.setattr(
        discovery,
        "serial_ports",
        lambda: [
            {"port": "COM8", "esp32_candidate": True},
            {"port": "COM9", "esp32_candidate": False},
        ],
    )
    assert discovery.automatic_port() == "COM8"


def test_camera_probe_timeout_is_bounded_and_current_source_is_not_reopened(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    from smart_traffic_backend.maket import discovery

    calls = []

    def timeout(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)
    result = asyncio.run(discovery.camera_indices(current=0, indices=(0, 1)))
    assert result[0]["available"] is True
    assert result[1]["available"] is False
    assert calls and all(call["timeout"] == 4 for call in calls)


def test_absent_equipment_preserves_console_without_starting_native_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from smart_traffic_backend.maket import runtime as module

    async def no_camera(**kwargs: Any) -> list[dict[str, Any]]:
        return [{"index": 0, "available": False}]

    async def no_serial(self: Any) -> None:
        raise OSError("no equipment fixture")

    monkeypatch.setattr(module, "camera_indices", no_camera)
    monkeypatch.setattr(Esp32TrafficLightController, "connect", no_serial)
    c = config().model_copy(update={"session_directory": tmp_path / "sessions"})

    async def run() -> None:
        r = MaketRuntime(c)
        await r.start()
        await asyncio.sleep(0.05)
        assert not r.pipeline._threads
        assert r.snapshot()["camera_connection"] == "DISCONNECTED"
        assert not r.hardware.connected and r.hardware.last_command is None
        with pytest.raises(ValueError, match="Preflight"):
            await r.arm()
        await r.stop()

    asyncio.run(run())


def test_maket_arrivals_require_observed_entry_crossings() -> None:
    from smart_traffic_backend.maket.analytics import MaketAnalyzer

    g = load_geometry(Path("configs/maket/geometry-draft.json")).model_dump(mode="json")
    g["approaches"][0]["entry_line"] = {
        "start": {"x": 0.35, "y": 0.22},
        "end": {"x": 0.65, "y": 0.22},
        "upstream": {"x": 0.5, "y": 0.1},
    }
    geometry = IntersectionGeometry.model_validate(g)
    calibration = MaketCalibration(
        intersection_id="FIXTURE",
        version=1,
        created_at=datetime.now(UTC),
        orientation_degrees=0,
        geometry=geometry,
        reference_image_sha256="0" * 64,
    )
    analyzer = MaketAnalyzer(
        geometry, AnalyticsSettings(coordinate_domain="maket_relative"), calibration
    )
    first = observed_car(analyzer, 0, y=140)
    assert first.traffic.unique_vehicle_arrivals == 0
    crossed = observed_car(analyzer, 0.1, y=180)
    assert crossed.traffic.unique_vehicle_arrivals == 1
    assert observed_car(analyzer, 0.2, y=180).traffic.unique_vehicle_arrivals == 1


def test_estop_is_available_during_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx
    from smart_traffic_backend.maket import app as module

    monkeypatch.setenv("ROHAI_MAKET_OPERATOR_TOKEN", "unit-fixture-operator-secret")

    async def no_start(self: MaketRuntime) -> None:
        pass

    monkeypatch.setattr(MaketRuntime, "start", no_start)
    monkeypatch.setattr(module, "serial_ports", lambda: [])

    async def run() -> None:
        started, release = asyncio.Event(), asyncio.Event()

        async def probe(current: Any) -> list[dict[str, Any]]:
            started.set()
            await release.wait()
            return []

        monkeypatch.setattr(module, "camera_indices", probe)
        c = config().model_copy(update={"session_directory": tmp_path / "sessions"})
        app = create_maket_app(c)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://fixture",
                headers={"X-Operator-Token": "unit-fixture-operator-secret"},
            ) as client:
                task = asyncio.create_task(client.get("/api/maket/discovery"))
                await asyncio.wait_for(started.wait(), 1)
                try:
                    response = await client.post("/api/maket/control", json={"action": "estop"})
                    assert response.status_code == 200 and response.json()["running"] is False
                    assert (
                        await client.post("/api/maket/control", json={"action": "start"})
                    ).status_code == 409
                finally:
                    release.set()
                    await task

    asyncio.run(run())


def test_setup_reload_changes_camera_identity_and_stays_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROHAI_MAKET_OPERATOR_TOKEN", "unit-fixture-operator-secret")

    async def no_start(self: MaketRuntime) -> None:
        pass

    monkeypatch.setattr(MaketRuntime, "start", no_start)
    c = config().model_copy(update={"session_directory": tmp_path / "sessions"})
    with TestClient(create_maket_app(c, tmp_path / "maket.json")) as client:
        client.headers["X-Operator-Token"] = "unit-fixture-operator-secret"
        updated = c.model_dump(mode="json")
        updated["camera"]["webcam_index"] += 1
        response = client.put("/api/maket/config", json=updated)
        assert response.status_code == 200, response.text
        actual = client.get("/api/maket/config").json()
        assert actual["camera"]["source_id"] != c.camera.source_id
        assert actual["camera"]["webcam_index"] == updated["camera"]["webcam_index"]
        state = client.get("/api/maket/state").json()
        assert state["running"] is False and state["policy_mode"] == "SHADOW"

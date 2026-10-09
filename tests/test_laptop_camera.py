"""Laptop-camera contracts and process fixtures, not real-camera accuracy/HIL."""

import asyncio
import json
import subprocess
import sys
import time
from multiprocessing import shared_memory
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Literal

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from smart_traffic_backend.maket import discovery
from smart_traffic_backend.maket.app import create_maket_app
from smart_traffic_backend.maket.config import MaketConfig
from smart_traffic_backend.maket.runtime import MaketRuntime
from traffic_vision import camera
from traffic_vision.camera import LocalCameraSource, error_code, transform_frame
from traffic_vision.settings import VisionSettings
from traffic_vision.sources import create_source


def configuration(tmp_path: Path) -> MaketConfig:
    c = MaketConfig.load(Path("configs/maket/maket.json"))
    return c.model_copy(update={"session_directory": tmp_path / "sessions"})


def test_camera_interrupt_exits_cleanly_and_releases_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from queue import Queue
    from threading import Event, Lock
    from unittest.mock import MagicMock

    memory, capture = MagicMock(), MagicMock()
    capture.isOpened.return_value = True
    capture.read.side_effect = KeyboardInterrupt()
    monkeypatch.setattr(shared_memory, "SharedMemory", lambda **kwargs: memory)
    monkeypatch.setattr(cv2, "VideoCapture", lambda *args: capture)
    ready, stop = Event(), Event()
    errors: Queue[str] = Queue()
    settings = VisionSettings(camera_backend="DSHOW")
    camera._capture_worker(settings.model_dump_json(), "fixture", [], Lock(), ready, stop, errors)
    assert stop.is_set() and ready.is_set()
    assert errors.get_nowait() == "CAMERA_CAPTURE_INTERRUPTED"
    capture.release.assert_called_once()
    memory.close.assert_called_once()


def stream_fixture(
    settings_json: str, name: str, header: Any, lock: Any, ready: Any, stop: Any, errors: Any
) -> None:
    """Actual IPC fixture, explicitly unrelated to a physical camera."""
    memory = shared_memory.SharedMemory(name=name)
    try:
        while not stop.is_set():
            with lock:
                np.ndarray((4, 6, 3), dtype=np.uint8, buffer=memory.buf)[:] = header[0] % 255
                header[1], header[2], header[3] = 4, 6, time.monotonic_ns()
                header[0] += 1
            ready.set()
            time.sleep(0.015)
    finally:
        memory.close()


@pytest.mark.parametrize("source", ["builtin_camera", "usb_camera"])
def test_local_sources_are_first_class(source: str) -> None:
    settings = VisionSettings.model_validate({"source": source, "camera_backend": "DSHOW"})
    instance = create_source(settings)
    assert isinstance(instance, LocalCameraSource)
    instance.close()


def test_discovery_checks_all_indices_and_only_suggests_unique(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def probe(index: int) -> dict[str, Any]:
        calls.append(index)
        return {"index": index, "available": index == 7, "friendly_name": None}

    monkeypatch.setattr(discovery, "probe_camera", probe)
    result = asyncio.run(discovery.camera_indices())
    assert calls == list(range(11))
    assert discovery.suggested_camera(result) == 7
    assert (
        discovery.suggested_camera(
            [{"index": 1, "available": True}, {"index": 7, "available": True}]
        )
        is None
    )
    assert discovery.suggested_camera([]) is None


def test_backend_fallback_is_sequential_and_returns_real_frame_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def run(command: list[str], **kwargs: Any) -> Any:
        selected = command[-1]
        calls.append(selected)
        return SimpleNamespace(
            stdout=json.dumps(
                {
                    "index": 3,
                    "backend": selected,
                    "available": selected == "MSMF",
                    "error": "CAMERA_OPEN_FAILED" if selected == "DSHOW" else None,
                    "width": 1280,
                    "height": 720,
                    "fps": 30,
                    "preview": "FIXTURE",
                }
            )
        )

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "run", run)
    result = discovery.probe_camera(3)
    assert calls == ["DSHOW", "MSMF"] and result["backend"] == "MSMF"
    assert result["available"] and result["preview"] == "FIXTURE"


def test_probe_timeout_and_busy_are_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def timeout(command: Any, **kwargs: Any) -> Any:
        assert kwargs["timeout"] == 4
        raise subprocess.TimeoutExpired(command, 4)

    monkeypatch.setattr(subprocess, "run", timeout)
    assert discovery.probe_camera(0)["error"] == "FRAME_TIMEOUT"

    def busy(command: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(
            stdout=json.dumps({"index": 0, "available": False, "error": "CAMERA_IN_USE"})
        )

    monkeypatch.setattr(subprocess, "run", busy)
    assert discovery.probe_camera(0)["available"] is False
    assert error_code(PermissionError(13, "denied")) == "CAMERA_PERMISSION_DENIED"
    assert error_code(OSError(16, "busy")) == "CAMERA_IN_USE"


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("mirror", [False, True])
def test_crop_rotation_mirror_define_geometry_coordinates(
    rotation: Literal[0, 90, 180, 270], mirror: bool
) -> None:
    image = np.arange(4 * 6 * 3, dtype=np.uint8).reshape(4, 6, 3)
    settings = VisionSettings(
        camera_crop=(0.5, 0, 0.5, 1), camera_rotation=rotation, camera_mirror=mirror
    )
    expected = image[:, 3:]
    if rotation:
        expected = np.asarray(
            cv2.rotate(
                expected,
                {
                    90: cv2.ROTATE_90_CLOCKWISE,
                    180: cv2.ROTATE_180,
                    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
                }[rotation],
            ),
            dtype=np.uint8,
        )
    if mirror:
        expected = np.asarray(cv2.flip(expected, 1), dtype=np.uint8)
    assert np.array_equal(transform_frame(image, settings), expected)
    with pytest.raises(ValueError, match="crop"):
        VisionSettings(camera_crop=(0.8, 0, 0.3, 1))


def test_live_process_stream_is_latest_only_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(camera, "_capture_worker", stream_fixture)
    source = LocalCameraSource(VisionSettings(source="builtin_camera", camera_backend="ANY"))
    source.open()
    try:
        first = source.read()
        time.sleep(0.12)  # Consumer is deliberately slower than capture.
        second = source.read()
        assert first is not None and second is not None
        assert second.frame_id > first.frame_id + 1
        assert source.dropped_frames > 0
        assert time.monotonic() - second.captured_at < 0.2
        assert first.frame.shape == (4, 6, 3)
    finally:
        source.close()
    assert source._process is None and source._memory is None


@pytest.mark.parametrize(
    "preset,shape,hz",
    [("LOW", (640, 480), 5), ("BALANCED", (1280, 720), 10), ("HIGH", (3840, 2160), 20)],
)
def test_presets_do_not_change_control_clock(
    tmp_path: Path, preset: str, shape: Any, hz: int
) -> None:
    c = configuration(tmp_path).model_copy(update={"performance_preset": preset}).with_preset()
    assert (c.camera.camera_width, c.camera.camera_height) == shape
    assert c.camera.inference_hz == hz and c.control_hz == 5


def test_camera_only_start_and_shutdown_send_zero_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    async def forbidden(self: Any, *args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        raise AssertionError("CAMERA_ONLY attempted hardware access")

    from smart_traffic_backend.maket.serial import Esp32TrafficLightController

    monkeypatch.setattr(Esp32TrafficLightController, "connect", forbidden)
    monkeypatch.setattr(Esp32TrafficLightController, "command", forbidden)

    async def run() -> None:
        r = MaketRuntime(configuration(tmp_path))
        await r.start()
        assert r.config.output_mode == "CAMERA_ONLY" and r.failure is None
        with pytest.raises(ValueError, match="CAMERA_ONLY"):
            await r.arm()
        await r.stop()

    asyncio.run(run())
    assert calls == []


def test_shadow_computes_without_esp32_and_stops_on_vision_loss(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        r = MaketRuntime(configuration(tmp_path))
        snapshot = r.pipeline.analyzer.process(
            (), source_id=r.config.camera.source_id, timestamp=0, frame_id=0, width=1280, height=720
        )
        checks = r.preflight()
        for k in (
            "camera_healthy",
            "model_compatible",
            "observation_fresh",
            "calibration_valid",
            "calibration_complete",
            "analytics_healthy",
            "controller_loaded",
        ):
            checks[k] = True
        monkeypatch.setattr(r, "preflight", lambda: checks)
        monkeypatch.setattr(r.pipeline, "observation", lambda: snapshot)
        events = []
        monkeypatch.setattr(
            r.recorder, "submit", lambda kind, payload: events.append((kind, payload))
        )
        try:
            assert not r.readiness()["HARDWARE_READY"] and r.readiness()["CONTROL_READY"]
            await r.start_camera_only()
            await r._shadow_tick()
            assert r.proposed is not None and r.hardware.last_command is None
            decision = next(p for k, p in events if k == "decision")
            assert decision["executed_action"] is None and decision["hardware_ack"] is None
            assert decision["actual_baseline_action"] is None and not decision["physical_commands"]
            checks["observation_fresh"] = False
            await r._shadow_tick()
            assert not r.demo_running and r.revalidation_required
            with pytest.raises(ValueError, match="revalidated"):
                await r.start_camera_only()
            assert r.hardware.last_command is None
        finally:
            await r.stop()

    asyncio.run(run())


def test_camera_only_api_rejects_active_without_blocking_console(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROHAI_MAKET_OPERATOR_TOKEN", "laptop-fixture-operator-secret")
    with TestClient(create_maket_app(configuration(tmp_path))) as client:
        client.headers["X-Operator-Token"] = "laptop-fixture-operator-secret"
        state = client.get("/api/maket/state").json()
        assert state["output_mode"] == "CAMERA_ONLY" and state["failure"] is None
        assert client.get("/maket").status_code == 200
        assert (
            client.post(
                "/api/maket/control",
                json={"action": "policy", "policy": "ADAPTIVE", "policy_mode": "ACTIVE"},
            ).status_code
            == 409
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("camera_rotation", 90),
        ("camera_mirror", True),
        ("camera_crop", [0.1, 0, 0.9, 1]),
        ("camera_backend", "MSMF"),
        ("camera_width", 640),
    ],
)
def test_transform_or_resolution_changes_invalidate_camera_calibration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: Any,
) -> None:
    monkeypatch.setenv("ROHAI_MAKET_OPERATOR_TOKEN", "laptop-fixture-operator-secret")

    async def start(self: Any) -> None:
        pass

    monkeypatch.setattr(MaketRuntime, "start", start)
    c = configuration(tmp_path)
    with TestClient(create_maket_app(c, tmp_path / "settings.json")) as client:
        client.headers["X-Operator-Token"] = "laptop-fixture-operator-secret"
        body = c.model_dump(mode="json")
        body["camera"][field] = value
        if field == "camera_width":
            body["performance_preset"] = "LOW"
        response = client.put("/api/maket/config", json=body)
        assert response.status_code == 200, response.text
        updated = client.get("/api/maket/config").json()
        assert updated["camera"]["source_id"] != c.camera.source_id
        assert client.get("/api/maket/geometry").json() is None
        assert (
            client.get("/api/maket/calibration-draft").json()["source_id"]
            == updated["camera"]["source_id"]
        )
        assert not client.get("/api/maket/state").json()["demo_running"]


def test_opened_device_without_frames_is_not_available(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class OpenButEmpty:
        def isOpened(self) -> bool:
            return True

        def read(self) -> tuple[bool, None]:
            return False, None

        def release(self) -> None:
            pass

    monkeypatch.setattr(cv2, "VideoCapture", lambda *a: OpenButEmpty())
    monkeypatch.setattr(sys, "argv", ["probe", "2", "DSHOW"])
    exec(discovery.PROBE_SCRIPT, {})
    result = json.loads(capsys.readouterr().out)
    assert result["error"] == "FRAME_TIMEOUT" and not result["available"]


def test_reconnect_requires_revalidation_and_never_resumes_demo(tmp_path: Path) -> None:
    async def run() -> None:
        r = MaketRuntime(configuration(tmp_path))
        try:
            await r.reconnect_camera()  # No selected camera: no native/device access.
            assert r.revalidation_required and not r.demo_running and not r.running
            assert r.hardware.last_command is None
            with pytest.raises(ValueError, match="Fresh matching"):
                await r.revalidate_camera()
            assert r.revalidation_required
        finally:
            await r.stop()

    asyncio.run(run())


def test_resolution_change_fails_calibration_even_with_recent_alignment(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from smart_traffic_backend.maket.config import MaketCalibration
    from traffic_vision.geometry import load_geometry

    async def run() -> None:
        r = MaketRuntime(configuration(tmp_path))
        r.calibration = MaketCalibration(
            intersection_id="FIXTURE",
            version=1,
            created_at=datetime.now(UTC),
            orientation_degrees=0,
            camera_pose_verified=True,
            geometry=load_geometry(Path("configs/maket/geometry-draft.json")),
            reference_image_sha256="0" * 64,
        )
        r.pipeline._latest_analysis = SimpleNamespace(  # type: ignore[assignment]
            video=SimpleNamespace(frame=np.zeros((480, 640, 3), dtype=np.uint8))
        )
        r.alignment, r.alignment_updated_at = "VALID", time.monotonic()
        try:
            assert not r.calibration_check()
            assert r.alignment == "INVALID_ID_OR_RESOLUTION"
        finally:
            r.pipeline._latest_analysis = None
            await r.stop()

    asyncio.run(run())

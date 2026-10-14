"""Software acceptance only. No claim about physical camera, lamps or ESP32."""

import asyncio
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from smart_traffic_backend.launcher import LaunchLock
from smart_traffic_backend.maket.app import create_maket_app
from smart_traffic_backend.maket.config import MaketConfig, SerialSettings
from smart_traffic_backend.maket.history import delete_closed_session, list_sessions, timeline
from smart_traffic_backend.maket.pairing import ViewerPairing
from smart_traffic_backend.maket.serial import Esp32TrafficLightController
from smart_traffic_backend.maket.session import SessionRecorder
from test_maket_mode import WireFixture


def configuration(tmp_path: Path) -> MaketConfig:
    c = MaketConfig.load(Path("configs/maket/maket.json"))
    return c.model_copy(
        update={
            "camera_selected": False,
            "output_mode": "CAMERA_ONLY",
            "session_directory": tmp_path / "sessions",
            "calibration_path": tmp_path / "calibration.json",
        }
    )


def test_protected_viewer_restart_expiry_and_revocation(tmp_path: Path) -> None:
    now = [1000.0]
    store = tmp_path / "pairing.dat"
    pairing = ViewerPairing(store, clock=lambda: now[0])
    token = pairing.issue()
    assert token.encode() not in store.read_bytes()
    reopened = ViewerPairing(store, clock=lambda: now[0])
    assert reopened.valid(token) and not reopened.valid("wrong")
    now[0] += 7 * 86400
    assert not reopened.valid(token)
    new = reopened.issue()
    assert not reopened.valid(token) and reopened.valid(new)
    reopened.revoke()
    assert not ViewerPairing(store, clock=lambda: now[0]).valid(new)


def test_pairing_view_only_survives_service_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ROHAI_MAKET_OPERATOR_TOKEN", "workspace-test-secret-32-characters")
    c = configuration(tmp_path)
    options: dict[str, Any] = {"base_url": "http://127.0.0.1:8001", "client": ("127.0.0.1", 50000)}
    origin = {"Origin": "http://127.0.0.1:8001"}
    with TestClient(
        create_maket_app(c, local_session=True, persistent_viewer=True), **options
    ) as client:
        assert client.post("/api/maket/session", headers=origin).status_code == 403
        assert (
            client.post(
                "/api/maket/pair-viewer", json={"consent": True}, headers=origin
            ).status_code
            == 204
        )
        viewer_cookie = client.cookies.get("rohai_viewer")
        assert client.get("/api/maket/state").status_code == 200
        assert client.get("/api/maket/permissions").json()["setup_authorized"] is False
        for action in ("start", "stop", "reconnect", "reset", "record"):
            assert (
                client.post(
                    "/api/maket/control",
                    json={"action": action, "confirm_physical": True},
                    headers=origin,
                ).status_code
                == 401
            )
        assert client.get("/api/maket/config").status_code == 401
        assert client.get("/api/maket/view-config").json()["camera"]["uri"] == ""
        assert (
            client.post("/api/maket/session", json={"consent": True}, headers=origin).status_code
            == 204
        )
        operator_cookie = client.cookies.get("rohai_local")
        assert viewer_cookie is not None and operator_cookie is not None
        assert client.get("/api/maket/permissions").json()["setup_authorized"] is True
        refusal = client.post("/api/maket/control", json={"action": "start"}, headers=origin)
        assert refusal.status_code == 409
        assert refusal.json()["error"]["code"] == "PHYSICAL_CONFIRMATION_REQUIRED"
    with TestClient(
        create_maket_app(c, local_session=True, persistent_viewer=True), **options
    ) as client:
        client.cookies.set("rohai_viewer", viewer_cookie, path="/api/maket")
        client.cookies.set("rohai_local", operator_cookie, path="/api/maket")
        assert client.get("/api/maket/state").status_code == 200
        assert client.get("/api/maket/permissions").json()["setup_authorized"] is False
        assert (
            client.post("/api/maket/control", json={"action": "estop"}, headers=origin).status_code
            == 401
        )
        assert client.delete("/api/maket/pair-viewer", headers=origin).status_code == 204
        assert client.get("/api/maket/state").status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://evil.example"},
        {"Origin": "http://127.0.0.1:8001", "Sec-Fetch-Site": "cross-site"},
        {"Origin": "http://127.0.0.1:8001", "Host": "evil.example:8001"},
    ],
)
def test_persistent_pairing_rejects_csrf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, headers: dict[str, str]
) -> None:
    monkeypatch.setenv("ROHAI_MAKET_OPERATOR_TOKEN", "workspace-test-secret-32-characters")
    with TestClient(
        create_maket_app(configuration(tmp_path), local_session=True, persistent_viewer=True),
        base_url="http://127.0.0.1:8001",
        client=("127.0.0.1", 50000),
    ) as client:
        assert (
            client.post(
                "/api/maket/pair-viewer", json={"consent": True}, headers=headers
            ).status_code
            == 403
        )


def test_readonly_identity_cannot_send_outputs(tmp_path: Path) -> None:
    async def run() -> None:
        wire = WireFixture()
        adapter = Esp32TrafficLightController(configuration(tmp_path).hardware, wire)
        await adapter.connect(read_only=True)
        assert [p["kind"] for p in wire.commands] == ["IDENTIFY"]
        assert adapter.snapshot()["physical_execution"] is False
        for kind in ("ARM", "HELLO", "SIGNAL", "STATUS", "ESTOP"):
            with pytest.raises(ValueError):
                await adapter.command(kind)
        assert len(wire.commands) == 1
        await adapter.close()

    asyncio.run(run())


def test_command_and_ack_recorded_in_causal_order(tmp_path: Path) -> None:
    async def run() -> None:
        c = configuration(tmp_path)
        recorder = SessionRecorder(c)
        adapter = Esp32TrafficLightController(c.hardware, WireFixture())
        adapter.event_sink = recorder.submit
        await adapter.connect()
        await adapter.command("STATUS")
        await adapter.close()
        recorder.close()
        events = timeline(c.session_directory, recorder.id)["events"]
        assert [e["kind"] for e in events] == ["hardware_command", "hardware_ack"] * 2
        assert events[0]["payload"]["command_id"] == events[1]["payload"]["command_id"]

    asyncio.run(run())


def test_concurrent_recording_order_and_bounded_replay(tmp_path: Path) -> None:
    c = configuration(tmp_path)
    recorder = SessionRecorder(c)

    def submit(worker: int) -> None:
        for value in range(8):
            recorder.submit("measurement", {"worker": worker, "value": value})
            time.sleep(0.003)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(submit, range(3)))
    recorder.close()
    first = timeline(c.session_directory, recorder.id, limit=7)
    second = timeline(c.session_directory, recorder.id, offset=7, limit=7)
    assert first["physical_execution"] is False and first["mode"] == "REPLAY"
    assert first["next_offset"] == 7
    assert second["events"][0]["sequence"] > first["events"][-1]["sequence"]
    assert second["events"][0]["offset_ms"] >= first["events"][-1]["offset_ms"]
    assert first["manifest"]["configuration_sha256"]
    assert first["manifest"]["privacy"]["automatic_deletion"] is False
    with pytest.raises(ValueError):
        timeline(c.session_directory, recorder.id, limit=501)


def test_recorded_video_frame_indices_and_privacy(tmp_path: Path) -> None:
    c = configuration(tmp_path)
    recorder = SessionRecorder(c)
    image = np.full((64, 64, 3), 120, dtype=np.uint8)
    recorder.submit_frame(image, {"frame_id": 1})
    assert not (recorder.path / "video.avi").exists()
    recorder.record_video = True
    recorder.submit_frame(image, {"frame_id": 2, "stream_epoch": 3})
    time.sleep(0.05)
    recorder.submit_frame(image, {"frame_id": 3, "stream_epoch": 3})
    recorder.close()
    events = timeline(c.session_directory, recorder.id)["events"]
    assert [e["payload"]["recorded_video_index"] for e in events] == [0, 1]
    assert events[0]["payload"]["stream_epoch"] == 3
    assert recorder.video_frames == 2


def test_full_storage_preserves_old_recording(tmp_path: Path) -> None:
    c = configuration(tmp_path).model_copy(update={"max_sessions": 1})
    old = SessionRecorder(c)
    old.submit("evidence", {"preserve": True})
    old.close()
    full = SessionRecorder(c)
    assert not full.enabled and old.path.exists()
    assert full.error == "SESSION_STORAGE_FULL"
    full.close()
    assert not full.path.exists()


def test_expiry_labels_and_explicit_single_session_deletion(tmp_path: Path) -> None:
    c = configuration(tmp_path)
    recorder = SessionRecorder(c)
    recorder.close()
    path = recorder.path / "session.json"
    manifest = json.loads(path.read_text())
    manifest["created_at"] = time.time() - 9 * 86400
    path.write_text(json.dumps(manifest))
    assert list_sessions(c.session_directory, 7)[0]["expired"] is True
    assert recorder.path.exists()
    for selected, confirmed, active in (
        (recorder.id, False, ""),
        (recorder.id, True, recorder.id),
        ("../outside", True, ""),
    ):
        with pytest.raises(ValueError):
            delete_closed_session(
                c.session_directory, selected, confirmed=confirmed, active_id=active
            )
    delete_closed_session(c.session_directory, recorder.id, confirmed=True, active_id="")
    assert not recorder.path.exists()


def test_replay_rejects_corrupt_causal_order(tmp_path: Path) -> None:
    c = configuration(tmp_path)
    recorder = SessionRecorder(c)
    recorder.submit("a", {})
    recorder.submit("b", {})
    recorder.close()
    event_path = recorder.path / "events.jsonl"
    events = [json.loads(line) for line in event_path.read_text().splitlines()]
    events[1]["sequence"] = events[0]["sequence"]
    event_path.write_text("\n".join(json.dumps(e) for e in events))
    with pytest.raises(ValueError, match="causal"):
        timeline(c.session_directory, recorder.id)


def test_launcher_exclusive_lock_release(tmp_path: Path) -> None:
    path = tmp_path / "launcher.lock"
    first, second = LaunchLock(path), LaunchLock(path)
    assert first.acquired and not second.acquired
    second.close()
    first.close()
    third = LaunchLock(path)
    assert third.acquired
    third.close()


def test_autosetup_missing_devices_never_arms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smart_traffic_backend.maket import setup
    from smart_traffic_backend.maket.runtime import MaketRuntime

    async def cameras(current: int | None) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(setup, "camera_indices", cameras)
    monkeypatch.setattr(setup, "serial_ports", lambda: [])

    async def run() -> None:
        runtime = MaketRuntime(configuration(tmp_path))
        await runtime.start()
        try:
            state: dict[str, Any] = {}
            await setup.prepare(runtime, tmp_path / "config.json", state)
            assert state["state"] == "COMPLETE" and state["physical_control_started"] is False
            assert {i["code"] for i in state["issues"]} == {"CAMERA_NOT_FOUND", "ESP32_NOT_FOUND"}
            assert not runtime.running and not runtime.hardware.connected
        finally:
            await runtime.stop()

    asyncio.run(run())


@pytest.mark.parametrize("count", [1, 2])
def test_autosetup_unique_or_ambiguous_devices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int
) -> None:
    from smart_traffic_backend.maket import setup
    from smart_traffic_backend.maket.runtime import MaketRuntime

    wires: list[WireFixture] = []

    class FixtureAdapter(Esp32TrafficLightController):
        def __init__(self, settings: SerialSettings) -> None:
            wire = WireFixture()
            wires.append(wire)
            super().__init__(settings, wire)

    async def cameras(current: int | None) -> list[dict[str, Any]]:
        return [
            {"index": i, "backend": "MSMF", "available": True, "preview": "fixture"}
            for i in range(count)
        ]

    monkeypatch.setattr(setup, "camera_indices", cameras)
    monkeypatch.setattr(
        setup,
        "serial_ports",
        lambda: [{"port": f"FIXTURE-{i}", "esp32_candidate": True} for i in range(count)],
    )
    monkeypatch.setattr(setup, "Esp32TrafficLightController", FixtureAdapter)

    async def run() -> None:
        runtime = MaketRuntime(configuration(tmp_path))
        restarted = []

        async def reconnect() -> None:
            restarted.append(True)  # No camera is opened by this explicit software fixture.

        monkeypatch.setattr(runtime, "reconnect_camera", reconnect)
        try:
            state: dict[str, Any] = {}
            await setup.prepare(runtime, tmp_path / "config.json", state)
            assert all("preview" not in c for c in state["cameras"])
            assert state["physical_control_started"] is False
            if count == 1:
                assert restarted == [True] and runtime.config.camera_selected
                assert [p["kind"] for p in wires[0].commands] == ["IDENTIFY"]
                assert not state["device"]["connected"] and state["device"]["inspection_only"]
            else:
                assert not restarted and not wires
                assert {i["code"] for i in state["issues"]} == {
                    "CAMERA_CHOICE_REQUIRED",
                    "ESP32_CHOICE_REQUIRED",
                }
        finally:
            await runtime.stop()

    asyncio.run(run())


def test_recording_limit_stops_without_deleting_evidence(tmp_path: Path) -> None:
    recorder = SessionRecorder(configuration(tmp_path))
    recorder.started = time.monotonic() - recorder.config.max_recording_seconds - 1
    recorder.record_video = True
    recorder.submit("after_limit", {})
    deadline = time.monotonic() + 1
    while recorder.error is None and time.monotonic() < deadline:
        time.sleep(0.005)
    assert recorder.error == "retention_limit_recording_stopped"
    assert not recorder.record_video
    recorder.close()
    manifest = json.loads((recorder.path / "session.json").read_text())
    assert not manifest["complete"] and manifest["dropped_events"] == 1
    assert recorder.path.exists()


def test_expired_launch_permission_never_arms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from smart_traffic_backend.maket import app as app_module
    from smart_traffic_backend.maket.runtime import MaketRuntime

    offset = [0.0]
    real_clock = time.monotonic
    monkeypatch.setattr(
        app_module,
        "time",
        SimpleNamespace(time=time.time, monotonic=lambda: real_clock() + offset[0]),
    )
    calls: list[str] = []

    async def forbidden_arm(runtime: MaketRuntime) -> None:
        calls.append("ARM")

    monkeypatch.setattr(MaketRuntime, "arm", forbidden_arm)
    secret = "workspace-test-secret-32-characters"
    monkeypatch.setenv("ROHAI_MAKET_OPERATOR_TOKEN", secret)
    with TestClient(create_maket_app(configuration(tmp_path))) as client:
        offset[0] = 8 * 3600 + 1
        for action in ("start", "camera_only_start", "hardware_test_start"):
            r = client.post(
                "/api/maket/control",
                json={"action": action, "confirm_physical": True},
                headers={"X-Operator-Token": secret},
            )
            assert r.status_code == 409
            assert r.json()["error"]["code"] == "LOCAL_PERMISSION_REQUIRED"
        assert not calls

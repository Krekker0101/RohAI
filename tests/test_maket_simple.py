"""Local-session and assisted-calibration contracts; no physical camera evidence."""

from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from smart_traffic_backend.maket.app import create_maket_app
from smart_traffic_backend.maket.assisted import suggest_geometry
from smart_traffic_backend.maket.config import MaketConfig
from smart_traffic_backend.maket.runtime import MaketRuntime
from traffic_vision.geometry import IntersectionGeometry


def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MaketConfig:
    monkeypatch.setenv("ROHAI_MAKET_OPERATOR_TOKEN", "simple-fixture-operator-secret")
    return MaketConfig.load(Path("configs/maket/maket.json")).model_copy(
        update={"camera_selected": False, "session_directory": tmp_path / "sessions"}
    )


def test_loopback_cookie_session_without_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    c = config(tmp_path, monkeypatch)
    with TestClient(
        create_maket_app(c, local_session=True),
        base_url="http://127.0.0.1:8001",
        client=("127.0.0.1", 50000),
    ) as client:
        assert client.get("/api/maket/state").status_code == 401
        response = client.post(
            "/api/maket/session", headers={"Origin": str(client.base_url).rstrip("/")}
        )
        assert response.status_code == 204
        cookie = response.headers["set-cookie"]
        assert "HttpOnly" in cookie and "SameSite=strict" in cookie
        assert "Path=/api/maket" in cookie
        snapshot = client.get("/api/maket/state").json()
        assert snapshot["output_mode"] == "CAMERA_ONLY"
        assert not snapshot["demo_running"] and snapshot["virtual_signal_state"] is None
        assert (
            client.post(
                "/api/maket/control",
                headers={"Origin": "https://evil.example"},
                json={"action": "stop"},
            ).status_code
            == 401
        )
        assert client.get("/api/maket/state", headers={"Host": "evil.example"}).status_code == 401
        assert client.post("/api/maket/control", json={"action": "start"}).status_code == 409
        for route in ("/maket", "/maket/engineer"):
            page = client.get(route)
            assert page.status_code == 200 and "simple-fixture-operator-secret" not in page.text
        assert "simple.js" in client.get("/maket").text
        assert 'id="token"' in client.get("/maket/engineer").text


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Origin": "https://evil.example"},
        {"Origin": "http://127.0.0.1:8001", "Host": "evil.example:8001"},
        {"Origin": "http://127.0.0.1:8001", "Sec-Fetch-Site": "cross-site"},
    ],
)
def test_session_rejects_missing_origin_csrf_and_rebinding(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
) -> None:
    c = config(tmp_path, monkeypatch)
    with TestClient(
        create_maket_app(c, local_session=True),
        base_url="http://127.0.0.1:8001",
        client=("127.0.0.1", 50000),
    ) as client:
        assert client.post("/api/maket/session", headers=headers).status_code == 403


def test_session_disabled_for_remote_peer_and_default_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = config(tmp_path, monkeypatch)
    for enabled, peer in ((True, "192.0.2.1"), (False, "127.0.0.1")):
        with TestClient(
            create_maket_app(c, local_session=enabled),
            base_url="http://127.0.0.1:8001",
            client=(peer, 50000),
        ) as client:
            assert (
                client.post(
                    "/api/maket/session", headers={"Origin": "http://127.0.0.1:8001"}
                ).status_code
                == 403
            )
            assert client.get("/api/maket/state").status_code == 401
            assert (
                client.get(
                    "/api/maket/state",
                    headers={"X-Operator-Token": "simple-fixture-operator-secret"},
                ).status_code
                == 200
            )


def test_session_is_invalid_after_process_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = config(tmp_path, monkeypatch)
    with TestClient(
        create_maket_app(c, local_session=True),
        base_url="http://127.0.0.1:8001",
        client=("127.0.0.1", 50000),
    ) as first:
        first.post("/api/maket/session", headers={"Origin": "http://127.0.0.1:8001"})
        cookie = first.cookies.get("rohai_local")
    with TestClient(
        create_maket_app(c, local_session=True),
        base_url="http://127.0.0.1:8001",
        client=("127.0.0.1", 50000),
    ) as second:
        second.cookies.set("rohai_local", cookie)
        assert second.get("/api/maket/state").status_code == 401


def test_assisted_calibration_refuses_blank_and_returns_unverified_image_candidate() -> None:
    blank = np.zeros((720, 1280, 3), dtype=np.uint8)
    assert not suggest_geometry(blank, "fixture")["available"]
    for x in (460, 819):
        cv2.line(blank, (x, 0), (x, 719), (255, 255, 255), 3)
    for y in (259, 460):
        cv2.line(blank, (0, y), (1279, y), (255, 255, 255), 3)
    result = suggest_geometry(blank, "fixture")
    assert result["available"] and not result["verified"]
    g = IntersectionGeometry.model_validate(result["geometry"])
    assert g.source_id == "fixture" and g.calibration_validated_at is None


def test_cli_default_start_opens_browser_without_printing_secret(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import uvicorn
    from smart_traffic_backend.maket.app import main

    c = config(tmp_path, monkeypatch)
    settings = tmp_path / "config.json"
    settings.write_text(c.model_dump_json(), encoding="utf-8")
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append(kwargs))
    assert main(["--config", str(settings), "--open"]) == 0
    output = capsys.readouterr().out
    assert "RohAI Maket started" in output and "simple-fixture-operator-secret" not in output
    assert calls[0]["host"] == "127.0.0.1"


def test_new_camera_uses_unverified_draft_and_never_relabels_old_calibration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from datetime import UTC, datetime

    from smart_traffic_backend.maket.config import MaketCalibration

    c = config(tmp_path, monkeypatch).model_copy(
        update={
            "camera": config(tmp_path, monkeypatch).camera.model_copy(
                update={"source_id": "new-camera"}
            ),
            "calibration_path": tmp_path / "calibration.json",
        }
    )
    geometry = IntersectionGeometry.model_validate_json(
        Path("configs/maket/geometry-draft.json").read_text(encoding="utf-8")
    )
    old = MaketCalibration(
        intersection_id="fixture",
        version=1,
        created_at=datetime.now(UTC),
        orientation_degrees=0,
        camera_pose_verified=True,
        geometry=geometry,
        reference_image_sha256="0" * 64,
    )
    original = old.model_dump_json()
    c.calibration_path.write_text(original, encoding="utf-8")
    runtime = MaketRuntime(c)

    async def no_camera() -> None:
        pass

    monkeypatch.setattr(runtime, "_start_camera", no_camera)
    try:
        for restarting in (False, True):
            if restarting:
                asyncio.run(runtime.reconnect_camera())
            assert runtime.calibration is None
            assert runtime.pipeline.analyzer.geometry.source_id == "new-camera"
            assert runtime.pipeline.analyzer.geometry.calibration_validated_at is None
            runtime.pipeline.analyzer.process(
                (), source_id="new-camera", timestamp=1, frame_id=0, width=1280, height=720
            )
            assert not runtime.readiness()["CONTROL_READY"]
        assert c.calibration_path.read_text(encoding="utf-8") == original
    finally:
        asyncio.run(runtime.stop())

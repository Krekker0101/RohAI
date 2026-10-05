"""Local authenticated stand console; isolated from demo/replay APIs."""

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal

import cv2
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import Field
from traffic_core.models import Decision, Model, Phase

from smart_traffic_backend.maket.config import MaketCalibration, MaketConfig
from smart_traffic_backend.maket.discovery import (
    camera_indices,
    probe_camera,
    serial_ports,
    suggested_camera,
)
from smart_traffic_backend.maket.runtime import MaketRuntime


class ControlRequest(Model):
    action: str
    policy: str | None = None
    policy_mode: str | None = None
    phase: Phase | None = None
    duration_s: float = Field(default=8, ge=1, le=180)


class CameraTestRequest(Model):
    index: int = Field(ge=0, le=128)
    backend: Literal["AUTO", "DSHOW", "MSMF", "ANY"] = "AUTO"


def create_maket_app(
    config: MaketConfig,
    config_path: Path = Path("configs/maket/maket.json"),
    *,
    local_session: bool = False,
    open_browser: bool = False,
) -> FastAPI:
    token = os.environ.get(config.operator_token_env)
    if not token or len(token) < 16:
        raise ValueError(f"Set {config.operator_token_env} to a secret of at least 16 characters")
    holder: dict[str, MaketRuntime] = {}
    session_secret = secrets.token_urlsafe(32)
    session_expires = time.monotonic() + 8 * 3600
    local_origin = f"http://127.0.0.1:{config.port}"

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = MaketRuntime(config)
        holder["runtime"] = runtime
        try:
            await runtime.start()
            if open_browser:
                import webbrowser

                asyncio.create_task(asyncio.to_thread(webbrowser.open, local_origin + "/maket"))
            yield
        finally:
            await holder["runtime"].stop()

    app = FastAPI(title="RohAI Physical Maket", lifespan=lifespan)
    operation_lock = asyncio.Lock()

    async def exclusive_operation() -> AsyncIterator[None]:
        if operation_lock.locked():
            raise HTTPException(409, "Another setup/control operation is in progress")
        async with operation_lock:
            yield

    def local_request(request: Request) -> bool:
        return bool(
            local_session
            and request.client
            and request.client.host == "127.0.0.1"
            and request.headers.get("host") == f"127.0.0.1:{config.port}"
            and request.headers.get("origin", local_origin) == local_origin
            and request.headers.get("sec-fetch-site", "same-origin") == "same-origin"
        )

    def auth(request: Request, x_operator_token: Annotated[str | None, Header()] = None) -> None:
        if secrets.compare_digest(x_operator_token or "", token):
            return
        if (
            local_request(request)
            and time.monotonic() < session_expires
            and secrets.compare_digest(request.cookies.get("rohai_local", ""), session_secret)
        ):
            return
        raise HTTPException(401, "Operator token required")

    @app.post("/api/maket/session")
    def establish_session(request: Request) -> Response:
        if not local_request(request) or request.headers.get("origin") != local_origin:
            raise HTTPException(403, "Local same-origin session only")
        if time.monotonic() >= session_expires:
            raise HTTPException(401, "Local session expired; restart launcher")
        response = Response(status_code=204, headers={"Cache-Control": "no-store"})
        # HTTP loopback: Secure would prevent sending the cookie in some browsers.
        # HttpOnly, Strict, exact Host/Origin and loopback peer checks bound its use.
        response.set_cookie(
            "rohai_local",
            session_secret,
            httponly=True,
            samesite="strict",
            max_age=8 * 3600,
            path="/api/maket",
        )
        return response

    @app.get("/api/maket/state", dependencies=[Depends(auth)])
    async def state() -> dict[str, Any]:
        return holder["runtime"].snapshot()

    @app.get("/api/maket/config", dependencies=[Depends(auth)])
    async def configuration() -> dict[str, Any]:
        return holder["runtime"].config.model_dump(mode="json")

    discovery_lock = asyncio.Lock()

    @app.get("/api/maket/discovery", dependencies=[Depends(auth), Depends(exclusive_operation)])
    async def discover() -> dict[str, Any]:
        r = holder["runtime"]
        if r.running or r.demo_running or discovery_lock.locked():
            raise HTTPException(409, "Stop control before discovery; only one probe at a time")
        async with discovery_lock:
            if r.pipeline.source.connected:
                await asyncio.to_thread(r.pipeline.stop)
                r.camera_connection = "RELEASED_FOR_DISCOVERY"
                r.alignment = "NOT_CHECKED"
            ports, cameras = await asyncio.gather(
                asyncio.to_thread(serial_ports), camera_indices(None)
            )
            return {
                "serial_ports": ports,
                "cameras": cameras,
                "hardware_tested": False,
                "suggested_index": suggested_camera(cameras),
                "error": None if any(c.get("available") for c in cameras) else "CAMERA_NOT_FOUND",
            }

    @app.post("/api/maket/camera-test", dependencies=[Depends(auth), Depends(exclusive_operation)])
    async def camera_test(body: CameraTestRequest) -> dict[str, Any]:
        r = holder["runtime"]
        if r.running or r.demo_running:
            raise HTTPException(409, "Stop control/demo before camera probe")
        if r.pipeline.source.connected:
            live = r.pipeline.latest_capture
            if (
                body.index == r.config.camera.webcam_index
                and live is not None
                and body.backend in ("AUTO", r.config.camera.camera_backend)
                and time.monotonic() - live.captured_at < 1.5
            ):
                ok, encoded = await asyncio.to_thread(cv2.imencode, ".jpg", live.frame)
                return {
                    **getattr(r, "camera_metadata", {}),
                    "index": body.index,
                    "backend": r.config.camera.camera_backend,
                    "available": True,
                    "preview": base64.b64encode(encoded).decode() if ok else None,
                }
            await asyncio.to_thread(r.pipeline.stop)
            r.camera_connection = "RELEASED_FOR_TEST"
            r.alignment = "NOT_CHECKED"
        return await asyncio.to_thread(probe_camera, body.index, body.backend)

    @app.put("/api/maket/config", dependencies=[Depends(auth), Depends(exclusive_operation)])
    async def save_configuration(body: dict[str, Any]) -> dict[str, str]:
        if holder["runtime"].running or holder["runtime"].demo_running:
            raise HTTPException(409, "Stop before setup changes")
        try:
            updated = MaketConfig.model_validate(body).with_preset()
            previous = holder["runtime"].config.camera
            identity_fields = (
                "source",
                "webcam_index",
                "uri",
                "camera_backend",
                "camera_rotation",
                "camera_mirror",
                "camera_crop",
                "camera_width",
                "camera_height",
            )
            if any(getattr(updated.camera, k) != getattr(previous, k) for k in identity_fields):
                # Reusing a source label must not revalidate an old camera calibration.
                updated = updated.model_copy(
                    update={
                        "camera": updated.camera.model_copy(
                            update={"source_id": "maket-" + uuid.uuid4().hex[:16]}
                        )
                    }
                )
            await asyncio.to_thread(
                config_path.write_text, updated.model_dump_json(indent=2), encoding="utf-8"
            )
            await holder["runtime"].stop()
            holder["runtime"] = MaketRuntime(updated)
            await holder["runtime"].start()
            return {"status": "SAVED_AND_RELOADED_STOPPED_SHADOW"}
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/maket/geometry", dependencies=[Depends(auth)])
    async def geometry() -> dict[str, Any] | None:
        return holder["runtime"].overlay_calibration()["geometry"]

    @app.get("/api/maket/calibration-draft", dependencies=[Depends(auth)])
    async def calibration_draft() -> dict[str, Any]:
        r = holder["runtime"]
        # Editor draft only; runtime overlays use snapshot.overlay_calibration.
        return r.pipeline.analyzer.geometry.model_copy(
            update={"source_id": r.config.camera.source_id}
        ).model_dump(mode="json")

    @app.post(
        "/api/maket/calibration-suggestion",
        dependencies=[Depends(auth), Depends(exclusive_operation)],
    )
    async def calibration_suggestion() -> dict[str, Any]:
        from smart_traffic_backend.maket.assisted import suggest_geometry

        r = holder["runtime"]
        capture = r.pipeline.latest_capture
        if r.running or r.demo_running or capture is None:
            raise HTTPException(409, "Stop control and open live camera first")
        if time.monotonic() - capture.captured_at > 1.5:
            raise HTTPException(409, "Fresh camera frame required")
        return await asyncio.to_thread(suggest_geometry, capture.frame, r.config.camera.source_id)

    async def execute_control(body: ControlRequest) -> dict[str, Any]:
        r = holder["runtime"]
        try:
            if body.action in ("stop", "estop"):
                await r.emergency_stop()
            elif body.action == "start":
                await r.arm()
            elif body.action == "camera_only_start":
                await r.start_camera_only()
            elif body.action == "hardware_test_start":
                await r.arm_hardware_test()
            elif body.action == "reconnect":
                await r.reconnect()
            elif body.action == "reconnect_camera":
                await r.reconnect_camera()
            elif body.action == "revalidate_camera":
                await r.revalidate_camera()
            elif body.action == "policy":
                if r.running or r.demo_running:
                    raise ValueError("Stop before changing policy/mode")
                if body.policy not in ("FIXED", "ADAPTIVE", "LEARNED_AI"):
                    raise ValueError("Invalid policy")
                if body.policy_mode not in ("SHADOW", "ACTIVE"):
                    raise ValueError("Invalid policy mode")
                if body.policy_mode == "ACTIVE" and r.config.output_mode == "CAMERA_ONLY":
                    raise ValueError("CAMERA_ONLY only permits SHADOW recommendations")
                if body.policy == "LEARNED_AI" and r.policy.model is None:
                    raise ValueError("No compatible learned artifact loaded")
                if (
                    body.policy == "LEARNED_AI"
                    and body.policy_mode == "ACTIVE"
                    and not r.policy.active_approved
                ):
                    raise ValueError("Offline/shadow/stand evidence and approval required")
                if (
                    body.policy == "LEARNED_AI"
                    and body.policy_mode == "ACTIVE"
                    and r.config.vision_profile != "MAKET_MODEL"
                ):
                    raise ValueError("Learned ACTIVE requires an evaluated Maket vision release")
                r.policy.name = body.policy
                r.policy_mode = body.policy_mode
                r.manual = None
            elif body.action == "manual":
                if not r.running or body.phase is None:
                    raise ValueError("Arm validated stand before manual phase request")
                r.manual = Decision(
                    phase=body.phase, green_seconds=body.duration_s, reason="operator_phase_request"
                )
            elif body.action == "record":
                r.recorder.record_video = not r.recorder.record_video
            else:
                raise ValueError("Unknown action")
        except (ValueError, RuntimeError, TimeoutError, OSError) as error:
            raise HTTPException(409, str(error)) from error
        return r.snapshot()

    @app.post("/api/maket/control", dependencies=[Depends(auth)])
    async def control(body: ControlRequest) -> dict[str, Any]:
        # E-stop must remain available during discovery/configuration/calibration.
        if body.action in ("stop", "estop"):
            return await execute_control(body)
        if operation_lock.locked():
            raise HTTPException(409, "Another setup/control operation is in progress")
        async with operation_lock:
            return await execute_control(body)

    @app.put("/api/maket/calibration", dependencies=[Depends(auth), Depends(exclusive_operation)])
    async def calibration(body: dict[str, Any]) -> dict[str, str]:
        r = holder["runtime"]
        current_config = r.config
        if r.running or r.demo_running:
            raise HTTPException(409, "Stop before calibration")
        frame = r.pipeline.latest_capture
        if (
            frame is None
            or not r.pipeline.source.connected
            or (time.monotonic() - frame.captured_at > 1.5)
        ):
            raise HTTPException(409, "Fresh live camera frame unavailable")
        try:
            # Reference belongs to this exact live frame, not an uploaded stale image.
            ok, encoded = await asyncio.to_thread(cv2.imencode, ".png", frame.frame)
            if not ok:
                raise ValueError("Reference encoding failed")
            data = encoded.tobytes()
            body["reference_image_sha256"] = hashlib.sha256(data).hexdigest()
            c = MaketCalibration.model_validate(body)
            h, w = frame.frame.shape[:2]
            if c.geometry.source_id != current_config.camera.source_id or (w, h) != (
                c.geometry.reference_width,
                c.geometry.reference_height,
            ):
                raise ValueError("Calibration camera/resolution mismatch")

            def write_calibration() -> None:
                current_config.reference_image_path.parent.mkdir(parents=True, exist_ok=True)
                current_config.reference_image_path.write_bytes(data)
                current_config.calibration_path.write_text(
                    c.model_dump_json(indent=2), encoding="utf-8"
                )

            await asyncio.to_thread(write_calibration)
            await r.emergency_stop("calibration_changed")
            await r.reconnect_camera()
            r.revalidation_required = False  # Operator verified this new live reference.
            return {"status": "SAVED_CAMERA_RESTARTED_STOPPED_SHADOW"}
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    preview_task: asyncio.Task[bytes] | None = None
    preview_key: float | None = None

    @app.get("/api/maket/frame", dependencies=[Depends(auth)])
    async def frame() -> Response:
        nonlocal preview_task, preview_key
        r = holder["runtime"]
        current = r.pipeline.latest_capture
        if current is None or time.monotonic() - current.captured_at > 1.5:
            raise HTTPException(503, "Live frame unavailable")

        def encode() -> bytes:
            ok, data = cv2.imencode(".jpg", current.frame)
            if not ok:
                raise RuntimeError("JPEG failed")
            return data.tobytes()

        if (
            preview_task is not None
            and not preview_task.done()
            and preview_key != current.captured_at
        ):
            raise HTTPException(429, "Preview encoder busy; consume latest frame later")
        if preview_task is None or preview_key != current.captured_at:
            preview_key = current.captured_at
            preview_task = asyncio.create_task(asyncio.to_thread(encode))
            preview_task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        return Response(
            await asyncio.shield(preview_task),
            media_type="image/jpeg",
            headers={
                "X-Frame-Width": str(current.frame.shape[1]),
                "X-Frame-Height": str(current.frame.shape[0]),
                "Cache-Control": "no-store",
            },
        )

    @app.get("/api/maket/events", dependencies=[Depends(auth)])
    async def events() -> StreamingResponse:
        async def stream() -> AsyncIterator[bytes]:
            while True:
                yield (json.dumps(holder["runtime"].snapshot()) + "\n").encode()
                await asyncio.sleep(0.5)

        return StreamingResponse(stream(), media_type="application/x-ndjson")

    @app.get("/maket")
    def console() -> FileResponse:
        return FileResponse("apps/maket-ui/simple.html", headers={"Cache-Control": "no-store"})

    @app.get("/maket/engineer")
    def engineer_console() -> FileResponse:
        return FileResponse("apps/maket-ui/index.html")

    app.mount("/maket-assets", StaticFiles(directory="apps/maket-ui"), name="maket-assets")
    return app


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(prog="rohai maket")
    parser.add_argument(
        "action",
        nargs="?",
        default="start",
        choices=("start", "validate", "ports", "cameras", "camera-test", "replay"),
    )
    parser.add_argument("--config", type=Path, default=Path("configs/maket/maket.json"))
    parser.add_argument("--session", type=Path)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--backend", choices=("AUTO", "DSHOW", "MSMF", "ANY"), default="AUTO")
    parser.add_argument("--open", action=argparse.BooleanOptionalAction, default=None)
    args = parser.parse_args(argv)
    if args.action == "ports":
        print(json.dumps(serial_ports()))
        return 0
    if args.action == "cameras":
        cameras = asyncio.run(camera_indices())
        for c in cameras:
            c.pop("preview", None)
        print(
            json.dumps(
                {
                    "cameras": cameras,
                    "suggested_index": suggested_camera(cameras),
                    "error": None
                    if any(c.get("available") for c in cameras)
                    else "CAMERA_NOT_FOUND",
                }
            )
        )
        return 0
    if args.action == "camera-test":
        if args.index < 0 or args.index > 128:
            parser.error("--index must be between 0 and 128")
        result = probe_camera(args.index, args.backend)
        result.pop("preview", None)
        print(json.dumps(result))
        return 0 if result.get("available") else 2
        return 0
    if args.action == "replay":
        if args.session is None:
            parser.error("--session is required")
        # Replay explicitly has no hardware adapter or live-control capability.
        events = (args.session / "events.jsonl").read_text(encoding="utf-8").splitlines()
        print(
            json.dumps(
                {
                    "mode": "VIDEO_REPLAY",
                    "physical_outputs": False,
                    "events": len(events),
                    "video": str(args.session / "video.avi"),
                }
            )
        )
        import uvicorn

        from smart_traffic_backend.maket.replay import replay_app

        uvicorn.run(replay_app(args.session), host="127.0.0.1", port=8002)
        return 0
    try:
        config = MaketConfig.load(args.config)
        if args.action == "validate":
            print(
                json.dumps(
                    {
                        "config": "VALID",
                        "hardware": "NOT_CONNECTED",
                        "camera": "NOT_TESTED",
                        "active": "NOT_AUTHORIZED",
                    }
                )
            )
            return 0
        import uvicorn

        if not os.environ.get(config.operator_token_env):
            os.environ[config.operator_token_env] = secrets.token_urlsafe(24)
        print(
            f"RohAI Maket started\nMode: {config.output_mode}\n"
            f"Dashboard: http://{config.host}:{config.port}/maket\nPress Ctrl+C to stop."
        )
        import sys

        should_open = sys.stdin.isatty() if args.open is None else args.open
        uvicorn.run(
            create_maket_app(config, args.config, local_session=True, open_browser=should_open),
            host=config.host,
            port=config.port,
            log_level="warning",
        )
        return 0
    except (ValueError, OSError, ImportError) as error:
        print(json.dumps({"status": "FAIL", "reason": str(error), "mock_fallback": False}))
        return 2

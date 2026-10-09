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
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
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
    confirm_physical: bool = False
    enabled: bool | None = None


class CameraTestRequest(Model):
    index: int = Field(ge=0, le=128)
    backend: Literal["AUTO", "DSHOW", "MSMF", "ANY"] = "AUTO"


def create_maket_app(
    config: MaketConfig,
    config_path: Path = Path("configs/maket/maket.json"),
    *,
    local_session: bool = False,
    open_browser: bool = False,
    persistent_viewer: bool = False,
    auto_setup: bool = False,
) -> FastAPI:
    token = os.environ.get(config.operator_token_env)
    if not token or len(token) < 16:
        raise ValueError(f"Set {config.operator_token_env} to a secret of at least 16 characters")
    holder: dict[str, MaketRuntime] = {}
    session_secret = secrets.token_urlsafe(32)
    session_expires = time.monotonic() + 8 * 3600
    local_origin = f"http://127.0.0.1:{config.port}"
    from smart_traffic_backend.maket.pairing import ViewerPairing

    viewer = (
        ViewerPairing(config.session_directory.parent / "viewer-pairing.dat")
        if persistent_viewer
        else None
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = MaketRuntime(config)
        holder["runtime"] = runtime
        try:
            await runtime.start()
            if auto_setup:
                await autosetup()
            if open_browser:
                import webbrowser

                asyncio.create_task(asyncio.to_thread(webbrowser.open, local_origin + "/maket"))
            yield
        finally:
            if setup_task is not None and not setup_task.done():
                setup_task.cancel()
                from contextlib import suppress

                with suppress(asyncio.CancelledError):
                    await setup_task
            await holder["runtime"].stop()

    app = FastAPI(title="RohAI Physical Maket", lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
        diagnostic = {
            "code": "REQUEST_INVALID",
            "message": "Параметры запроса некорректны.",
            "action": "Проверьте значения в настройках и повторите.",
        }
        return JSONResponse(status_code=422, content={"detail": diagnostic, "error": diagnostic})

    @app.exception_handler(HTTPException)
    async def friendly_error(request: Request, error: HTTPException) -> JSONResponse:
        from smart_traffic_backend.maket.setup import issue

        reason = str(error.detail).lower()
        code = next(
            (
                code
                for key, code in (
                    ("firmware", "FIRMWARE_INCOMPATIBLE"),
                    ("ack", "ACK_TIMEOUT"),
                    ("calibration", "CALIBRATION_REQUIRED"),
                    ("camera", "VIDEO_LOST"),
                    ("model", "VISION_NOT_READY"),
                    ("protection", "PHYSICAL_PROTECTION_REQUIRED"),
                    ("confirmation", "PHYSICAL_CONFIRMATION_REQUIRED"),
                    ("token", "LOCAL_PERMISSION_REQUIRED"),
                    ("session expired", "LOCAL_PERMISSION_REQUIRED"),
                    ("storage full", "SESSION_STORAGE_FULL"),
                )
                if key in reason
            ),
            "LOCAL_PERMISSION_REQUIRED"
            if error.status_code in (401, 403)
            else "OPERATION_REJECTED",
        )
        diagnostic = error.detail if isinstance(error.detail, dict) else issue(code)
        return JSONResponse(
            status_code=error.status_code,
            content={"detail": error.detail, "error": diagnostic},
            headers={"Cache-Control": "no-store", **(error.headers or {})},
        )

    app.state.request_shutdown = None
    operation_lock = asyncio.Lock()
    setup_task: asyncio.Task[None] | None = None

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

    def privileged(request: Request, supplied: str = "") -> bool:
        return secrets.compare_digest(supplied, token) or bool(
            local_request(request)
            and time.monotonic() < session_expires
            and secrets.compare_digest(request.cookies.get("rohai_local", ""), session_secret)
        )

    def auth(request: Request, x_operator_token: Annotated[str | None, Header()] = None) -> None:
        if privileged(request, x_operator_token or ""):
            return
        read_paths = {
            "/api/maket/state",
            "/api/maket/geometry",
            "/api/maket/calibration-draft",
            "/api/maket/permissions",
            "/api/maket/view-config",
            "/api/maket/frame",
            "/api/maket/sessions",
        }
        read_request = request.method == "GET" and (
            request.url.path in read_paths or request.url.path.startswith("/api/maket/replay/")
        )
        read_request = read_request or (
            request.method == "DELETE" and request.url.path == "/api/maket/pair-viewer"
        )
        if (
            read_request
            and viewer is not None
            and local_request(request)
            and viewer.valid(request.cookies.get("rohai_viewer", ""))
        ):
            return
        raise HTTPException(401, "Operator token required")

    @app.post("/api/maket/session")
    def establish_session(request: Request, body: dict[str, Any] | None = None) -> Response:
        if not local_request(request) or request.headers.get("origin") != local_origin:
            raise HTTPException(403, "Local same-origin session only")
        if time.monotonic() >= session_expires:
            raise HTTPException(401, "Local session expired; restart launcher")
        if persistent_viewer and (body is None or body.get("consent") is not True):
            raise HTTPException(
                403,
                detail={
                    "code": "SETUP_PERMISSION_REQUIRED",
                    "message": "Настройка требует разрешения на текущий запуск.",
                    "action": "Подтвердите разрешение на настройку в интерфейсе.",
                },
            )
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

    @app.post("/api/maket/pair-viewer")
    def pair_viewer(request: Request, body: dict[str, Any]) -> Response:
        if (
            viewer is None
            or not local_request(request)
            or request.headers.get("origin") != local_origin
        ):
            raise HTTPException(403, "Local viewer pairing unavailable")
        if body.get("consent") is not True:
            raise HTTPException(422, "Explicit viewer consent required")
        response = Response(status_code=204, headers={"Cache-Control": "no-store"})
        if not viewer.valid(request.cookies.get("rohai_viewer", "")):
            response.set_cookie(
                "rohai_viewer",
                viewer.issue(),
                httponly=True,
                samesite="strict",
                max_age=7 * 86400,
                path="/api/maket",
            )
        return response

    @app.get("/api/maket/permissions", dependencies=[Depends(auth)])
    def permissions(request: Request) -> dict[str, Any]:
        return {
            "persistent_viewer": persistent_viewer,
            "setup_authorized": privileged(request, request.headers.get("x-operator-token", "")),
            "viewer_expires_at": viewer.expiry if viewer else None,
            "physical_control_persisted": False,
        }

    @app.delete("/api/maket/pair-viewer", dependencies=[Depends(auth)])
    async def revoke_viewer(request: Request) -> Response:
        if privileged(request, request.headers.get("x-operator-token", "")):
            await holder["runtime"].emergency_stop("local_permission_revoked")
        if viewer is not None:
            viewer.revoke()
        response = Response(status_code=204)
        response.delete_cookie("rohai_viewer", path="/api/maket")
        response.delete_cookie("rohai_local", path="/api/maket")
        return response

    @app.get("/health/live")
    def live() -> dict[str, Any]:
        return {
            "application": "rohai-maket",
            "backend_ready": bool(holder),
            "workspace": hashlib.sha256(str(Path.cwd().resolve()).encode()).hexdigest()[:16],
        }

    @app.post("/api/maket/shutdown", dependencies=[Depends(auth)])
    async def shutdown() -> dict[str, str]:
        await holder["runtime"].emergency_stop("launcher_shutdown")
        callback = app.state.request_shutdown
        if callback is None:
            raise HTTPException(409, "Shutdown is managed by server process")
        callback()
        return {"status": "SHUTDOWN_REQUESTED"}

    @app.get("/api/maket/state", dependencies=[Depends(auth)])
    async def state() -> dict[str, Any]:
        return holder["runtime"].snapshot()

    @app.get("/api/maket/config", dependencies=[Depends(auth)])
    async def configuration() -> dict[str, Any]:
        return holder["runtime"].config.model_dump(mode="json")

    @app.get("/api/maket/view-config", dependencies=[Depends(auth)])
    def view_config() -> dict[str, Any]:
        data = holder["runtime"].config.model_dump(mode="json")
        data["camera"]["uri"] = ""  # Persistent viewer never receives RTSP credentials.
        return data

    @app.post("/api/maket/autosetup", dependencies=[Depends(auth)])
    async def autosetup() -> dict[str, str]:
        nonlocal setup_task
        if operation_lock.locked() or (setup_task is not None and not setup_task.done()):
            return {"status": "ALREADY_RUNNING"}
        if holder["runtime"].running or holder["runtime"].demo_running:
            raise HTTPException(409, "Stop before autosetup")

        async def run_setup() -> None:
            from smart_traffic_backend.maket.setup import issue, prepare

            async with operation_lock:
                runtime = holder["runtime"]
                try:
                    await prepare(runtime, config_path, runtime.setup_state)
                except Exception:
                    runtime.setup_state.update(state="FAILED", issues=[issue("SETUP_FAILED")])

        setup_task = asyncio.create_task(run_setup(), name="rohai-autosetup")
        return {"status": "STARTED"}

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
            from smart_traffic_backend.maket.calibration import atomic_write

            await asyncio.to_thread(
                atomic_write, config_path, updated.model_dump_json(indent=2).encode()
            )
            await holder["runtime"].stop()
            holder["runtime"] = MaketRuntime(updated)
            await holder["runtime"].start()
            return {"status": "SAVED_AND_RELOADED_STOPPED_SHADOW"}
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/api/maket/geometry", dependencies=[Depends(auth)])
    async def geometry() -> dict[str, Any] | None:
        value = holder["runtime"].overlay_calibration()["geometry"]
        return dict(value) if isinstance(value, dict) else None

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
    async def calibration_suggestion(body: dict[str, Any] | None = None) -> dict[str, Any]:
        from smart_traffic_backend.maket.assisted import project_geometry, suggest_geometry

        r = holder["runtime"]
        capture = r.pipeline.latest_capture
        if r.running or r.demo_running or capture is None:
            raise HTTPException(409, "Stop control and open live camera first")
        if time.monotonic() - capture.captured_at > 1.5:
            raise HTTPException(409, "Fresh camera frame required")
        try:
            if body is not None and "corners" in body:
                result = await asyncio.to_thread(
                    project_geometry, capture.frame, r.config.camera.source_id, body["corners"]
                )
            else:
                result = await asyncio.to_thread(
                    suggest_geometry, capture.frame, r.config.camera.source_id
                )
            return {
                **result,
                "frame_id": capture.frame_id,
                "stream_epoch": capture.stream_epoch,
                "captured_at_monotonic": capture.captured_at,
                "source_id": capture.source_id,
            }
        except (ValueError, KeyError, TypeError) as error:
            raise HTTPException(422, str(error)) from error

    async def execute_control(body: ControlRequest) -> dict[str, Any]:
        r = holder["runtime"]
        try:
            if body.action in ("start", "camera_only_start", "hardware_test_start"):
                if time.monotonic() >= session_expires:
                    raise ValueError("Local session expired; restart launcher")
                r.physical_permission_until = time.monotonic() + (
                    session_expires - time.monotonic()
                )
            if body.action in ("stop", "estop"):
                await r.emergency_stop()
            elif body.action == "start":
                if not body.confirm_physical:
                    raise ValueError("Explicit physical-control confirmation required")
                r.physical_permission_until = time.monotonic() + max(
                    0, session_expires - time.monotonic()
                )
                await r.arm()
            elif body.action == "camera_only_start":
                await r.start_camera_only()
            elif body.action == "hardware_test_start":
                if not body.confirm_physical:
                    raise ValueError("Explicit physical-control confirmation required")
                r.physical_permission_until = time.monotonic() + max(
                    0, session_expires - time.monotonic()
                )
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
                if not r.recorder.enabled or r.recorder.error:
                    raise ValueError(
                        "Recording storage full; review historical sessions before recording"
                    )
                r.recorder.record_video = not r.recorder.record_video
                r.recorder.submit(
                    "recording_privacy",
                    {
                        "video_enabled": r.recorder.record_video,
                        "retention_days": r.config.retention_days,
                        "automatic_deletion": False,
                    },
                )
            elif body.action == "reset":
                if r.running or r.demo_running:
                    raise ValueError("Stop before reset")
                await r.stop()
                holder["runtime"] = MaketRuntime(r.config)
                await holder["runtime"].start()
                r = holder["runtime"]
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
            from smart_traffic_backend.maket.calibration import camera_signature, save_calibration

            body["camera_signature"] = camera_signature(current_config.camera)
            c = MaketCalibration.model_validate(body)
            h, w = frame.frame.shape[:2]
            if c.geometry.source_id != current_config.camera.source_id or (w, h) != (
                c.geometry.reference_width,
                c.geometry.reference_height,
            ):
                raise ValueError("Calibration camera/resolution mismatch")

            await asyncio.to_thread(save_calibration, current_config, c, data)
            await r.emergency_stop("calibration_changed")
            await r.reconnect_camera()
            r.revalidation_required = False  # Operator verified this new live reference.
            return {"status": "SAVED_CAMERA_RESTARTED_STOPPED_SHADOW"}
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    preview_task: asyncio.Task[bytes] | None = None
    preview_key: float | None = None

    @app.get("/api/maket/frame", dependencies=[Depends(auth)])
    async def frame(analyzed: bool = False) -> Response:
        nonlocal preview_task, preview_key
        r = holder["runtime"]
        current = r.pipeline.latest_capture
        candidate = r.pipeline.latest_analysis
        if (
            analyzed
            and candidate is not None
            and time.monotonic() - candidate.captured_at <= r.config.camera.max_frame_age_seconds
            and current is not None
            and candidate.video.stream_epoch == current.stream_epoch
        ):
            current = candidate.video
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
                "X-Frame-Id": str(current.frame_id),
                "X-Stream-Epoch": str(current.stream_epoch),
                "X-Captured-At-Monotonic": str(current.captured_at),
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

    @app.get("/api/maket/sessions", dependencies=[Depends(auth)])
    async def sessions() -> list[dict[str, Any]]:
        from smart_traffic_backend.maket.history import list_sessions

        runtime = holder["runtime"]
        return await asyncio.to_thread(
            list_sessions, runtime.config.session_directory, runtime.config.retention_days
        )

    @app.get("/api/maket/replay/{session_id}/timeline", dependencies=[Depends(auth)])
    async def replay_timeline(
        session_id: str,
        offset: int = Query(default=0, ge=0, le=1000000),
        limit: int = Query(default=200, ge=1, le=500),
    ) -> dict[str, Any]:
        from smart_traffic_backend.maket.history import timeline

        try:
            return await asyncio.to_thread(
                timeline, holder["runtime"].config.session_directory, session_id, offset, limit
            )
        except (ValueError, OSError, KeyError, TypeError) as error:
            raise HTTPException(
                422, "Recording is unavailable or its event order is invalid"
            ) from error

    @app.get("/api/maket/replay/{session_id}/frame/{index}", dependencies=[Depends(auth)])
    async def replay_frame(session_id: str, index: int) -> Response:
        from smart_traffic_backend.maket.history import asset, session_path

        if index < 0:
            raise HTTPException(422, "Nonnegative frame index required")

        def read() -> bytes:
            directory = session_path(holder["runtime"].config.session_directory, session_id)
            capture = cv2.VideoCapture(str(asset(directory, "video.avi")))
            try:
                capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                ok, image = capture.read()
                if not ok:
                    raise ValueError("Recorded video frame unavailable")
                ok, data = cv2.imencode(".jpg", image)
                if not ok:
                    raise ValueError("Recorded frame encoding failed")
                return data.tobytes()
            finally:
                capture.release()

        try:
            return Response(
                await asyncio.to_thread(read),
                media_type="image/jpeg",
                headers={"Cache-Control": "no-store"},
            )
        except (ValueError, OSError) as error:
            raise HTTPException(422, "Recorded video frame unavailable") from error

    @app.delete(
        "/api/maket/sessions/{session_id}",
        dependencies=[Depends(auth), Depends(exclusive_operation)],
    )
    async def delete_session(session_id: str, body: dict[str, Any]) -> dict[str, str]:
        from smart_traffic_backend.maket.history import delete_closed_session

        runtime = holder["runtime"]
        try:
            await asyncio.to_thread(
                delete_closed_session,
                runtime.config.session_directory,
                session_id,
                confirmed=body.get("confirm_delete") is True,
                active_id=runtime.recorder.id,
            )
        except (ValueError, OSError) as error:
            raise HTTPException(409, str(error)) from error
        return {"status": "DELETED_SELECTED_SESSION"}

    @app.get("/maket/replay/{session_id}")
    def replay_page(session_id: str) -> FileResponse:
        if len(session_id) != 32 or any(c not in "0123456789abcdef" for c in session_id):
            raise HTTPException(404, "Recording unavailable")
        return FileResponse("apps/maket-ui/replay.html", headers={"Cache-Control": "no-store"})

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

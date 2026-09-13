import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import Field
from traffic_core.models import Direction, Model, Telemetry, phase_for
from traffic_core.ports import HardwareController
from traffic_simulator.comparison import ComparisonRequest, ComparisonResult, compare
from traffic_vision.geometry import GeometryStore, IntersectionGeometry
from traffic_vision.models import PipelineStatus, VisionTrafficState

from smart_traffic_backend.config import Settings
from smart_traffic_backend.hardware import MockHardwareController
from smart_traffic_backend.runtime import Runtime
from smart_traffic_backend.vision_runtime import VisionRuntime, VisionTelemetry


class EmergencyRequest(Model):
    direction: Direction
    ttl_seconds: float = Field(default=30, ge=1, le=120)


class EmergencyResponse(Model):
    status: str
    expires_at_simulation_seconds: float


def get_runtime(request: Request) -> Runtime | VisionRuntime:
    runtime: Runtime | VisionRuntime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime | VisionRuntime, Depends(get_runtime)]


def require_operator(
    runtime: RuntimeDep, x_operator_token: Annotated[str | None, Header()] = None
) -> None:
    expected = runtime.settings.operator_token
    if expected is not None and not secrets.compare_digest(
        (x_operator_token or "").encode(), expected.get_secret_value().encode()
    ):
        raise HTTPException(status_code=401, detail="Invalid operator token")


def create_app(
    settings: Settings | None = None, hardware: HardwareController | None = None
) -> FastAPI:
    config = settings if settings is not None else Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logging.basicConfig(
            level=config.log_level, format="%(asctime)s %(levelname)s %(name)s %(message)s"
        )
        adapter = hardware if hardware is not None else MockHardwareController()
        runtime = (
            Runtime(config, adapter)
            if config.mode == "simulation"
            else VisionRuntime(config, adapter)
        )
        app.state.runtime = runtime
        try:
            await runtime.start()
            yield
        finally:
            await runtime.stop()

    app = FastAPI(title="Smart Traffic AI", version="0.1.0", lifespan=lifespan)

    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready", tags=["health"])
    def ready(runtime: RuntimeDep) -> JSONResponse:
        healthy = runtime.ready
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={
                "status": "ready" if healthy else "not_ready",
                "mode": config.mode,
                "hardware": type(runtime.hardware).__name__,
                "failure": runtime.failure,
            },
        )

    @app.get("/api/v1/state", response_model=Telemetry | VisionTelemetry, tags=["telemetry"])
    async def state(runtime: RuntimeDep) -> Telemetry | VisionTelemetry:
        return runtime.latest

    @app.post(
        "/api/v1/emergency",
        response_model=EmergencyResponse,
        dependencies=[Depends(require_operator)],
        tags=["control"],
    )
    async def emergency(body: EmergencyRequest, runtime: RuntimeDep) -> EmergencyResponse:
        if config.mode == "calibration":
            raise HTTPException(status_code=409, detail="Control is disabled during calibration")
        if not runtime.ready:
            raise HTTPException(status_code=503, detail="Controller is not ready")
        runtime.engine.set_emergency(phase_for(body.direction), body.ttl_seconds)
        logging.getLogger(__name__).warning(
            "Emergency priority requested: direction=%s ttl=%s", body.direction, body.ttl_seconds
        )
        return EmergencyResponse(
            status="accepted", expires_at_simulation_seconds=runtime.engine.emergency_until
        )

    @app.delete("/api/v1/emergency", dependencies=[Depends(require_operator)], tags=["control"])
    async def cancel_emergency(runtime: RuntimeDep) -> dict[str, str]:
        runtime.engine.clear_emergency()
        return {"status": "cleared"}

    @app.post(
        "/api/v1/simulation/compare",
        response_model=ComparisonResult,
        dependencies=[Depends(require_operator)],
        tags=["simulation"],
    )
    def comparison(body: ComparisonRequest, runtime: RuntimeDep) -> ComparisonResult:
        if not runtime.comparison_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="A comparison is already running")
        try:
            return compare(body, config.timing)
        finally:
            runtime.comparison_lock.release()

    @app.websocket("/ws/telemetry")
    async def telemetry(websocket: WebSocket) -> None:
        runtime: Runtime | VisionRuntime = websocket.app.state.runtime
        await websocket.accept()
        events = runtime.stream()
        try:
            while True:
                try:
                    async with asyncio.timeout(config.stale_after_seconds):
                        snapshot = await anext(events)
                        await websocket.send_json(snapshot.model_dump(mode="json"))
                except TimeoutError:
                    await websocket.close(code=1013, reason="Telemetry timeout")
                    break
                if runtime.failure is not None:
                    await websocket.close(code=1011, reason="Controller failed")
                    break
        except WebSocketDisconnect:
            pass
        finally:
            await events.aclose()

    @app.get("/api/v1/vision/status", response_model=PipelineStatus, tags=["vision"])
    async def vision_status(runtime: RuntimeDep) -> PipelineStatus:
        if not isinstance(runtime, VisionRuntime):
            raise HTTPException(status_code=409, detail="Vision mode is not active")
        return runtime.pipeline.status()

    @app.get("/api/v1/vision/state", response_model=VisionTrafficState, tags=["vision"])
    async def vision_state(runtime: RuntimeDep) -> VisionTrafficState:
        if not isinstance(runtime, VisionRuntime) or not runtime.ready:
            raise HTTPException(status_code=503, detail="No fresh vision state")
        latest = runtime.pipeline.latest
        assert latest is not None
        return latest.analysis.traffic

    @app.get("/api/v1/vision/frame.jpg", tags=["vision"])
    async def vision_frame(runtime: RuntimeDep) -> Response:
        if not isinstance(runtime, VisionRuntime) or not runtime.ready:
            raise HTTPException(status_code=503, detail="No fresh vision frame")
        latest = runtime.pipeline.latest
        assert latest is not None
        return Response(latest.jpeg, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/v1/vision/stream", tags=["vision"])
    async def vision_stream(request: Request, runtime: RuntimeDep) -> StreamingResponse:
        if not isinstance(runtime, VisionRuntime) or not runtime.ready:
            raise HTTPException(status_code=503, detail="No fresh vision stream")

        async def frames() -> AsyncIterator[bytes]:
            previous = -1.0
            while runtime.ready and not await request.is_disconnected():
                current = runtime.pipeline.latest
                if current is not None and current.published_at != previous:
                    previous = current.published_at
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + current.jpeg + b"\r\n"
                await asyncio.sleep(config.tick_seconds)
            if runtime.failure is not None:
                # Wait for the control task to publish its terminal all-red image.
                if runtime.task is not None:
                    with suppress(asyncio.TimeoutError):
                        await asyncio.wait_for(asyncio.shield(runtime.task), timeout=1)
                terminal = runtime.pipeline.latest
                if terminal is not None:
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + terminal.jpeg + b"\r\n"

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get(
        "/api/v1/vision/geometry/{source_id}",
        response_model=IntersectionGeometry,
        tags=["calibration"],
    )
    def geometry_get(source_id: str) -> IntersectionGeometry:
        try:
            return GeometryStore(config.vision.calibration_directory).load(source_id)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Camera geometry not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put(
        "/api/v1/vision/geometry/{source_id}",
        dependencies=[Depends(require_operator)],
        tags=["calibration"],
    )
    def geometry_put(source_id: str, body: IntersectionGeometry) -> dict[str, str]:
        if source_id != body.source_id:
            raise HTTPException(status_code=422, detail="Camera IDs do not match")
        if config.mode == "vision" and source_id == config.vision.source_id:
            raise HTTPException(
                status_code=409,
                detail="Use calibration mode before changing the active camera geometry",
            )
        GeometryStore(config.vision.calibration_directory).save(body)
        return {"status": "saved", "applies_on": "pipeline_restart"}

    return app


app = create_app()

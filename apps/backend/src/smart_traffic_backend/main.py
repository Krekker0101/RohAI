import asyncio
import logging
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import Field
from traffic_core.models import Direction, Model, Telemetry, phase_for
from traffic_core.ports import HardwareController
from traffic_simulator.comparison import ComparisonRequest, ComparisonResult, compare

from smart_traffic_backend.config import Settings
from smart_traffic_backend.hardware import MockHardwareController
from smart_traffic_backend.runtime import Runtime


class EmergencyRequest(Model):
    direction: Direction
    ttl_seconds: float = Field(default=30, ge=1, le=120)


class EmergencyResponse(Model):
    status: str
    expires_at_simulation_seconds: float


def get_runtime(request: Request) -> Runtime:
    runtime: Runtime = request.app.state.runtime
    return runtime


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]


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
        runtime = Runtime(config, hardware if hardware is not None else MockHardwareController())
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

    @app.get("/api/v1/state", response_model=Telemetry, tags=["telemetry"])
    async def state(runtime: RuntimeDep) -> Telemetry:
        return runtime.latest

    @app.post(
        "/api/v1/emergency",
        response_model=EmergencyResponse,
        dependencies=[Depends(require_operator)],
        tags=["control"],
    )
    async def emergency(body: EmergencyRequest, runtime: RuntimeDep) -> EmergencyResponse:
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
        runtime: Runtime = websocket.app.state.runtime
        await websocket.accept()
        queue: asyncio.Queue[Telemetry] = asyncio.Queue(maxsize=1)
        runtime.subscribers.add(queue)
        queue.put_nowait(runtime.latest)
        try:
            while True:
                try:
                    async with asyncio.timeout(config.stale_after_seconds):
                        snapshot = await queue.get()
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
            runtime.subscribers.discard(queue)

    return app


app = create_app()

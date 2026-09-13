import asyncio
import time

from fastapi.testclient import TestClient
from pydantic import SecretStr
from smart_traffic_backend.config import Settings
from smart_traffic_backend.hardware import MockHardwareController
from smart_traffic_backend.main import create_app
from smart_traffic_backend.runtime import Runtime
from traffic_core.models import SignalStage, SignalState, Telemetry


def test_health_telemetry_websocket_and_shutdown() -> None:
    hardware = MockHardwareController()
    app = create_app(Settings(tick_seconds=0.05), hardware)
    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 200
        Telemetry.model_validate(client.get("/api/v1/state").json())
        with client.websocket_connect("/ws/telemetry") as socket:
            first = Telemetry.model_validate(socket.receive_json())
            second = Telemetry.model_validate(socket.receive_json())
            assert second.sequence > first.sequence
        assert client.get("/openapi.json").status_code == 200
    assert hardware.closed
    assert hardware.last_signal.stage == SignalStage.ALL_RED


def test_operator_auth_validation_and_comparison() -> None:
    app = create_app(Settings(operator_token=SecretStr("test-secret")))
    with TestClient(app) as client:
        body = {"direction": "east", "ttl_seconds": 15}
        assert client.post("/api/v1/emergency", json=body).status_code == 401
        headers = {"X-Operator-Token": "test-secret"}
        assert client.post("/api/v1/emergency", json=body, headers=headers).status_code == 200
        assert (
            client.post(
                "/api/v1/emergency", json={"direction": "invalid"}, headers=headers
            ).status_code
            == 422
        )
        assert client.delete("/api/v1/emergency", headers=headers).status_code == 200
        response = client.post(
            "/api/v1/simulation/compare", json={"duration_seconds": 30}, headers=headers
        )
        assert response.status_code == 200
        data = response.json()
        assert data["fixed"]["arrived"] == data["adaptive"]["arrived"]


class FailingHardware(MockHardwareController):
    async def apply(self, signals: SignalState) -> None:
        if signals.stage != SignalStage.ALL_RED:
            raise OSError("Disconnected")
        await super().apply(signals)


def test_runtime_fault_requests_all_red_and_readiness_fails() -> None:
    async def exercise() -> None:
        hardware = FailingHardware()
        runtime = Runtime(Settings(tick_seconds=0.05), hardware)
        # Reach initial green on the first background tick.
        runtime.engine.simulator.now = 2
        await runtime.start()
        assert runtime.task is not None
        await asyncio.wait_for(runtime.task, timeout=2)
        assert not runtime.ready
        assert runtime.failure == "runtime_failure"
        assert runtime.latest.signals.stage == SignalStage.ALL_RED
        assert hardware.last_signal.stage == SignalStage.ALL_RED
        await runtime.stop()

    asyncio.run(exercise())


def test_stale_runtime_fails_safe_and_slow_subscriber_is_bounded() -> None:
    async def exercise() -> None:
        runtime = Runtime(Settings(tick_seconds=0.05), MockHardwareController())
        queue: asyncio.Queue[Telemetry] = asyncio.Queue(maxsize=1)
        runtime.subscribers.add(queue)
        runtime._publish()
        runtime._publish()
        assert queue.qsize() == 1
        await runtime.start()
        runtime.last_tick = time.monotonic() - 10
        assert runtime.task is not None
        await asyncio.wait_for(runtime.task, timeout=2)
        assert runtime.failure is not None
        assert not runtime.ready
        assert runtime.latest.signals.stage == SignalStage.ALL_RED
        await runtime.stop()

    asyncio.run(exercise())


class HangingHardware(MockHardwareController):
    async def apply(self, signals: SignalState) -> None:
        if signals.stage != SignalStage.ALL_RED:
            await asyncio.sleep(10)
        await super().apply(signals)


def test_hardware_timeout_is_bounded_and_latches_fault() -> None:
    async def exercise() -> None:
        hardware = HangingHardware()
        runtime = Runtime(Settings(tick_seconds=0.05, hardware_timeout_seconds=0.05), hardware)
        runtime.engine.simulator.now = 2
        await runtime.start()
        assert runtime.task is not None
        await asyncio.wait_for(runtime.task, timeout=2)
        assert runtime.failure is not None
        assert hardware.last_signal.stage == SignalStage.ALL_RED
        await runtime.stop()

    asyncio.run(exercise())


def test_failed_readiness_keeps_liveness_and_rejects_emergency() -> None:
    app = create_app(Settings())
    with TestClient(app) as client:
        runtime: Runtime = app.state.runtime
        runtime.failure = "test_failure"
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        assert client.post("/api/v1/emergency", json={"direction": "north"}).status_code == 503

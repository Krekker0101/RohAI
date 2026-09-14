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
        demo_response = client.post(
            "/api/v1/demo/simulation/compare", json={"duration_seconds": 30}
        )
        assert demo_response.status_code == 200


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


def test_cors_allows_only_configured_dashboard_origin() -> None:
    origin = "https://dashboard.example.com"
    app = create_app(Settings(cors_origins=(origin,)))
    with TestClient(app) as client:
        allowed = client.options(
            "/api/v1/system",
            headers={"Origin": origin, "Access-Control-Request-Method": "GET"},
        )
        assert allowed.status_code == 200
        assert allowed.headers["access-control-allow-origin"] == origin

        denied = client.options(
            "/api/v1/system",
            headers={"Origin": "https://wrong.example.com", "Access-Control-Request-Method": "GET"},
        )
        assert denied.status_code == 400
        assert "access-control-allow-origin" not in denied.headers


def test_presentation_demo_is_ready_and_isolated_from_real_runtime() -> None:
    app = create_app(Settings(tick_seconds=0.05))
    with TestClient(app) as client:
        demo_system = client.get("/api/v1/demo/system")
        assert demo_system.status_code == 200
        assert demo_system.json()["mode"] == "demo"
        assert demo_system.json()["demo"] is True
        assert client.get("/health/demo-ready").status_code == 200

        initial_real_policy = client.get("/api/v1/system").json()["policy"]
        policy_response = client.put("/api/v1/demo/control/policy", json={"policy": "fixed"})
        assert policy_response.status_code == 200
        assert client.get("/api/v1/demo/system").json()["policy"] == "fixed"
        assert client.get("/api/v1/system").json()["policy"] == initial_real_policy

        comparison = client.post(
            "/api/v1/demo/simulation/compare",
            json={"duration_seconds": 30, "scenario": {"seed": 7}},
        )
        assert comparison.status_code == 200
        assert comparison.json()["fixed"]["arrived"] == comparison.json()["adaptive"]["arrived"]

        state = client.get("/api/v1/demo/state")
        assert state.status_code == 200
        payload = state.json()
        assert payload["schema_version"] == "2.0-vision"
        assert payload["mode"] == "demo"
        assert payload["traffic"]["source_id"] == "demo"
        assert payload["pipeline"]["state"] == "running"

        with client.websocket_connect("/ws/demo/telemetry") as socket:
            first = socket.receive_json()
            second = socket.receive_json()
            assert second["sequence"] >= first["sequence"]
            assert second["mode"] == "demo"

        emergency = client.post(
            "/api/v1/demo/emergency", json={"direction": "east", "ttl_seconds": 10}
        )
        assert emergency.status_code == 200
        assert client.get("/api/v1/demo/state").json()["emergency_phase"] == "east_west"
        assert client.delete("/api/v1/demo/emergency").status_code == 200

        frame = client.get("/api/v1/demo/vision/frame.svg")
        assert frame.status_code == 200
        assert frame.headers["content-type"].startswith("image/svg+xml")
        assert "PRESENTATION DEMO" in frame.text

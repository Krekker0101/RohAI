from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr
from smart_traffic_backend.config import Settings
from smart_traffic_backend.main import create_app


def test_dashboard_serves_only_distribution_and_system_hides_token(tmp_path: Path) -> None:
    distribution = tmp_path / "dist"
    distribution.mkdir()
    (distribution / "index.html").write_text("<html>React dashboard</html>", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("private", encoding="utf-8")
    app = create_app(
        Settings(dashboard_directory=distribution, operator_token=SecretStr("private-token"))
    )
    with TestClient(app) as client:
        assert "React dashboard" in client.get("/").text
        assert client.get("/dashboard/%2e%2e/secret.txt").status_code == 404
        response = client.get("/api/v1/system")
        assert response.status_code == 200
        assert response.json()["operator_auth_required"] is True
        assert response.json()["hardware"] == "MockHardwareController"
        assert "private-token" not in response.text


def test_policy_change_is_authenticated_and_preserves_safety_state() -> None:
    app = create_app(Settings(operator_token=SecretStr("test-token"), tick_seconds=1))
    with TestClient(app) as client:
        body = {"policy": "fixed"}
        assert client.put("/api/v1/control/policy", json=body).status_code == 401
        before = app.state.runtime.engine.safety.snapshot(0)
        headers = {"X-Operator-Token": "test-token"}
        assert client.put("/api/v1/control/policy", json=body, headers=headers).status_code == 200
        assert app.state.runtime.engine.controller.policy.value == "fixed"
        assert app.state.runtime.engine.safety.snapshot(0) == before
        assert client.get("/api/v1/system").json()["policy"] == "fixed"
        assert (
            client.put(
                "/api/v1/control/policy", json={"policy": "invalid"}, headers=headers
            ).status_code
            == 422
        )
        app.state.runtime.failure = "test_failure"
        assert client.put("/api/v1/control/policy", json=body, headers=headers).status_code == 503

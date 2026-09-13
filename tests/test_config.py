import pytest
from pydantic import ValidationError
from smart_traffic_backend.config import Settings
from traffic_vision.contracts import BoundingBox


def test_nested_environment_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STA_SCENARIO__SEED", "99")
    monkeypatch.setenv("STA_TIMING__MAX_GREEN_SECONDS", "50")
    config = Settings()
    assert config.scenario.seed == 99
    assert config.timing.max_green_seconds == 50


def test_invalid_mode_and_empty_token_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STA_MODE", "camera")
    with pytest.raises(ValidationError):
        Settings()
    monkeypatch.setenv("STA_MODE", "simulation")
    monkeypatch.setenv("STA_OPERATOR_TOKEN", "")
    with pytest.raises(ValidationError):
        Settings()


def test_vision_contract_rejects_inverted_box() -> None:
    with pytest.raises(ValidationError):
        BoundingBox(x1=10, y1=10, x2=5, y2=20)

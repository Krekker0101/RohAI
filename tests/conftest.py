import os

import pytest
from smart_traffic_backend.config import Settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Developer camera settings must not change deterministic test scenarios."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in os.environ:
        if name.startswith("STA_"):
            monkeypatch.delenv(name)

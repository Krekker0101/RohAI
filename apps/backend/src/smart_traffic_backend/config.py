from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from traffic_core.models import Policy
from traffic_core.settings import Timing
from traffic_simulator.simulator import Scenario
from traffic_vision.settings import VisionSettings


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="STA_", env_nested_delimiter="__", env_file=".env", extra="forbid"
    )
    mode: Literal["simulation", "vision", "calibration"] = "simulation"
    policy: Policy = Policy.ADAPTIVE
    tick_seconds: float = Field(default=0.5, ge=0.05, le=1, allow_inf_nan=False)
    stale_after_seconds: float = Field(default=3, ge=2, le=30, allow_inf_nan=False)
    hardware_timeout_seconds: float = Field(default=1, gt=0, le=2, allow_inf_nan=False)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    operator_token: SecretStr | None = None
    timing: Timing = Field(default_factory=Timing)
    scenario: Scenario = Field(default_factory=Scenario)
    vision: VisionSettings = Field(default_factory=VisionSettings)

    @field_validator("operator_token")
    @classmethod
    def nonempty_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("Operator token cannot be empty")
        return value

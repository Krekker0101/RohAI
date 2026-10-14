from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from traffic_core.models import Policy
from traffic_core.settings import Timing
from traffic_simulator.simulator import Scenario
from traffic_vision.settings import AerialSettings, VisionSettings

from smart_traffic_backend.pilot_config import PilotSettings


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="STA_", env_nested_delimiter="__", env_file=".env", extra="forbid"
    )
    pilot: PilotSettings = Field(default_factory=PilotSettings)
    mode: Literal["simulation", "vision", "calibration"] = "simulation"
    hardware_mode: Literal["mock", "replay"] = "mock"
    policy: Policy = Policy.ADAPTIVE
    tick_seconds: float = Field(default=0.5, ge=0.05, le=1, allow_inf_nan=False)
    stale_after_seconds: float = Field(default=3, ge=2, le=30, allow_inf_nan=False)
    hardware_timeout_seconds: float = Field(default=1, gt=0, le=2, allow_inf_nan=False)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    operator_token: SecretStr | None = None
    cors_origins: tuple[str, ...] = ()
    dashboard_directory: Path = Path("apps/dashboard/dist")
    improvement_log_path: Path = Path(".runtime/improvement/decisions.sqlite3")
    ai_artifact_directory: Path = Path("artifacts/rohai-ai-2.0/stage3")
    vision_model_version: str = "UNVERIFIED_LOCAL_MODEL"
    controller_policy_version: str = "deterministic-baseline-v1"
    operations_enabled: bool = False
    operations_database: Path = Path(".runtime/pilot/observations.sqlite3")
    operations_retention_days: int = Field(default=30, ge=1, le=30)
    operations_site_id: str = Field(default="local", pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    operations_viewer_token: SecretStr | None = None
    video_jobs_directory: Path = Path(".runtime/video-analysis")
    video_road_model_directory: Path = Path("datasets/models/road-segformer-city")
    video_road_manifest_path: Path = Path("configs/models/road-segformer-city.json")
    video_aerial_model_path: Path = Path("datasets/models/yolo26s.pt")
    video_aerial_manifest_path: Path = Path("configs/models/yolo26s.json")
    video_aerial: AerialSettings = Field(default_factory=AerialSettings)
    video_cpu_threads: int = Field(default=0, ge=0, le=32)
    video_max_upload_mb: int = Field(default=256, ge=1, le=512)
    video_max_jobs: int = Field(default=10, ge=1, le=20)
    video_max_frames: int = Field(default=100000, ge=1, le=200000)
    video_max_duration_seconds: int = Field(default=1800, ge=1, le=3600)
    timing: Timing = Field(default_factory=Timing)
    scenario: Scenario = Field(default_factory=Scenario)
    vision: VisionSettings = Field(default_factory=VisionSettings)

    @field_validator("operator_token")
    @classmethod
    def nonempty_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            raise ValueError("Operator token cannot be empty")
        return value

    @model_validator(mode="after")
    def pilot_authentication(self) -> "Settings":
        if self.operations_enabled and (
            self.operator_token is None or len(self.operator_token.get_secret_value()) < 16
        ):
            raise ValueError("Pilot operations require an operator token of at least 16 characters")
        if self.operations_viewer_token is not None:
            if len(self.operations_viewer_token.get_secret_value()) < 16:
                raise ValueError("Viewer token must contain at least 16 characters")
            if self.operator_token == self.operations_viewer_token:
                raise ValueError("Viewer and operator tokens must differ")
        return self

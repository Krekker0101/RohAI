from pydantic import Field, model_validator

from traffic_core.models import Model


class Timing(Model):
    min_green_seconds: float = Field(default=8, ge=1, le=120)
    max_green_seconds: float = Field(default=45, ge=1, le=180)
    fixed_green_seconds: float = Field(default=20, ge=1, le=180)
    yellow_seconds: float = Field(default=3, ge=1, le=10)
    all_red_seconds: float = Field(default=2, ge=1, le=15)
    pedestrian_green_seconds: float = Field(default=10, ge=1, le=120)
    pedestrian_clearance_seconds: float = Field(default=5, ge=1, le=60)
    seconds_per_score: float = Field(default=1.8, gt=0)
    waiting_score_per_second: float = Field(default=0.05, ge=0)

    @model_validator(mode="after")
    def ordered(self) -> "Timing":
        if not self.min_green_seconds <= self.fixed_green_seconds <= self.max_green_seconds:
            raise ValueError("Require min_green <= fixed_green <= max_green")
        if not self.min_green_seconds <= self.pedestrian_green_seconds <= self.max_green_seconds:
            raise ValueError("Pedestrian green must be within green limits")
        return self

"""Optional causal arrival baselines; measured history only, never simulator future tape."""

from collections import deque
from typing import Literal

import numpy as np
from pydantic import Field
from traffic_core.models import Model


class ArrivalSample(Model):
    timestamp_s: float = Field(ge=0)
    rate_per_minute: float = Field(ge=0)


class FlowForecast(Model):
    issued_at_s: float
    horizon_s: float
    rate_per_minute: float
    lower_rate: float
    upper_rate: float
    method: str
    history_samples: int
    uncertainty_basis: str = (
        "Descriptive rolling residual spread; not calibrated confidence coverage"
    )


class ArrivalForecaster:
    def __init__(
        self,
        method: Literal["moving_average", "exponential", "historical"] = "moving_average",
        window: int = 32,
        alpha: float = 0.3,
    ) -> None:
        if (
            method not in ("moving_average", "exponential", "historical")
            or not 3 <= window <= 256
            or not 0 < alpha <= 1
        ):
            raise ValueError("Invalid forecast configuration")
        self.method, self.alpha = method, alpha
        self.history: deque[ArrivalSample] = deque(maxlen=window)

    def add(self, sample: ArrivalSample) -> None:
        if self.history and sample.timestamp_s <= self.history[-1].timestamp_s:
            raise ValueError("Forecast history must increase strictly")
        self.history.append(sample)

    def predict(self, now_s: float, horizon_s: float = 30) -> FlowForecast | None:
        if not 0 < horizon_s <= 120:
            raise ValueError("Forecast horizon must be within (0,120]")
        if len(self.history) < 3 or not 0 <= now_s - self.history[-1].timestamp_s <= 60:
            return None
        values = np.asarray([s.rate_per_minute for s in self.history])
        rate = float(values.mean())
        if self.method == "exponential":
            rate = float(values[0])
            for v in values[1:]:
                rate = self.alpha * float(v) + (1 - self.alpha) * rate
        elif self.method == "historical":
            rate = float(np.median(values))
        spread = float(values.std(ddof=1)) * 1.96
        return FlowForecast(
            issued_at_s=now_s,
            horizon_s=horizon_s,
            rate_per_minute=rate,
            lower_rate=max(0, rate - spread),
            upper_rate=rate + spread,
            method=self.method,
            history_samples=len(values),
        )

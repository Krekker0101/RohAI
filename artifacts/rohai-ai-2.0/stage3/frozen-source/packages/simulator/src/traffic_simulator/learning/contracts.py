"""Stable semantic features, masks and phase requests, independent of detector/model IDs."""

from typing import Literal

from pydantic import Field, model_validator
from traffic_core.models import Model, Phase

PHASES = tuple(Phase)
DURATION_LEVELS = (8.0, 20.0, 45.0)


class ControlRequest(Model):
    requested_phase: Phase
    requested_duration_s: float = Field(ge=1, le=180)


def decode_action(index: int) -> ControlRequest:
    if isinstance(index, bool) or not 0 <= index < len(PHASES) * len(DURATION_LEVELS):
        raise ValueError("ActionV1 requires a phase-duration index in [0,8]")
    return ControlRequest(
        requested_phase=PHASES[index // 3], requested_duration_s=DURATION_LEVELS[index % 3]
    )


class ObservedSupplement(Model):
    """Optional production measurements. Missing is unknown, never latent simulator truth."""

    captured_at_s: float = Field(ge=0)
    confidence: float = Field(gt=0, le=1)
    source: str = Field(min_length=1)
    departure_rate_per_minute: float | None = Field(default=None, ge=0)
    downstream_occupancy: float | None = Field(default=None, ge=0, le=1)


class Feature(Model):
    name: str
    unit: str
    scale: float = Field(gt=0)


APPROACH_FEATURES = (
    ("queue_count", "vehicle", 100.0),
    ("queue_length_m", "m", 500.0),
    ("oldest_wait_s", "s", 300.0),
    ("mean_wait_s", "s", 300.0),
    ("arrival_rate", "vehicle/min", 120.0),
    ("departure_rate", "vehicle/min", 120.0),
    ("pedestrian_waiting", "person", 100.0),
    ("pedestrian_oldest_wait_s", "s", 300.0),
    ("time_since_service_s", "s", 600.0),
    ("has_received_service", "bool", 1.0),
    ("downstream_occupancy", "fraction", 1.0),
    ("measurement_age_s", "s", 10.0),
    ("observation_confidence", "fraction", 1.0),
    ("camera_connected", "bool", 1.0),
    ("reliable", "bool", 1.0),
    ("traffic_score", "score", 1000.0),
)


def features() -> tuple[Feature, ...]:
    from traffic_core.models import Direction, ObservationStatus, SignalStage

    result = [
        Feature(name=f"{direction.value}.{name}", unit=unit, scale=scale)
        for direction in Direction
        for name, unit, scale in APPROACH_FEATURES
    ]
    result.extend(
        Feature(name=f"{d.value}.status.{status.value}", unit="bool", scale=1)
        for d in Direction
        for status in ObservationStatus
    )
    result.extend(Feature(name=f"phase.{phase.value}", unit="bool", scale=1) for phase in Phase)
    result.extend(
        Feature(name=f"stage.{stage.value}", unit="bool", scale=1) for stage in SignalStage
    )
    result.extend(Feature(name=f"pending.{phase.value}", unit="bool", scale=1) for phase in Phase)
    result.extend(
        Feature(name=f"last_requested.{phase.value}", unit="bool", scale=1) for phase in Phase
    )
    result.extend(
        (
            Feature(name="phase_age_s", unit="s", scale=180),
            Feature(name="stage_age_s", unit="s", scale=180),
            Feature(name="green_budget_s", unit="s", scale=180),
            Feature(name="pending_budget_s", unit="s", scale=180),
            Feature(name="last_requested_duration_s", unit="s", scale=180),
            Feature(name="last_request_accepted", unit="bool", scale=1),
            Feature(name="pedestrian_time_since_service_s", unit="s", scale=600),
            Feature(name="fault_latched", unit="bool", scale=1),
        )
    )
    return tuple(result)


FEATURES = features()
INDEX = {feature.name: index for index, feature in enumerate(FEATURES)}


class ObservationV1(Model):
    schema_version: Literal["rohai-observation-v1"] = "rohai-observation-v1"
    timestamp_s: float = Field(ge=0)
    values: tuple[float, ...]
    valid: tuple[int, ...]
    clipped: tuple[int, ...]

    @model_validator(mode="after")
    def shape(self) -> "ObservationV1":
        if not len(self.values) == len(self.valid) == len(self.clipped) == len(FEATURES):
            raise ValueError("ObservationV1 feature count mismatch")
        if any(not 0 <= v <= 1 for v in self.values) or any(
            v not in (0, 1) for v in (*self.valid, *self.clipped)
        ):
            raise ValueError("Invalid normalized features/masks")
        if any(
            not mask and value != 0 for mask, value in zip(self.valid, self.values, strict=True)
        ):
            raise ValueError("Unknown features must have zero placeholder and validity=0")
        return self

    def physical(self, name: str) -> float | None:
        index = INDEX[name]
        return self.values[index] * FEATURES[index].scale if self.valid[index] else None

    @property
    def phase(self) -> Phase:
        return next(phase for phase in PHASES if self.physical(f"phase.{phase.value}") == 1)


class RewardSpecification(Model):
    version: Literal["rohai-reward-v1"] = "rohai-reward-v1"
    objective: str = "Reference: minimize accumulated entity delay; not an optimized reward"
    approved_for_training: bool = False
    approval_basis: str | None = None
    # Signs are defined by the specification, not chosen by the policy.
    total_delay: float = Field(default=1, ge=0)
    queue_delay: float = Field(default=0, ge=0)
    pedestrian_delay: float = Field(default=0, ge=0)
    throughput: float = Field(default=0, ge=0)
    maximum_wait: float = Field(default=0, ge=0)
    fairness: float = Field(default=0, ge=0)
    spillback: float = Field(default=0, ge=0)
    switching: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def reviewed(self) -> "RewardSpecification":
        if self.approved_for_training and not self.approval_basis:
            raise ValueError("Training reward requires a documented review basis")
        return self


REWARD_UNITS = {
    "total_delay": "entity*s",
    "queue_delay": "vehicle*s",
    "pedestrian_delay": "person*s",
    "throughput": "entity",
    "maximum_wait": "s^2",
    "fairness": "s^2",
    "spillback": "lane*s",
    "switching": "transition",
}

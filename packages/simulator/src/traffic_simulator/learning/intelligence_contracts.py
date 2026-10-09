"""Camera/UI-independent decision contract; policy proposals never command lamps."""

import math
from typing import Literal, Protocol

from pydantic import Field, model_validator
from traffic_core.models import Direction, Model, Phase, SignalState
from traffic_core.settings import Timing

from traffic_simulator.learning.contracts import ControlRequest, ObservationV1


class ControlGeometry(Model):
    lanes_per_approach: int = Field(default=2, ge=1, le=8)
    lane_length_m: float = Field(default=90, gt=0, le=1000)
    headway_s: float = Field(default=2, ge=0.5, le=10)
    # Known static geometry, not latent arrival rates or simulator queues.


class TrafficObservation(ObservationV1):
    geometry: ControlGeometry = Field(default_factory=ControlGeometry)
    downstream_queue_counts: dict[Direction, float] | None = None
    downstream_captured_at_s: float | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def semantic(self) -> "TrafficObservation":
        for prefix, names in (
            ("phase", tuple(p.value for p in Phase)),
            ("stage", ("green", "yellow", "all_red")),
            ("pending", tuple(p.value for p in Phase)),
        ):
            values = [self.physical(f"{prefix}.{name}") for name in names]
            if any(v not in (0, 1) for v in values) or sum(v or 0 for v in values) != 1:
                raise ValueError(f"Exactly one valid {prefix} required")
        if self.downstream_queue_counts is not None:
            if (
                set(self.downstream_queue_counts) != set(Direction)
                or any(not math.isfinite(v) or v < 0 for v in self.downstream_queue_counts.values())
                or self.downstream_captured_at_s is None
                or not 0 <= self.timestamp_s - self.downstream_captured_at_s <= 3
            ):
                raise ValueError("Downstream counts need complete fresh measured evidence")
        return self


class ControlAction(ControlRequest):
    pass


class PolicyState(Model):
    history: tuple[tuple[float, ...], ...]
    sequence: int = Field(ge=0)

    @model_validator(mode="after")
    def bounded(self) -> "PolicyState":
        from traffic_simulator.learning.contracts import FEATURES

        if not 1 <= len(self.history) <= 32 or any(
            len(row) != 3 * len(FEATURES) + 1
            or any(not math.isfinite(v) or not 0 <= v <= 1 for v in row)
            for row in self.history
        ):
            raise ValueError("History shape/range mismatch")
        return self


class PolicyMetadata(Model):
    policy_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    learned: bool = False
    weights_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    allowed_modes: tuple[Literal["SIMULATION", "SHADOW"], ...] = ("SIMULATION", "SHADOW")


class SafetyConstraints(Model):
    timing: Timing = Field(default_factory=Timing)
    observation_ttl_s: float = Field(default=3, gt=0, le=10)
    minimum_confidence: float = Field(default=0.5, ge=0, le=1)
    inference_timeout_s: float = Field(default=0.1, gt=0, le=2)
    allowed_phases: tuple[Phase, ...] = tuple(Phase)

    @model_validator(mode="after")
    def phases(self) -> "SafetyConstraints":
        if not self.allowed_phases or len(set(self.allowed_phases)) != len(self.allowed_phases):
            raise ValueError("Unique nonempty action phases required")
        return self


class PolicyDecision(Model):
    metadata: PolicyMetadata
    proposed: ControlAction
    available_phases: tuple[Phase, ...]
    facts: dict[str, float | str | None]
    interpretation: str = "No causal explanation of neural internals is asserted"


class DecisionAudit(Model):
    sequence: int
    timestamp_s: float
    observation: TrafficObservation
    proposed: PolicyDecision | None
    requested: ControlAction
    safety_reason: str
    accepted: bool
    signals: SignalState
    effective_phase: Phase
    effective_duration_s: float
    fallback_reason: str | None
    inference_ms: float


class ControlPolicy(Protocol):
    metadata: PolicyMetadata

    def decide(
        self, observation: TrafficObservation, state: PolicyState, constraints: SafetyConstraints
    ) -> PolicyDecision: ...

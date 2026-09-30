from __future__ import annotations

from collections import deque
from enum import StrEnum
from threading import Lock
from time import time
from typing import Literal, Protocol

from pydantic import Field
from traffic_core.hardware_protocol import HardwareFeedback
from traffic_core.models import Model, Phase, SignalState, TrafficState


class PolicyMode(StrEnum):
    DISABLED = "DISABLED"
    SHADOW = "SHADOW"
    ADVISORY = "ADVISORY"
    ACTIVE = "ACTIVE"


class PolicyCandidate(Model):
    phase: Phase
    green_seconds: float = Field(gt=0)


class LearnedPolicyProposer(Protocol):
    async def propose(self, observation: TrafficState) -> PolicyCandidate: ...


class RecoveryState(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAULT = "FAULT"
    SAFE = "SAFE"
    RECOVERING = "RECOVERING"
    READY_FOR_OPERATOR_CONFIRMATION = "READY_FOR_OPERATOR_CONFIRMATION"


class ComponentHealth(Model):
    status: Literal["healthy", "degraded", "fault", "unknown"] = "unknown"
    last_success_at: float | None = Field(default=None, ge=0)
    detail: str | None = None


class OperationalHealth(Model):
    vision: ComponentHealth = Field(default_factory=ComponentHealth)
    analytics: ComponentHealth = Field(default_factory=ComponentHealth)
    controller: ComponentHealth = Field(default_factory=ComponentHealth)
    hardware: ComponentHealth = Field(default_factory=ComponentHealth)
    last_frame_at: float | None = None
    last_inference_at: float | None = None
    last_analytics_update_at: float | None = None
    last_control_tick_at: float | None = None
    last_hardware_ack_at: float | None = None
    physical_production_ready: bool = False


class HardwareStateStages(Model):
    requested: SignalState | None = None
    safety_approved: SignalState | None = None
    command_sent: SignalState | None = None
    hardware_acknowledged: SignalState | None = None
    physically_confirmed: HardwareFeedback | None = None
    command_id: str | None = None
    sequence: int | None = None
    requested_at: float | None = None
    approved_at: float | None = None
    sent_at: float | None = None
    error: str | None = None


def capture_hardware_state(
    hardware: object, requested: SignalState, approved: SignalState, error: str | None = None
) -> HardwareStateStages:
    """Record the five delivery stages as separate facts.

    An adapter without a delivery record (for example ``MockHardwareController``) has no
    correlated ACK and no readback, so only the requested/approved/sent stages are known.
    A send that raised leaves the sent stage unknown instead of assuming it landed, and a
    successful API call is never recorded as physical lamp evidence.
    """
    delivery = getattr(hardware, "delivery", None)
    command = getattr(delivery, "command_sent", None)
    if command is not None:
        sent = approved if command.kind == "ENTER_SAFE" else command.requested_state
    else:
        sent = approved if error is None else None
    captured_at = time()
    return HardwareStateStages(
        requested=requested,
        safety_approved=approved,
        command_sent=sent,
        hardware_acknowledged=getattr(delivery, "acknowledged_state", None),
        physically_confirmed=getattr(delivery, "physically_confirmed", None),
        command_id=getattr(command, "command_id", None),
        sequence=getattr(command, "sequence", None),
        requested_at=captured_at,
        approved_at=captured_at,
        sent_at=getattr(command, "issued_at", None),
        error=error or getattr(delivery, "error", None),
    )


class RecoveryChecks(Model):
    source: bool = False
    calibration: bool = False
    freshness: bool = False
    analytics: bool = False
    controller: bool = False
    hardware: bool = False

    @property
    def passed(self) -> bool:
        return all(
            (
                self.source,
                self.calibration,
                self.freshness,
                self.analytics,
                self.controller,
                self.hardware,
            )
        )


class SafetyStatus(Model):
    policy_mode: PolicyMode = PolicyMode.DISABLED
    recovery_state: RecoveryState = RecoveryState.HEALTHY
    recovery_reason: str | None = None
    recovery_checks: RecoveryChecks | None = None
    health: OperationalHealth = Field(default_factory=OperationalHealth)
    hardware_state: HardwareStateStages = Field(default_factory=HardwareStateStages)
    latest_decision: DecisionAudit | None = None


class DecisionAudit(Model):
    observation_sequence: int = Field(ge=0)
    recorded_at: float = Field(default_factory=time, ge=0)
    actual_baseline_action: dict[str, str | float]
    ai_proposed_action: dict[str, str | float] | None = None
    safety_response: str
    future_observed_outcome: dict[str, str | float] | None = None
    policy_error: str | None = None


class DecisionJournal:
    def __init__(self, capacity: int = 10_000) -> None:
        if capacity < 1:
            raise ValueError("Journal capacity must be positive")
        self._records: deque[DecisionAudit] = deque(maxlen=capacity)
        self._lock = Lock()

    def append(self, record: DecisionAudit) -> None:
        with self._lock:
            self._records.append(record)

    def record_outcome(self, observation_sequence: int, outcome: dict[str, str | float]) -> bool:
        with self._lock:
            for index in range(len(self._records) - 1, -1, -1):
                record = self._records[index]
                if record.observation_sequence == observation_sequence:
                    self._records[index] = record.model_copy(
                        update={"future_observed_outcome": outcome}
                    )
                    return True
        return False

    def snapshot(self) -> tuple[DecisionAudit, ...]:
        with self._lock:
            return tuple(sorted(self._records, key=lambda record: record.observation_sequence))


class RecoveryStateMachine:
    def __init__(self) -> None:
        self.state = RecoveryState.HEALTHY
        self.reason: str | None = None
        self.checks = RecoveryChecks()

    def degrade(self, reason: str) -> None:
        if self.state in (RecoveryState.HEALTHY, RecoveryState.DEGRADED):
            self.state, self.reason = RecoveryState.DEGRADED, reason

    def restore(self) -> None:
        """Clear a transient degradation. This never unlatches FAULT/SAFE and never
        re-arms a learned policy; the operator-visible reason is dropped with it."""
        if self.state == RecoveryState.DEGRADED:
            self.state, self.reason = RecoveryState.HEALTHY, None

    def fault(self, reason: str) -> None:
        if self.state != RecoveryState.SAFE:
            self.state, self.reason = RecoveryState.FAULT, reason

    def mark_safe(self) -> None:
        if self.state not in (RecoveryState.FAULT, RecoveryState.SAFE):
            raise RuntimeError("Only a faulted controller can enter SAFE")
        self.state = RecoveryState.SAFE

    def begin_recovery(self) -> None:
        if self.state != RecoveryState.SAFE:
            raise RuntimeError("Recovery can only start from SAFE")
        self.state, self.checks = RecoveryState.RECOVERING, RecoveryChecks()

    def qualify(self, checks: RecoveryChecks) -> bool:
        if self.state != RecoveryState.RECOVERING:
            raise RuntimeError("Recovery checks require RECOVERING state")
        self.checks = checks
        if checks.passed:
            self.state = RecoveryState.READY_FOR_OPERATOR_CONFIRMATION
            self.reason = None
            return True
        self.state, self.reason = RecoveryState.SAFE, "recovery_checks_failed"
        return False

    def confirm_operator(self) -> None:
        if self.state != RecoveryState.READY_FOR_OPERATOR_CONFIRMATION:
            raise RuntimeError("All recovery checks and operator confirmation are required")
        self.state, self.reason = RecoveryState.HEALTHY, None

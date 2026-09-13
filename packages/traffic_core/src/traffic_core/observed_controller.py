from traffic_core.controller import TrafficController, next_phase
from traffic_core.models import Decision, Phase, Policy, SignalStage, TrafficState
from traffic_core.safety import SafetyController
from traffic_core.settings import Timing


class ObservedTrafficController:
    """Control an observed state on an independent monotonic control clock."""

    def __init__(self, timing: Timing, policy: Policy) -> None:
        self.policy = TrafficController(timing, policy)
        self.safety = SafetyController(timing)
        self.now = 0.0
        self.emergency_phase: Phase | None = None
        self.emergency_until = 0.0
        self.decision = Decision(
            phase=Phase.NS, green_seconds=timing.min_green_seconds, reason="awaiting_observation"
        )

    def set_emergency(self, phase: Phase, ttl_seconds: float) -> None:
        if phase == Phase.PEDESTRIAN or not 1 <= ttl_seconds <= 120:
            raise ValueError("Emergency requires a vehicle phase and TTL 1..120 seconds")
        self.emergency_phase = phase
        self.emergency_until = self.now + ttl_seconds

    def clear_emergency(self) -> None:
        self.emergency_phase = None
        self.emergency_until = 0

    def advance(self, state: TrafficState, now: float) -> None:
        self.now = now
        if now >= self.emergency_until:
            self.clear_emergency()
        phase = self.safety.phase
        if self.safety.stage == SignalStage.GREEN:
            if now - self.safety.entered_at >= self.safety.green_seconds:
                phase = next_phase(phase)
        else:
            phase = self.safety.pending
        self.decision = self.policy.decide(state, phase, self.emergency_phase)
        self.safety.advance(now, self.decision)

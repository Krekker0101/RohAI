from traffic_core.models import Decision, Phase, Policy, TrafficState, phase_for
from traffic_core.settings import Timing

PHASE_ORDER = (Phase.NS, Phase.EW, Phase.PEDESTRIAN)


class TrafficController:
    """Bounded cyclic service prevents starvation; demand determines green duration."""

    def __init__(self, timing: Timing, policy: Policy) -> None:
        self.timing = timing
        self.policy = policy

    def decide(self, state: TrafficState, phase: Phase, emergency: Phase | None = None) -> Decision:
        if emergency is not None:
            return Decision(
                phase=emergency,
                green_seconds=self.timing.max_green_seconds,
                reason="operator_emergency_priority",
            )
        if phase == Phase.PEDESTRIAN:
            duration = self.timing.pedestrian_green_seconds
        elif self.policy == Policy.FIXED:
            duration = self.timing.fixed_green_seconds
        else:
            score = sum(
                a.traffic_score for a in state.approaches if phase_for(a.direction) == phase
            )
            duration = min(
                self.timing.max_green_seconds,
                self.timing.min_green_seconds + score * self.timing.seconds_per_score,
            )
        return Decision(phase=phase, green_seconds=duration, reason=self.policy.value)


def next_phase(phase: Phase) -> Phase:
    return PHASE_ORDER[(PHASE_ORDER.index(phase) + 1) % len(PHASE_ORDER)]

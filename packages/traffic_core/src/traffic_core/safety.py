import math

from traffic_core.models import Decision, Phase, SignalStage, SignalState
from traffic_core.settings import Timing


class SafetyController:
    """Only this state machine issues signal states. At most one transition per tick.

    Delayed ticks lengthen clearance instead of skipping intermediate outputs.
    Pedestrian yellow is a clearance interval with all physical lamps red.
    """

    def __init__(self, timing: Timing) -> None:
        self.timing = timing
        self.phase = Phase.NS
        self.stage = SignalStage.ALL_RED
        self.entered_at = 0.0
        self.last_time = 0.0
        self.pending = Phase.NS
        self.green_seconds = timing.min_green_seconds
        self.faulted = False

    def snapshot(self, now: float) -> SignalState:
        return SignalState(
            phase=self.phase, stage=self.stage, elapsed_seconds=max(0, now - self.entered_at)
        )

    def fail_safe(self, now: float) -> SignalState:
        self.faulted = True
        self.stage = SignalStage.ALL_RED
        self.entered_at = now
        return self.snapshot(now)

    def advance(self, now: float, decision: Decision) -> SignalState:
        if not math.isfinite(now) or now < self.last_time:
            raise ValueError("Safety clock must be finite and monotonic")
        self.last_time = now
        if self.faulted:
            return self.snapshot(now)
        elapsed = now - self.entered_at
        if self.stage == SignalStage.ALL_RED:
            if elapsed >= self.timing.all_red_seconds:
                self.phase = decision.phase
                self.green_seconds = min(
                    self.timing.max_green_seconds,
                    max(self.timing.min_green_seconds, decision.green_seconds),
                )
                if self.phase == Phase.PEDESTRIAN:
                    self.green_seconds = max(
                        self.green_seconds, self.timing.pedestrian_green_seconds
                    )
                self._enter(SignalStage.GREEN, now)
        elif self.stage == SignalStage.GREEN:
            minimum = (
                self.timing.pedestrian_green_seconds
                if self.phase == Phase.PEDESTRIAN
                else self.timing.min_green_seconds
            )
            if elapsed >= self.green_seconds or (
                decision.phase != self.phase and elapsed >= minimum
            ):
                self.pending = decision.phase
                self._enter(SignalStage.YELLOW, now)
        else:
            clearance = (
                self.timing.pedestrian_clearance_seconds
                if self.phase == Phase.PEDESTRIAN
                else self.timing.yellow_seconds
            )
            if elapsed >= clearance:
                self._enter(SignalStage.ALL_RED, now)
        return self.snapshot(now)

    def _enter(self, stage: SignalStage, now: float) -> None:
        self.stage = stage
        self.entered_at = now

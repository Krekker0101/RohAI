"""Requests cannot command lamps. Mandatory clearances remain in the core safety machine."""

from dataclasses import dataclass

from traffic_core.controller import next_phase
from traffic_core.models import Decision, Phase, SignalStage, SignalState
from traffic_core.safety import SafetyController
from traffic_core.settings import Timing

from traffic_simulator.learning.contracts import ControlRequest
from traffic_simulator.twin_models import ObservedTrafficState


@dataclass(frozen=True)
class RequestOutcome:
    accepted: bool
    reason: str
    requested_phase: Phase
    effective_phase: Phase
    effective_duration_s: float


class SafetySupervisor:
    def __init__(self, timing: Timing) -> None:
        self.timing = timing
        self.machine = SafetyController(timing)
        self.last_service = dict.fromkeys(Phase, 0.0)
        self.has_served = dict.fromkeys(Phase, False)
        self.locked = False
        self.pending_duration = timing.min_green_seconds
        # Service-opportunity bound derived from existing maximum greens and clearances.
        self.service_bound_s = (
            2 * (timing.max_green_seconds + timing.yellow_seconds + timing.all_red_seconds)
            + timing.pedestrian_clearance_seconds
        )
        self.interventions = 0
        self.switches = 0
        self.phase_started_at = 0.0
        self.last_request: ControlRequest | None = None
        self.outcome = RequestOutcome(
            False, "startup", Phase.NS, Phase.NS, timing.min_green_seconds
        )

    def apply(self, request: ControlRequest, observation: ObservedTrafficState) -> SignalState:
        if not isinstance(request, ControlRequest) or not isinstance(
            observation, ObservedTrafficState
        ):
            raise TypeError("Supervisor accepts ControlRequest and observed traffic only")
        now = observation.traffic.simulation_time
        self.last_request = request
        if now < self.machine.last_time:
            raise ValueError("Supervisor clock must be monotonic")
        before = self.machine.snapshot(now)
        previous_entered = self.machine.entered_at
        if self.machine.faulted:
            self.machine.last_time = now
            return self._fault_output(now, request)
        if not self.machine.accept_observation(observation.traffic, now):
            # Existing core fail_safe latches all-red immediately. This request supervisor
            # preserves mandatory yellow/ pedestrian clearance before settling into all-red.
            if self.machine.faulted and before.stage in (SignalStage.GREEN, SignalStage.YELLOW):
                self.machine.stage = SignalStage.YELLOW
                self.machine.entered_at = (
                    now if before.stage == SignalStage.GREEN else previous_entered
                )
                self.switches += int(before.stage == SignalStage.GREEN)
            return self._fault_output(now, request)

        return self._healthy_request(request, before, now)

    def _fault_output(self, now: float, request: ControlRequest) -> SignalState:
        if self.machine.stage == SignalStage.YELLOW:
            clearance = (
                self.timing.pedestrian_clearance_seconds
                if self.machine.phase == Phase.PEDESTRIAN
                else self.timing.yellow_seconds
            )
            if now - self.machine.entered_at >= clearance:
                self.machine.stage = SignalStage.ALL_RED
                self.machine.entered_at = now
        self.outcome = RequestOutcome(
            False,
            "perception_unreliable",
            request.requested_phase,
            self.machine.phase,
            self.machine.green_seconds,
        )
        self.interventions += 1
        return self.machine.snapshot(now)

    def _healthy_request(
        self, request: ControlRequest, before: SignalState, now: float
    ) -> SignalState:
        minimum = (
            self.timing.pedestrian_green_seconds
            if request.requested_phase == Phase.PEDESTRIAN
            else self.timing.min_green_seconds
        )
        duration = min(self.timing.max_green_seconds, max(minimum, request.requested_duration_s))
        target = request.requested_phase
        reason = "accepted" if duration == request.requested_duration_s else "duration_clamped"
        elapsed = now - self.machine.entered_at
        if self.locked:
            target, duration = self.machine.pending, self.pending_duration
            reason = "clearance_locked"
        elif before.stage == SignalStage.GREEN:
            phase_min = (
                self.timing.pedestrian_green_seconds
                if before.phase == Phase.PEDESTRIAN
                else self.timing.min_green_seconds
            )
            if elapsed < phase_min and target != before.phase:
                target, duration = before.phase, self.machine.green_seconds
                reason = "minimum_green"
            elif elapsed >= phase_min:
                overdue = [
                    p
                    for p in Phase
                    if p != before.phase and now - self.last_service[p] >= self.service_bound_s
                ]
                if overdue:
                    target = max(overdue, key=lambda p: now - self.last_service[p])
                    duration = (
                        self.timing.pedestrian_green_seconds
                        if target == Phase.PEDESTRIAN
                        else self.timing.min_green_seconds
                    )
                    reason = "service_deadline"
                elif target == before.phase and elapsed >= min(
                    duration, self.timing.max_green_seconds
                ):
                    target = next_phase(before.phase)
                    duration = (
                        self.timing.pedestrian_green_seconds
                        if target == Phase.PEDESTRIAN
                        else self.timing.min_green_seconds
                    )
                    reason = "green_budget_exhausted"
            if target == before.phase:
                self.machine.green_seconds = duration
            else:
                self.pending_duration = duration
        if before.stage == SignalStage.ALL_RED and not self.locked:
            self.machine.pending = target
            self.pending_duration = duration
        decision = Decision(phase=target, green_seconds=duration, reason=reason)
        result = self.machine.advance(now, decision)
        if before.stage == SignalStage.GREEN and result.stage == SignalStage.YELLOW:
            self.locked = True
            self.pending_duration = duration
            self.switches += 1
        if result.stage == SignalStage.GREEN:
            if before.stage != SignalStage.GREEN:
                self.phase_started_at = now
            self.locked = False
            self.last_service[result.phase] = now
            self.has_served[result.phase] = True
        accepted = reason in ("accepted", "duration_clamped")
        self.outcome = RequestOutcome(accepted, reason, request.requested_phase, target, duration)
        self.interventions += int(not accepted)
        return result

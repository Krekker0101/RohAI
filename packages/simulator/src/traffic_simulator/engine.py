from traffic_core.controller import TrafficController, next_phase
from traffic_core.models import Phase, Policy, SignalStage, Telemetry
from traffic_core.safety import SafetyController
from traffic_core.settings import Timing

from traffic_simulator.simulator import Scenario, Simulator


class SimulationEngine:
    """Synchronous application use case, independently testable without asyncio."""

    def __init__(self, scenario: Scenario, timing: Timing, policy: Policy) -> None:
        self.simulator = Simulator(scenario, timing.waiting_score_per_second)
        self.controller = TrafficController(timing, policy)
        self.safety = SafetyController(timing)
        self.sequence = 0
        self.emergency_phase: Phase | None = None
        self.emergency_until = 0.0
        self.decision = self.controller.decide(self.simulator.snapshot(), Phase.NS)

    def set_emergency(self, phase: Phase, ttl_seconds: float) -> None:
        if phase == Phase.PEDESTRIAN or not 1 <= ttl_seconds <= 120:
            raise ValueError("Emergency requires a vehicle phase and TTL between 1 and 120 seconds")
        self.emergency_phase = phase
        self.emergency_until = self.simulator.now + ttl_seconds

    def clear_emergency(self) -> None:
        self.emergency_phase = None
        self.emergency_until = 0

    def step(self, seconds: float) -> Telemetry:
        self.simulator.advance(seconds, self.safety.snapshot(self.simulator.now))
        now = self.simulator.now
        if now >= self.emergency_until:
            self.clear_emergency()
        state = self.simulator.snapshot()
        safety = self.safety
        desired = safety.phase
        if safety.stage == SignalStage.GREEN:
            if now - safety.entered_at >= safety.green_seconds:
                desired = next_phase(safety.phase)
        else:
            desired = safety.pending
        self.decision = self.controller.decide(state, desired, self.emergency_phase)
        safety.advance(now, self.decision)
        self.sequence += 1
        return self.snapshot()

    def snapshot(self) -> Telemetry:
        signals = self.safety.snapshot(self.simulator.now)
        return Telemetry(
            sequence=self.sequence,
            policy=self.controller.policy,
            traffic=self.simulator.snapshot(),
            signals=signals,
            lamps=signals.lamps(),
            decision=self.decision,
            green_target_seconds=self.safety.green_seconds,
            emergency_phase=self.emergency_phase,
            kpi=self.simulator.kpi(),
        )

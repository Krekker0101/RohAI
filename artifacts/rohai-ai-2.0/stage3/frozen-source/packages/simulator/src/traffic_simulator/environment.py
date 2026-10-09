"""Environment boundary. A policy receives observations, never an environment handle."""

from typing import Protocol, runtime_checkable

from traffic_core.models import ObjectKind, Policy, SignalState
from traffic_core.observed_controller import ObservedTrafficController
from traffic_core.settings import Timing

from traffic_simulator.simulator import Scenario, Simulator
from traffic_simulator.twin_dynamics import TwinDynamics, realize, stable_seed
from traffic_simulator.twin_models import (
    GroundTruthState,
    ObservedTrafficState,
    PerceptionProfile,
    ScenarioDefinition,
    TruthEntity,
)
from traffic_simulator.twin_observation import ObservationModel


@runtime_checkable
class TrafficEnvironment(Protocol):
    @property
    def now(self) -> float: ...
    def step(self, action: SignalState) -> ObservedTrafficState: ...
    def observe(self) -> ObservedTrafficState: ...
    def ground_truth(self) -> GroundTruthState: ...
    def close(self) -> None: ...


class ObservationControllerPort:
    """Runtime type guard supplements static separation from latent state."""

    def __init__(self, timing: Timing, policy: Policy) -> None:
        self._controller = ObservedTrafficController(timing, policy)

    def act(self, observation: ObservedTrafficState) -> SignalState:
        if not isinstance(observation, ObservedTrafficState):
            raise TypeError("Digital-twin control requires ObservedTrafficState")
        state = observation.traffic
        if any(a.observation.source_camera_ids == ("simulator",) for a in state.approaches):
            raise ValueError("Ideal fast-simulator evidence is forbidden in twin evaluation")
        self._controller.advance(state, state.simulation_time)
        return self._controller.safety.snapshot(state.simulation_time)


class DigitalTwinEnvironment:
    def __init__(self, scenario: ScenarioDefinition, timing: Timing | None = None) -> None:
        self.requested_scenario = scenario
        self.scenario = realize(scenario)
        self.dynamics = TwinDynamics(self.scenario)
        self.sensor = ObservationModel(
            self.scenario.perception,
            stable_seed(scenario.seed, "perception"),
            (timing or Timing()).waiting_score_per_second,
        )
        self.sensor.ingest(self.ground_truth())

    @property
    def now(self) -> float:
        return self.dynamics.now

    def step(self, action: SignalState) -> ObservedTrafficState:
        self.sensor.ingest(self.dynamics.advance(action))
        return self.observe()

    def observe(self) -> ObservedTrafficState:
        return self.sensor.observe(self.now)

    def ground_truth(self) -> GroundTruthState:
        return self.dynamics.ground_truth()

    def close(self) -> None:
        pass


class FastTrafficEnvironment:
    """Original point-queue engine behind the same observation boundary, unchanged dynamics."""

    def __init__(
        self, scenario: Scenario, perception: PerceptionProfile | None = None, step_s: float = 0.25
    ) -> None:
        self.simulator = Simulator(scenario)
        self.step_s = step_s
        self.sensor = ObservationModel(perception or PerceptionProfile(), scenario.seed)
        if not 0 < step_s <= 1 / self.sensor.profile.fps:
            raise ValueError("Fast step must resolve camera capture interval")
        self.sensor.ingest(self.ground_truth())

    @property
    def now(self) -> float:
        return self.simulator.now

    def step(self, action: SignalState) -> ObservedTrafficState:
        self.simulator.advance(self.step_s, action)
        self.sensor.ingest(self.ground_truth())
        return self.observe()

    def observe(self) -> ObservedTrafficState:
        return self.sensor.observe(self.now)

    def ground_truth(self) -> GroundTruthState:
        from traffic_core.models import Direction

        queued = tuple(
            TruthEntity(
                entity_id=e.id,
                kind=e.kind,
                approach=direction or Direction.NORTH,
                lane_id=f"{direction or Direction.NORTH}-fast",
                arrived_at_s=e.at,
                admitted_at_s=e.at,
                distance_to_stop_m=index * 6.5,
                length_m=0.5 if e.kind == ObjectKind.PEDESTRIAN else 4.5,
            )
            for direction, queue in self.simulator.queues.items()
            for index, e in enumerate(queue)
        )
        kpi = self.simulator.kpi()
        return GroundTruthState(
            timestamp_s=self.now,
            queued=queued,
            arrived=kpi.arrived,
            departed=kpi.departed,
            total_wait_s=kpi.total_wait_seconds,
        )

    def close(self) -> None:
        pass


class SumoBridge(Protocol):
    """Network-specific TraCI/libsumo mapping lives behind this injected interface.

    Implementations must apply TLS commands before simulationStep, map lanes/types,
    and return truth on a monotonic clock; TLS link indices never reach core policy.
    """

    def apply_signal(self, action: SignalState) -> None: ...
    def simulation_step(self) -> None: ...
    def read_ground_truth(self) -> GroundTruthState: ...
    def close(self) -> None: ...


class SumoTrafficEnvironment:
    """Tested adapter boundary. This class does not claim an installed SUMO integration."""

    def __init__(self, bridge: SumoBridge, perception: PerceptionProfile, seed: int) -> None:
        self._bridge = bridge
        self._truth = bridge.read_ground_truth()
        self.sensor = ObservationModel(perception, seed)
        self.sensor.ingest(self._truth)

    @property
    def now(self) -> float:
        return self._truth.timestamp_s

    def step(self, action: SignalState) -> ObservedTrafficState:
        self._bridge.apply_signal(action)
        self._bridge.simulation_step()
        next_truth = self._bridge.read_ground_truth()
        if not 0 < next_truth.timestamp_s - self.now <= 1 / self.sensor.profile.fps + 1e-9:
            raise ValueError("SUMO bridge clock/step violates camera sampling contract")
        self._truth = next_truth
        self.sensor.ingest(self._truth)
        return self.observe()

    def observe(self) -> ObservedTrafficState:
        return self.sensor.observe(self.now)

    def ground_truth(self) -> GroundTruthState:
        return self._truth

    def close(self) -> None:
        self._bridge.close()

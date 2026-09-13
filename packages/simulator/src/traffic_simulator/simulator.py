import math
import random
from collections import deque
from dataclasses import dataclass

from pydantic import Field, model_validator
from traffic_core.models import (
    KPI,
    ApproachState,
    Direction,
    Model,
    ObjectKind,
    Phase,
    SignalStage,
    SignalState,
    TrafficState,
    phase_for,
)
from traffic_core.state import build_approach_state

VEHICLE_KINDS = (
    ObjectKind.CAR,
    ObjectKind.BUS,
    ObjectKind.TRUCK,
    ObjectKind.MOTORCYCLE,
    ObjectKind.BICYCLE,
)
VEHICLE_PROBABILITIES = (0.70, 0.08, 0.10, 0.07, 0.05)


class Scenario(Model):
    seed: int = Field(default=42, ge=0)
    north_per_minute: float = Field(default=18, ge=0, le=120)
    south_per_minute: float = Field(default=12, ge=0, le=120)
    east_per_minute: float = Field(default=5, ge=0, le=120)
    west_per_minute: float = Field(default=4, ge=0, le=120)
    pedestrians_per_minute: float = Field(default=6, ge=0, le=120)
    vehicle_headway_seconds: float = Field(default=2, ge=0.5, le=10)
    pedestrian_headway_seconds: float = Field(default=0.5, ge=0.1, le=10)
    arrival_window_seconds: float = Field(default=60, ge=1)
    moderate_queue: int = Field(default=5, ge=1)
    high_queue: int = Field(default=15, ge=1)

    @model_validator(mode="after")
    def ordered_thresholds(self) -> "Scenario":
        if self.high_queue <= self.moderate_queue:
            raise ValueError("high_queue must exceed moderate_queue")
        return self

    def rates(self) -> dict[Direction | None, float]:
        return {
            Direction.NORTH: self.north_per_minute / 60,
            Direction.SOUTH: self.south_per_minute / 60,
            Direction.EAST: self.east_per_minute / 60,
            Direction.WEST: self.west_per_minute / 60,
            None: self.pedestrians_per_minute / 60,
        }


@dataclass(frozen=True)
class Arrival:
    id: int
    at: float
    direction: Direction | None
    kind: ObjectKind


class Simulator:
    """Point queues with Poisson arrivals and deterministic saturation headways.

    Arrival RNG is independent of controller/service, enabling paired comparisons.
    Queue delay is integrated exactly for every arrival and departure event.
    """

    def __init__(self, scenario: Scenario, waiting_weight: float = 0.05) -> None:
        self.scenario = scenario
        self.waiting_weight = waiting_weight
        self.now = 0.0
        self.rng = random.Random(scenario.seed)
        self.rates = scenario.rates()
        self.next_arrival = {d: self._interval(rate) for d, rate in self.rates.items()}
        self.queues: dict[Direction | None, deque[Arrival]] = {d: deque() for d in self.rates}
        self.recent: dict[Direction | None, deque[float]] = {d: deque() for d in self.rates}
        self.next_departure = dict.fromkeys(self.rates, 0.0)
        self.arrived = 0
        self.departed = 0
        self.completed_wait = 0.0
        self.max_queue = 0

    def _interval(self, rate: float) -> float:
        return self.rng.expovariate(rate) if rate > 0 else math.inf

    def advance(self, seconds: float, signal: SignalState) -> None:
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError("Simulation step must be finite and positive")
        end = self.now + seconds
        occupancy = sum(len(q) for q in self.queues.values())
        self.max_queue = max(self.max_queue, occupancy)
        events: list[tuple[float, int]] = []
        # A globally ordered event stream makes arrivals independent of step size too.
        while True:
            direction = min(self.next_arrival, key=lambda d: self.next_arrival[d])
            at = self.next_arrival[direction]
            if at > end:
                break
            self.arrived += 1
            events.append((at, 1))
            kind = (
                ObjectKind.PEDESTRIAN
                if direction is None
                else self.rng.choices(VEHICLE_KINDS, weights=VEHICLE_PROBABILITIES, k=1)[0]
            )
            self.queues[direction].append(Arrival(self.arrived, at, direction, kind))
            self.recent[direction].append(at)
            self.next_arrival[direction] = at + self._interval(self.rates[direction])
        for direction, queue in self.queues.items():
            phase = Phase.PEDESTRIAN if direction is None else phase_for(direction)
            headway = (
                self.scenario.pedestrian_headway_seconds
                if direction is None
                else self.scenario.vehicle_headway_seconds
            )
            if signal.stage != SignalStage.GREEN or signal.phase != phase:
                self.next_departure[direction] = end + headway
                continue
            while queue:
                depart_at = max(self.next_departure[direction], self.now, queue[0].at + headway)
                if depart_at > end:
                    break
                entity = queue.popleft()
                events.append((depart_at, -1))
                self.departed += 1
                self.completed_wait += depart_at - entity.at
                self.next_departure[direction] = depart_at + headway
        for _, change in sorted(events):
            occupancy += change
            self.max_queue = max(self.max_queue, occupancy)
        self.now = end
        for history in self.recent.values():
            while history and history[0] <= self.now - self.scenario.arrival_window_seconds:
                history.popleft()

    def snapshot(self) -> TrafficState:
        approaches: list[ApproachState] = []
        window = min(self.now, self.scenario.arrival_window_seconds)
        for direction in Direction:
            queue = self.queues[direction]
            approaches.append(
                build_approach_state(
                    direction=direction,
                    waiting_objects=[(e.kind, self.now - e.at) for e in queue],
                    arrival_rate_per_minute=len(self.recent[direction]) * 60 / window
                    if window
                    else 0,
                    waiting_weight=self.waiting_weight,
                    moderate_queue=self.scenario.moderate_queue,
                    high_queue=self.scenario.high_queue,
                )
            )
        pedestrians = self.queues[None]
        return TrafficState(
            simulation_time=self.now,
            approaches=tuple(approaches),
            pedestrians_waiting=len(pedestrians),
            pedestrian_oldest_wait_seconds=self.now - pedestrians[0].at if pedestrians else 0,
        )

    def kpi(self) -> KPI:
        remaining = sum(len(q) for q in self.queues.values())
        total = self.completed_wait + sum(
            self.now - e.at for queue in self.queues.values() for e in queue
        )
        return KPI(
            arrived=self.arrived,
            departed=self.departed,
            remaining=remaining,
            total_wait_seconds=total,
            mean_wait_per_arrival_seconds=total / self.arrived if self.arrived else 0,
            mean_completed_wait_seconds=(
                self.completed_wait / self.departed if self.departed else 0
            ),
            throughput_per_minute=self.departed * 60 / self.now if self.now else 0,
            max_queue=self.max_queue,
        )

"""Finite lane queues and downstream storage, not a car-following physics model."""

import hashlib
import json
import math
import random
from collections import deque
from dataclasses import dataclass
from typing import Literal

from traffic_core.models import Direction, ObjectKind, Phase, SignalStage, SignalState, phase_for

from traffic_simulator.simulator import VEHICLE_KINDS
from traffic_simulator.twin_models import (
    GroundTruthState,
    ScenarioDefinition,
    ServiceRecord,
    TruthEntity,
)

LENGTHS = dict(zip(VEHICLE_KINDS, (4.5, 12.0, 10.0, 2.0, 1.8), strict=True))
HEADWAYS = dict(zip(VEHICLE_KINDS, (1.0, 1.5, 1.5, 0.8, 1.2), strict=True))
COMPASS = (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST)


def stable_seed(seed: int, *parts: object) -> int:
    return int.from_bytes(hashlib.sha256(repr((seed, *parts)).encode()).digest()[:8], "big")


def realize(scenario: ScenarioDefinition) -> ScenarioDefinition:
    profile = scenario.randomization
    if not profile.enabled:
        return scenario
    rng = random.Random(stable_seed(scenario.seed, "domain_randomization"))
    values = {
        key: rng.uniform(getattr(profile, key).low, getattr(profile, key).high)
        for key in (
            "arrival_multiplier",
            "headway_multiplier",
            "reaction_multiplier",
            "noise_multiplier",
            "latency_multiplier",
            "occlusion_multiplier",
            "camera_availability",
        )
    }
    data = scenario.model_dump(mode="json")
    traffic, perception = data["traffic"], data["perception"]
    for period in traffic["periods"]:
        period["vehicles_per_minute"] = {
            k: v * values["arrival_multiplier"] for k, v in period["vehicles_per_minute"].items()
        }
        period["pedestrians_per_minute"] *= values["arrival_multiplier"]
    traffic["headway_s"] *= values["headway_multiplier"]
    traffic["reaction_s"] *= values["reaction_multiplier"]
    traffic["arrival_distribution"] = rng.choice(profile.arrival_distributions)
    for key in ("position_noise_std_m", "wait_noise_std_s"):
        perception[key] *= values["noise_multiplier"]
    for key in ("camera_delay_s", "inference_latency_s"):
        perception[key] *= values["latency_multiplier"]
    perception["occlusion_probability"] = min(
        1, perception["occlusion_probability"] * values["occlusion_multiplier"]
    )
    perception["camera_availability"] *= values["camera_availability"]
    data["randomization"]["enabled"] = False
    return ScenarioDefinition.model_validate(data)


def arrival_tape(scenario: ScenarioDefinition) -> tuple[TruthEntity, ...]:
    """Pre-generate exogenous demand; actions cannot consume its random stream."""
    rng = random.Random(stable_seed(scenario.seed, "arrivals"))
    records: list[TruthEntity] = []
    traffic = scenario.traffic
    for period in traffic.periods:
        for direction in Direction:
            for pedestrian in (False, True):
                rate = (
                    period.pedestrians_per_minute / 4
                    if pedestrian
                    else period.vehicles_per_minute[direction]
                ) / 60
                if rate == 0:
                    continue
                at = period.start_s
                batch = traffic.burst_size if traffic.arrival_distribution == "bursty" else 1
                while True:
                    at += (
                        batch / rate
                        if traffic.arrival_distribution == "regular"
                        else rng.expovariate(rate / batch)
                    )
                    if at >= period.end_s:
                        break
                    for _ in range(batch):
                        kind = (
                            ObjectKind.PEDESTRIAN
                            if pedestrian
                            else rng.choices(VEHICLE_KINDS, weights=traffic.vehicle_probabilities)[
                                0
                            ]
                        )
                        lane = rng.randrange(traffic.lanes_per_approach)
                        turns: tuple[Literal["left", "straight", "right"], ...] = (
                            "left",
                            "straight",
                            "right",
                        )
                        turn = rng.choices(turns, weights=traffic.turn_probabilities)[0]
                        records.append(
                            TruthEntity(
                                entity_id=len(records) + 1,
                                kind=kind,
                                approach=direction,
                                lane_id=f"{direction.value}-{lane}"
                                if not pedestrian
                                else f"{direction.value}-walk",
                                turn=turn,
                                arrived_at_s=at,
                                distance_to_stop_m=traffic.lane_length_m,
                                length_m=0.5 if pedestrian else LENGTHS[kind],
                            )
                        )
                        if len(records) > 200000:
                            raise ValueError("Arrival tape capacity exceeded")
    return tuple(sorted(records, key=lambda e: (e.arrived_at_s, e.entity_id)))


def tape_hash(tape: tuple[TruthEntity, ...]) -> str:
    return hashlib.sha256(
        json.dumps(
            [e.model_dump(mode="json") for e in tape], sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()


@dataclass
class Lane:
    direction: Direction
    queue: deque[TruthEntity]
    upstream: deque[TruthEntity]
    next_service_s: float = 0


class TwinDynamics:
    def __init__(self, scenario: ScenarioDefinition) -> None:
        self.scenario = scenario
        self.tape = arrival_tape(scenario)
        self.tape_sha256 = tape_hash(self.tape)
        self.cursor = 0
        self.tick = 0
        self.now = 0.0
        self.lanes = {
            f"{direction.value}-{index}": Lane(direction, deque(), deque())
            for direction in Direction
            for index in range(scenario.traffic.lanes_per_approach)
        }
        self.pedestrians: deque[TruthEntity] = deque()
        self.crossing: list[float] = []
        self.vehicle_crossings: list[tuple[float, Phase, bool]] = []
        self.downstream: dict[Direction, deque[float]] = {d: deque() for d in Direction}
        self.departed = self.stop_line_departures = self.pedestrian_departed = 0
        self.total_wait_s = self.pedestrian_wait_s = self.spillback_s = self.blocked_service_s = 0.0
        self.previous_green: Phase | None = None
        self.next_pedestrian_s = 0.0
        self.conflicting_service = 0
        self.service_records: list[ServiceRecord] = []
        self.downstream_record_indices: dict[Direction, deque[int]] = {
            d: deque() for d in Direction
        }
        self.crossing_record_indices: list[int] = []
        self.downstream_delay_s = 0.0

    def _blocked(self, direction: Direction, now: float) -> bool:
        return any(
            i.downstream == direction and i.start_s <= now < i.end_s
            for i in self.scenario.traffic.incidents
        )

    def advance(self, action: SignalState) -> GroundTruthState:
        dt, traffic = self.scenario.step_s, self.scenario.traffic
        if self.now >= self.scenario.duration_s - 1e-9:
            raise ValueError("Scenario duration exhausted")
        self.tick += 1
        end = self.tick * dt
        # Wait integral before end-of-tick service; arrivals within the tick get fractional wait.
        waiting = sum(len(lane.queue) + len(lane.upstream) for lane in self.lanes.values())
        self.total_wait_s += (waiting + len(self.pedestrians)) * dt
        self.pedestrian_wait_s += len(self.pedestrians) * dt
        while self.cursor < len(self.tape) and self.tape[self.cursor].arrived_at_s <= end:
            entity = self.tape[self.cursor]
            self.cursor += 1
            self.total_wait_s += end - entity.arrived_at_s
            if entity.kind == ObjectKind.PEDESTRIAN:
                self.pedestrian_wait_s += end - entity.arrived_at_s
                self.pedestrians.append(entity.model_copy(update={"admitted_at_s": end}))
            else:
                self.lanes[entity.lane_id].upstream.append(entity)
        for ready, index in zip(self.crossing, self.crossing_record_indices, strict=True):
            if ready <= end:
                self.service_records[index] = self.service_records[index].model_copy(
                    update={"departed": True}
                )
        self.crossing_record_indices = [
            index
            for ready, index in zip(self.crossing, self.crossing_record_indices, strict=True)
            if ready > end
        ]
        cleared = [at for at in self.crossing if at <= end]
        self.crossing = [at for at in self.crossing if at > end]
        self.vehicle_crossings = [entry for entry in self.vehicle_crossings if entry[0] > end]
        self.departed += len(cleared)
        self.pedestrian_departed += len(cleared)
        for direction, storage in self.downstream.items():
            indices = self.downstream_record_indices[direction]
            for ready, index in zip(storage, indices, strict=True):
                record = self.service_records[index]
                self.service_records[index] = record.model_copy(
                    update={
                        "downstream_delay_s": record.downstream_delay_s
                        + max(0, end - max(self.now, ready))
                    }
                )
            self.downstream_delay_s += sum(max(0, end - max(self.now, ready)) for ready in storage)
            if not self._blocked(direction, end):
                while storage and storage[0] <= end:
                    storage.popleft()
                    index = indices.popleft()
                    self.service_records[index] = self.service_records[index].model_copy(
                        update={"departed": True}
                    )
                    self.departed += 1
        green = action.phase if action.stage == SignalStage.GREEN else None
        for lane in self.lanes.values():
            used = sum(e.length_m + 2 for e in lane.queue)
            while lane.upstream and used + lane.upstream[0].length_m + 2 <= traffic.lane_length_m:
                entity = lane.upstream.popleft()
                lane.queue.append(entity.model_copy(update={"admitted_at_s": end}))
                used += entity.length_m + 2
            if lane.upstream:
                self.spillback_s += dt  # lane-seconds, not elapsed scenario seconds
            if green != self.previous_green and green == phase_for(lane.direction):
                lane.next_service_s = max(lane.next_service_s, self.now + traffic.reaction_s)
            if green != phase_for(lane.direction) or not lane.queue or self.crossing:
                continue
            entity = lane.queue[0]
            # Conservative conflict abstraction: left turns use the junction exclusively;
            # compatible straight/right lanes can discharge concurrently in the same phase.
            if any(
                phase != green or left or entity.turn == "left"
                for _, phase, left in self.vehicle_crossings
            ):
                self.blocked_service_s += dt
                continue
            offset = {"left": 1, "straight": 2, "right": -1}[entity.turn]
            destination = COMPASS[(COMPASS.index(entity.approach) + offset) % 4]
            if (
                self._blocked(destination, end)
                or len(self.downstream[destination]) >= traffic.downstream_capacity
            ):
                self.blocked_service_s += dt
                continue
            if (
                end >= lane.next_service_s
                and entity.admitted_at_s is not None
                and end > entity.admitted_at_s
            ):
                lane.queue.popleft()
                self.service_records.append(
                    ServiceRecord(
                        entity_id=entity.entity_id,
                        kind=entity.kind,
                        approach=entity.approach,
                        queue_wait_s=end - entity.arrived_at_s,
                        served_at_s=end,
                        departed=False,
                    )
                )
                factor = HEADWAYS[entity.kind] * (1.2 if entity.turn == "left" else 1)
                lane.next_service_s = end + traffic.headway_s * factor
                self.downstream[destination].append(end + traffic.downstream_travel_s)
                self.downstream_record_indices[destination].append(len(self.service_records) - 1)
                self.stop_line_departures += 1
                self.conflicting_service += int(
                    bool(self.crossing) or green != phase_for(entity.approach)
                )
                self.vehicle_crossings.append(
                    (
                        end + min(1.0, traffic.downstream_travel_s),
                        phase_for(entity.approach),
                        entity.turn == "left",
                    )
                )
        if (
            green == Phase.PEDESTRIAN
            and self.pedestrians
            and end >= self.next_pedestrian_s
            and not self.vehicle_crossings
        ):
            pedestrian = self.pedestrians.popleft()
            self.service_records.append(
                ServiceRecord(
                    entity_id=pedestrian.entity_id,
                    kind=pedestrian.kind,
                    approach=pedestrian.approach,
                    queue_wait_s=end - pedestrian.arrived_at_s,
                    served_at_s=end,
                    departed=False,
                )
            )
            self.crossing.append(end + traffic.crossing_s)
            self.crossing_record_indices.append(len(self.service_records) - 1)
            self.next_pedestrian_s = end + traffic.pedestrian_headway_s
            self.conflicting_service += int(bool(self.vehicle_crossings))
        self.previous_green = green
        self.now = end
        return self.ground_truth()

    def ground_truth(self) -> GroundTruthState:
        queued: list[TruthEntity] = list(self.pedestrians)
        upstream: list[TruthEntity] = []
        for lane in self.lanes.values():
            position = 0.0
            for entity in lane.queue:
                queued.append(entity.model_copy(update={"distance_to_stop_m": position}))
                position += entity.length_m + 2
            upstream.extend(lane.upstream)
        return GroundTruthState(
            timestamp_s=self.now,
            queued=tuple(queued),
            upstream=tuple(upstream),
            crossing_pedestrians=len(self.crossing),
            downstream_vehicles=sum(map(len, self.downstream.values())),
            arrived=self.cursor,
            departed=self.departed,
            stop_line_departures=self.stop_line_departures,
            total_wait_s=self.total_wait_s,
            pedestrian_wait_s=self.pedestrian_wait_s,
            pedestrian_departed=self.pedestrian_departed,
            spillback_s=self.spillback_s,
            blocked_service_s=self.blocked_service_s,
            downstream_delay_s=self.downstream_delay_s,
        )

    def invariants(self) -> dict[str, bool]:
        truth = self.ground_truth()
        return {
            "conservation": truth.arrived
            == truth.departed
            + len(truth.queued)
            + len(truth.upstream)
            + truth.crossing_pedestrians
            + truth.downstream_vehicles,
            "finite_storage": all(
                sum(e.length_m + 2 for e in lane.queue)
                <= self.scenario.traffic.lane_length_m + 1e-9
                for lane in self.lanes.values()
            )
            and all(
                len(q) <= self.scenario.traffic.downstream_capacity
                for q in self.downstream.values()
            ),
            "no_conflicting_service": self.conflicting_service == 0,
            "finite_metrics": all(
                math.isfinite(v) and v >= 0
                for v in (truth.total_wait_s, truth.pedestrian_wait_s, truth.spillback_s)
            ),
        }

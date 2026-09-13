from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class Direction(StrEnum):
    NORTH = "north"
    SOUTH = "south"
    EAST = "east"
    WEST = "west"


class ObjectKind(StrEnum):
    CAR = "car"
    BUS = "bus"
    TRUCK = "truck"
    MOTORCYCLE = "motorcycle"
    BICYCLE = "bicycle"
    PEDESTRIAN = "pedestrian"


class Phase(StrEnum):
    NS = "north_south"
    EW = "east_west"
    PEDESTRIAN = "pedestrian"


class SignalStage(StrEnum):
    ALL_RED = "all_red"
    GREEN = "green"
    YELLOW = "yellow"


class Lamp(StrEnum):
    RED = "red"
    YELLOW = "yellow"
    GREEN = "green"


class Policy(StrEnum):
    FIXED = "fixed"
    ADAPTIVE = "adaptive"


class Congestion(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"


def phase_for(direction: Direction) -> Phase:
    return Phase.NS if direction in (Direction.NORTH, Direction.SOUTH) else Phase.EW


class Counts(Model):
    cars: int = Field(default=0, ge=0)
    buses: int = Field(default=0, ge=0)
    trucks: int = Field(default=0, ge=0)
    motorcycles: int = Field(default=0, ge=0)
    bicycles: int = Field(default=0, ge=0)
    pedestrians: int = Field(default=0, ge=0)

    @classmethod
    def from_kinds(cls, kinds: list[ObjectKind]) -> "Counts":
        return cls(
            cars=kinds.count(ObjectKind.CAR),
            buses=kinds.count(ObjectKind.BUS),
            trucks=kinds.count(ObjectKind.TRUCK),
            motorcycles=kinds.count(ObjectKind.MOTORCYCLE),
            bicycles=kinds.count(ObjectKind.BICYCLE),
            pedestrians=kinds.count(ObjectKind.PEDESTRIAN),
        )


class ApproachState(Model):
    direction: Direction
    counts: Counts
    queue_length: int = Field(ge=0)
    traffic_score: float = Field(ge=0)
    mean_wait_seconds: float = Field(ge=0)
    oldest_wait_seconds: float = Field(ge=0)
    arrival_rate_per_minute: float = Field(ge=0)
    congestion: Congestion


class TrafficState(Model):
    simulation_time: float = Field(ge=0)
    approaches: tuple[ApproachState, ...]
    pedestrians_waiting: int = Field(ge=0)
    pedestrian_oldest_wait_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def complete_directions(self) -> "TrafficState":
        if len(self.approaches) != 4 or {a.direction for a in self.approaches} != set(Direction):
            raise ValueError("Exactly one state per direction is required")
        return self


class SignalState(Model):
    phase: Phase
    stage: SignalStage
    elapsed_seconds: float = Field(ge=0)

    def lamps(self) -> dict[str, Lamp]:
        result = dict.fromkeys([d.value for d in Direction] + ["pedestrian"], Lamp.RED)
        if self.stage == SignalStage.ALL_RED:
            return result
        if self.phase == Phase.PEDESTRIAN:
            if self.stage == SignalStage.GREEN:
                result["pedestrian"] = Lamp.GREEN
            return result
        for direction in Direction:
            if phase_for(direction) == self.phase:
                result[direction.value] = (
                    Lamp.GREEN if self.stage == SignalStage.GREEN else Lamp.YELLOW
                )
        return result


class Decision(Model):
    phase: Phase
    green_seconds: float = Field(gt=0)
    reason: str


class KPI(Model):
    arrived: int = Field(ge=0)
    departed: int = Field(ge=0)
    remaining: int = Field(ge=0)
    total_wait_seconds: float = Field(ge=0)
    mean_wait_per_arrival_seconds: float = Field(ge=0)
    mean_completed_wait_seconds: float = Field(ge=0)
    throughput_per_minute: float = Field(ge=0)
    max_queue: int = Field(ge=0)


class Telemetry(Model):
    schema_version: str = "1.0"
    sequence: int = Field(ge=0)
    policy: Policy
    traffic: TrafficState
    signals: SignalState
    lamps: dict[str, Lamp]
    decision: Decision
    green_target_seconds: float = Field(gt=0)
    emergency_phase: Phase | None
    kpi: KPI

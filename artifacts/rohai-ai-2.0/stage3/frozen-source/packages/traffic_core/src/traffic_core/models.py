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
    EMERGENCY_VEHICLE = "emergency_vehicle"
    UNKNOWN = "unknown"


DomainClass = ObjectKind


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


class ObservationStatus(StrEnum):
    OBSERVED = "OBSERVED"
    PARTIALLY_OBSERVED = "PARTIALLY_OBSERVED"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    UNOBSERVED = "UNOBSERVED"
    FAILED = "FAILED"


class Uncertainty(Model):
    detection_confidence: float = Field(default=0, ge=0, le=1)
    tracking_confidence: float = Field(default=0, ge=0, le=1)
    lane_assignment_confidence: float = Field(default=0, ge=0, le=1)
    motion_confidence: float = Field(default=0, ge=0, le=1)
    queue_confidence: float = Field(default=0, ge=0, le=1)
    observation_confidence: float = Field(default=0, ge=0, le=1)


class ObservationEvidence(Model):
    observation_status: ObservationStatus = ObservationStatus.UNOBSERVED
    measurement_timestamp: float | None = Field(default=None, ge=0)
    measurement_age_ms: float | None = Field(default=None, ge=0)
    source_camera_ids: tuple[str, ...] = ()
    camera_health: dict[str, bool | None] = Field(default_factory=dict)
    visibility_score: float = Field(default=0, ge=0, le=1)
    detection_coverage: float = Field(default=0, ge=0, le=1)
    tracking_quality: float = Field(default=0, ge=0, le=1)
    calibration_status: str = "missing"
    measurement_confidence: float = Field(default=0, ge=0, le=1)
    failure_reasons: tuple[str, ...] = ("evidence_missing",)
    uncertainty: Uncertainty = Field(default_factory=Uncertainty)

    @property
    def reliable(self) -> bool:
        return (
            self.observation_status == ObservationStatus.OBSERVED
            and self.measurement_timestamp is not None
            and self.measurement_age_ms is not None
            and bool(self.source_camera_ids)
            and self.calibration_status == "valid"
            and self.measurement_confidence > 0
            and not self.failure_reasons
            and (
                self.source_camera_ids == ("simulator",)
                or (
                    {
                        "camera_connected",
                        "frame_fresh",
                        "frame_rate_adequate",
                        "image_not_frozen",
                        "not_black",
                        "not_underexposed",
                        "not_overexposed",
                        "not_blurred",
                        "no_major_occlusion_suspected",
                        "roi_visible",
                        "inference_alive",
                        "analytics_alive",
                        "calibration_available",
                        "calibration_fresh",
                    }.issubset(self.camera_health)
                    and all(value is True for value in self.camera_health.values())
                )
            )
        )

    @classmethod
    def simulated(cls, timestamp: float = 0) -> "ObservationEvidence":
        return cls(
            observation_status=ObservationStatus.OBSERVED,
            measurement_timestamp=timestamp,
            measurement_age_ms=0,
            source_camera_ids=("simulator",),
            calibration_status="valid",
            visibility_score=1,
            detection_coverage=1,
            tracking_quality=1,
            measurement_confidence=1,
            failure_reasons=(),
            uncertainty=Uncertainty(**dict.fromkeys(Uncertainty.model_fields, 1)),
        )


class ApproachState(Model):
    direction: Direction
    counts: Counts | None = None
    queue_length: int | None = Field(default=None, ge=0)
    traffic_score: float | None = Field(default=None, ge=0)
    mean_wait_seconds: float | None = Field(default=None, ge=0)
    oldest_wait_seconds: float | None = Field(default=None, ge=0)
    arrival_rate_per_minute: float | None = Field(default=None, ge=0)
    congestion: Congestion | None = None
    observation: ObservationEvidence = Field(default_factory=ObservationEvidence)
    estimated_queue_count: int | None = Field(default=None, ge=0)
    pedestrian_count_waiting: int | None = Field(default=None, ge=0)
    pedestrian_oldest_wait_seconds: float | None = Field(default=None, ge=0)
    pedestrian_mean_wait_seconds: float | None = Field(default=None, ge=0)
    pedestrian_arrival_rate: float | None = Field(default=None, ge=0)
    pedestrian_crossing_count: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def invalid_is_not_empty(self) -> "ApproachState":
        required = (
            self.counts,
            self.queue_length,
            self.traffic_score,
            self.mean_wait_seconds,
            self.oldest_wait_seconds,
            self.arrival_rate_per_minute,
            self.congestion,
        )
        if self.observation.reliable and any(value is None for value in required):
            evidence = ObservationEvidence.model_validate(
                {
                    **self.observation.model_dump(),
                    "observation_status": ObservationStatus.DEGRADED,
                    "measurement_confidence": 0,
                    "failure_reasons": ("measurement_incomplete",),
                }
            )
            object.__setattr__(self, "observation", evidence)
        if not self.observation.reliable:
            for name in (
                "counts",
                "queue_length",
                "traffic_score",
                "mean_wait_seconds",
                "oldest_wait_seconds",
                "arrival_rate_per_minute",
                "congestion",
                "pedestrian_count_waiting",
                "pedestrian_oldest_wait_seconds",
                "pedestrian_mean_wait_seconds",
                "pedestrian_arrival_rate",
                "pedestrian_crossing_count",
            ):
                object.__setattr__(self, name, None)
        return self


class TrafficState(Model):
    simulation_time: float = Field(ge=0)
    approaches: tuple[ApproachState, ...]
    pedestrians_waiting: int | None = Field(ge=0)
    pedestrian_oldest_wait_seconds: float | None = Field(ge=0)

    @model_validator(mode="after")
    def complete_directions(self) -> "TrafficState":
        if len(self.approaches) != 4 or {a.direction for a in self.approaches} != set(Direction):
            raise ValueError("Exactly one state per direction is required")
        if any(not a.observation.reliable for a in self.approaches):
            object.__setattr__(self, "pedestrians_waiting", None)
            object.__setattr__(self, "pedestrian_oldest_wait_seconds", None)
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

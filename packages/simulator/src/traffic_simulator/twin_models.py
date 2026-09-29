"""Digital-twin contracts. Truth and observations deliberately have different types."""

import math
from typing import Literal

from pydantic import Field, model_validator
from traffic_core.models import Direction, Model, ObjectKind, TrafficState


class DemandPeriod(Model):
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    vehicles_per_minute: dict[Direction, float]
    pedestrians_per_minute: float = Field(default=6, ge=0, le=600)

    @model_validator(mode="after")
    def valid(self) -> "DemandPeriod":
        if self.end_s <= self.start_s or set(self.vehicles_per_minute) != set(Direction):
            raise ValueError("Demand period needs four approaches and positive duration")
        if any(
            not math.isfinite(v) or not 0 <= v <= 600 for v in self.vehicles_per_minute.values()
        ):
            raise ValueError("Vehicle rates must lie in [0,600] vehicle/min")
        return self


class Incident(Model):
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    downstream: Direction

    @model_validator(mode="after")
    def valid(self) -> "Incident":
        if self.end_s <= self.start_s:
            raise ValueError("Incident must have positive duration")
        return self


class TrafficProfile(Model):
    periods: tuple[DemandPeriod, ...] = Field(min_length=1)
    lanes_per_approach: int = Field(default=2, ge=1, le=4)
    lane_length_m: float = Field(default=90, ge=15, le=1000)
    downstream_capacity: int = Field(default=12, ge=1, le=1000)
    downstream_travel_s: float = Field(default=5, ge=0.25, le=120)
    headway_s: float = Field(default=2, ge=0.5, le=10)
    reaction_s: float = Field(default=1, ge=0, le=10)
    pedestrian_headway_s: float = Field(default=0.5, ge=0.25, le=10)
    crossing_s: float = Field(default=6, ge=1, le=60)
    turn_probabilities: tuple[float, float, float] = (0.15, 0.7, 0.15)  # left/straight/right
    vehicle_probabilities: tuple[float, float, float, float, float] = (0.7, 0.08, 0.1, 0.07, 0.05)
    arrival_distribution: Literal["poisson", "regular", "bursty"] = "poisson"
    burst_size: int = Field(default=4, ge=1, le=20)
    incidents: tuple[Incident, ...] = ()

    @model_validator(mode="after")
    def probabilities(self) -> "TrafficProfile":
        for values in (self.turn_probabilities, self.vehicle_probabilities):
            if any(not math.isfinite(v) or v < 0 for v in values) or not math.isclose(
                sum(values), 1
            ):
                raise ValueError("Probabilities must be finite, nonnegative and sum to one")
        return self


class CameraFault(Model):
    start_s: float = Field(ge=0)
    end_s: float = Field(gt=0)
    directions: tuple[Direction, ...] = tuple(Direction)
    mode: Literal["outage", "freeze", "occlusion", "calibration_invalid", "latency_spike"]

    @model_validator(mode="after")
    def valid(self) -> "CameraFault":
        if self.end_s <= self.start_s or not self.directions:
            raise ValueError("Fault needs positive duration and affected cameras")
        return self


class PerceptionProfile(Model):
    provenance: Literal["engineering_assumption", "vision_benchmark_fit"] = "engineering_assumption"
    benchmark_report_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    calibration_notes: str = "Uncalibrated stress-test parameters; not field estimates"
    missed_detection_probability: float = Field(default=0.03, ge=0, le=1)
    false_positives_per_frame: float = Field(default=0.01, ge=0, le=1)
    id_switch_probability: float = Field(default=0.001, ge=0, le=1)
    occlusion_probability: float = Field(default=0.01, ge=0, le=1)
    occlusion_duration_s: float = Field(default=0.5, ge=0, le=60)
    position_noise_std_m: float = Field(default=0.15, ge=0, le=10)
    wait_noise_std_s: float = Field(default=0.1, ge=0, le=30)
    queue_undercount_probability: float = Field(default=0.02, ge=0, le=1)
    camera_delay_s: float = Field(default=0.05, ge=0, le=30)
    inference_latency_s: float = Field(default=0.1, ge=0, le=30)
    frame_drop_probability: float = Field(default=0.01, ge=0, le=1)
    fps: float = Field(default=4, ge=0.1, le=30)
    minimum_fps: float = Field(default=0.8, gt=0, le=30)
    stale_after_s: float = Field(default=3, gt=0, le=60)
    freeze_detection_s: float = Field(default=1, ge=0, le=30)
    track_ttl_s: float = Field(default=2, gt=0, le=60)
    camera_availability: float = Field(default=1, ge=0, le=1)
    calibration_scale_error: float = Field(default=0.01, ge=-0.5, le=0.5)
    faults: tuple[CameraFault, ...] = ()

    @model_validator(mode="after")
    def evidence(self) -> "PerceptionProfile":
        if self.provenance == "vision_benchmark_fit" and not self.benchmark_report_sha256:
            raise ValueError("Benchmark-derived parameters require a source report hash")
        return self


class RandomRange(Model):
    low: float
    high: float

    @model_validator(mode="after")
    def ordered(self) -> "RandomRange":
        if self.low > self.high:
            raise ValueError("Randomization range is reversed")
        return self


class DomainRandomizationProfile(Model):
    enabled: bool = False
    rationale: str = "Narrow engineering sensitivity ranges; not empirical uncertainty bounds"
    arrival_multiplier: RandomRange = RandomRange(low=0.9, high=1.1)
    headway_multiplier: RandomRange = RandomRange(low=0.95, high=1.05)
    reaction_multiplier: RandomRange = RandomRange(low=0.9, high=1.1)
    noise_multiplier: RandomRange = RandomRange(low=0.9, high=1.1)
    latency_multiplier: RandomRange = RandomRange(low=0.9, high=1.1)
    occlusion_multiplier: RandomRange = RandomRange(low=0.9, high=1.1)
    camera_availability: RandomRange = RandomRange(low=0.995, high=1)
    arrival_distributions: tuple[Literal["poisson", "regular", "bursty"], ...] = ("poisson",)

    @model_validator(mode="after")
    def bounded(self) -> "DomainRandomizationProfile":
        ranges = (
            self.arrival_multiplier,
            self.headway_multiplier,
            self.reaction_multiplier,
            self.noise_multiplier,
            self.latency_multiplier,
            self.occlusion_multiplier,
        )
        if any(r.low < 0 or r.high > 3 for r in ranges) or not self.arrival_distributions:
            raise ValueError("Sensitivity multipliers must be within [0,3]")
        if not 0 <= self.camera_availability.low <= self.camera_availability.high <= 1:
            raise ValueError("Availability is a probability")
        return self


class ScenarioDefinition(Model):
    schema_version: Literal["rohai-twin-1"] = "rohai-twin-1"
    scenario_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    seed: int = Field(default=42, ge=0)
    duration_s: float = Field(default=180, gt=0, le=3600)
    step_s: float = Field(default=0.25, ge=0.05, le=0.5)
    traffic: TrafficProfile
    perception: PerceptionProfile = Field(default_factory=PerceptionProfile)
    randomization: DomainRandomizationProfile = Field(default_factory=DomainRandomizationProfile)
    expected_invariants: tuple[
        Literal[
            "conservation",
            "finite_storage",
            "no_conflicting_service",
            "observed_only_control",
            "finite_metrics",
        ],
        ...,
    ] = (
        "conservation",
        "finite_storage",
        "no_conflicting_service",
        "observed_only_control",
        "finite_metrics",
    )

    @model_validator(mode="after")
    def time_coverage(self) -> "ScenarioDefinition":
        cursor = 0.0
        for period in self.traffic.periods:
            if not math.isclose(period.start_s, cursor):
                raise ValueError("Demand periods must cover duration without gaps/overlap")
            cursor = period.end_s
        if not math.isclose(cursor, self.duration_s):
            raise ValueError("Demand periods must cover the exact scenario duration")
        if not math.isclose(self.duration_s / self.step_s, round(self.duration_s / self.step_s)):
            raise ValueError("Duration must be an integer number of physics ticks")
        if self.perception.fps * self.step_s > 1:
            raise ValueError("Physics tick must resolve every camera capture (fps*step <= 1)")
        return self


class TruthEntity(Model):
    entity_id: int = Field(ge=1)
    kind: ObjectKind
    approach: Direction
    lane_id: str
    turn: Literal["left", "straight", "right"] = "straight"
    arrived_at_s: float = Field(ge=0)
    admitted_at_s: float | None = Field(default=None, ge=0)
    distance_to_stop_m: float = Field(ge=0)
    length_m: float = Field(gt=0)


class GroundTruthState(Model):
    timestamp_s: float = Field(ge=0)
    queued: tuple[TruthEntity, ...]
    upstream: tuple[TruthEntity, ...] = ()
    crossing_pedestrians: int = Field(default=0, ge=0)
    downstream_vehicles: int = Field(default=0, ge=0)
    arrived: int = Field(default=0, ge=0)
    departed: int = Field(default=0, ge=0)
    stop_line_departures: int = Field(default=0, ge=0)
    total_wait_s: float = Field(default=0, ge=0)
    pedestrian_wait_s: float = Field(default=0, ge=0)
    pedestrian_departed: int = Field(default=0, ge=0)
    spillback_s: float = Field(default=0, ge=0)
    blocked_service_s: float = Field(default=0, ge=0)
    downstream_delay_s: float = Field(default=0, ge=0)


class ServiceRecord(Model):
    entity_id: int
    kind: ObjectKind
    approach: Direction
    queue_wait_s: float = Field(ge=0)
    served_at_s: float = Field(ge=0)
    downstream_delay_s: float = Field(default=0, ge=0)
    departed: bool = True


class ObservedTrack(Model):
    track_id: str
    kind: ObjectKind
    approach: Direction
    lane_id: str
    distance_to_stop_m: float | None
    estimated_speed_mps: float | None
    observed_wait_s: float


class ObservedTrafficState(Model):
    source: Literal["digital_twin_observation"] = "digital_twin_observation"
    traffic: TrafficState
    tracks: tuple[ObservedTrack, ...] = ()

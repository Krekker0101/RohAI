from enum import StrEnum
from typing import Literal

from pydantic import Field, computed_field, model_validator
from traffic_core.models import (
    Model,
    ObjectKind,
    ObservationEvidence,
    ObservationStatus,
    Uncertainty,
)
from traffic_core.traffic_events import RoadTrackState, TrafficEstimation

from traffic_vision.contracts import BoundingBox


class Lane(StrEnum):
    NORTH = "north"
    SOUTH = "south"
    EAST = "east"
    WEST = "west"
    UNKNOWN = "unknown"


class Point(Model):
    x: float
    y: float


class Detection(Model):
    ground_contact: Point | None = None
    kind: ObjectKind
    confidence: float = Field(ge=0, le=1)
    bbox: BoundingBox
    timestamp: float = Field(ge=0)
    track_id: int | None = Field(default=None, ge=0)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def centroid(self) -> Point:
        return Point(x=(self.bbox.x1 + self.bbox.x2) / 2, y=(self.bbox.y1 + self.bbox.y2) / 2)

    @property
    def footpoint(self) -> Point:
        return self.ground_contact or Point(x=(self.bbox.x1 + self.bbox.x2) / 2, y=self.bbox.y2)


class TrajectoryPoint(Model):
    timestamp: float
    point: Point


class TrackVisibility(StrEnum):
    CONFIRMED_VISIBLE = "CONFIRMED_VISIBLE"
    TEMPORARILY_OCCLUDED = "TEMPORARILY_OCCLUDED"
    PREDICTED = "PREDICTED"
    LOST = "LOST"
    EXPIRED = "EXPIRED"


class PedestrianState(StrEnum):
    WAITING = "pedestrian_waiting"
    CROSSING = "pedestrian_crossing"
    CLEARING = "pedestrian_clearing"
    UNKNOWN = "pedestrian_unknown"


class TrackSnapshot(Model):
    road_state: RoadTrackState | None = None
    visible: bool = True
    track_state: TrackVisibility = TrackVisibility.CONFIRMED_VISIBLE
    pedestrian_state: PedestrianState = PedestrianState.UNKNOWN
    uncertainty: Uncertainty = Field(default_factory=Uncertainty)
    track_id: int
    track_uid: str
    kind: ObjectKind
    confidence: float
    bbox: BoundingBox
    lane: Lane
    first_seen: float
    last_seen: float
    trajectory: tuple[TrajectoryPoint, ...]
    speed_px_per_second: float | None
    waiting_started_at: float | None
    current_wait_time: float = Field(ge=0)
    total_observed_wait_seconds: float = Field(ge=0)
    consecutive_slow_frames: int
    queued: bool
    queue_confidence: float


class DirectionMetrics(Model):
    observation: ObservationEvidence = Field(default_factory=ObservationEvidence)
    estimated_queue_count: int | None = Field(default=None, ge=0)
    pedestrian_count_waiting: int | None = Field(default=None, ge=0)
    pedestrian_oldest_wait_seconds: float | None = Field(default=None, ge=0)
    pedestrian_mean_wait_seconds: float | None = Field(default=None, ge=0)
    pedestrian_arrival_rate: float | None = Field(default=None, ge=0)
    pedestrian_crossing_count: int | None = Field(default=None, ge=0)
    pedestrian_clearing_count: int | None = Field(default=None, ge=0)
    pedestrian_unknown_count: int | None = Field(default=None, ge=0)
    car_count: int | None = Field(default=None, ge=0)
    bus_count: int | None = Field(default=None, ge=0)
    truck_count: int | None = Field(default=None, ge=0)
    motorcycle_count: int | None = Field(default=None, ge=0)
    bicycle_count: int | None = Field(default=None, ge=0)
    pedestrian_count: int | None = Field(default=None, ge=0)
    queue_count: int | None = Field(default=None, ge=0)
    queue_vehicle_count: int | None = Field(default=None, ge=0)
    estimated_queue_length: float | None = Field(default=None, ge=0)
    queue_length_unit: Literal["px", "m"] = "px"
    queue_confidence: float = 0
    average_wait_seconds: float | None = Field(default=None, ge=0)
    max_wait_seconds: float | None = Field(default=None, ge=0)
    arrival_rate: float | None = Field(default=None, ge=0)
    arrival_rate_unit: Literal["objects/min"] = "objects/min"
    unique_arrivals: int | None = Field(default=None, ge=0)
    traffic_score: float | None = Field(default=None, ge=0)
    congestion: str | None = None

    @model_validator(mode="after")
    def invalid_is_not_empty(self) -> "DirectionMetrics":
        required = (
            self.queue_count,
            self.queue_vehicle_count,
            self.traffic_score,
            self.average_wait_seconds,
            self.max_wait_seconds,
            self.arrival_rate,
            self.car_count,
            self.bus_count,
            self.truck_count,
            self.motorcycle_count,
            self.bicycle_count,
            self.pedestrian_count,
            self.estimated_queue_length,
            self.unique_arrivals,
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
            for name in type(self).model_fields:
                if name not in (
                    "observation",
                    "estimated_queue_count",
                    "queue_length_unit",
                    "arrival_rate_unit",
                    "queue_confidence",
                ):
                    object.__setattr__(self, name, None)
        return self


class VisionTrafficState(Model):
    estimation: TrafficEstimation | None = None
    timestamp: float
    source_id: str
    stream_epoch: int
    frame_id: int
    directions: dict[str, DirectionMetrics]
    unknown_count: int
    unique_vehicle_arrivals: int
    unique_pedestrian_arrivals: int


class AnalysisSnapshot(Model):
    traffic: VisionTrafficState
    tracks: tuple[TrackSnapshot, ...]
    detections: tuple[Detection, ...]


class PipelineStatus(Model):
    state: Literal["starting", "running", "ended", "failed", "stopped"]
    captured: int
    inferred: int
    analyzed: int
    published: int
    dropped_capture: int
    dropped_inference: int
    dropped_analysis: int
    processing_fps: float
    latency_ms: float
    error: str | None

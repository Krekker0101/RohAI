from enum import StrEnum
from typing import Literal

from pydantic import Field, computed_field
from traffic_core.models import Model, ObjectKind

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
        return Point(x=(self.bbox.x1 + self.bbox.x2) / 2, y=self.bbox.y2)


class TrajectoryPoint(Model):
    timestamp: float
    point: Point


class TrackSnapshot(Model):
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
    current_wait_time: float
    total_observed_wait_seconds: float
    consecutive_slow_frames: int
    queued: bool
    queue_confidence: float


class DirectionMetrics(Model):
    car_count: int = 0
    bus_count: int = 0
    truck_count: int = 0
    motorcycle_count: int = 0
    bicycle_count: int = 0
    pedestrian_count: int = 0
    queue_count: int = 0
    queue_vehicle_count: int = 0
    estimated_queue_length: float = 0
    queue_length_unit: Literal["px", "m"] = "px"
    queue_confidence: float = 0
    average_wait_seconds: float = 0
    max_wait_seconds: float = 0
    arrival_rate: float = 0
    arrival_rate_unit: Literal["objects/min"] = "objects/min"
    unique_arrivals: int = 0
    traffic_score: float = 0
    congestion: str = "low"


class VisionTrafficState(Model):
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

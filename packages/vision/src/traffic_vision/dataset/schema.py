"""Annotation depth is explicit: unannotated is not a negative label."""

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from traffic_core.models import Direction, Model, ObjectKind

from traffic_vision.contracts import BoundingBox

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]


class Split(StrEnum):
    TRAIN = "TRAIN"
    VALIDATION = "VALIDATION"
    TEST = "TEST"
    CHALLENGE = "CHALLENGE"


class Layer(StrEnum):
    DETECTION = "detection"
    TRACKING = "tracking"
    ANALYTICS = "traffic_analytics"
    PEDESTRIAN = "pedestrian"
    CALIBRATION = "calibration"


FEATURE_UNITS = {
    "vehicle_count": "vehicle",
    "arrival_count": "vehicle",
    "queue_count": "vehicle",
    "queue_length_m": "m",
    "mean_wait_seconds": "s",
    "oldest_wait_seconds": "s",
    "throughput": "vehicle",
    "arrival_rate_per_minute": "vehicle/min",
    "traffic_score": "score",
    "counts.cars": "vehicle",
    "counts.buses": "vehicle",
    "counts.trucks": "vehicle",
    "counts.motorcycles": "vehicle",
    "counts.bicycles": "vehicle",
    "counts.pedestrians": "person",
    "pedestrian_count_waiting": "person",
    "pedestrian_oldest_wait_seconds": "s",
    "pedestrian_mean_wait_seconds": "s",
    "pedestrian_arrival_rate": "person/min",
    "pedestrian_crossing_count": "person",
    "pedestrian_clearing_count": "person",
    "pedestrian_unknown_count": "person",
    "occupancy": "fraction",
    "estimated_speed_mps": "m/s",
}
CONTROL_FEATURES = (
    "queue_count",
    "traffic_score",
    "mean_wait_seconds",
    "oldest_wait_seconds",
    "arrival_rate_per_minute",
    "counts.cars",
    "counts.buses",
    "counts.trucks",
    "counts.motorcycles",
    "counts.bicycles",
    "counts.pedestrians",
    "pedestrian_count_waiting",
    "pedestrian_oldest_wait_seconds",
    "pedestrian_mean_wait_seconds",
    "pedestrian_arrival_rate",
    "pedestrian_crossing_count",
)


class Asset(Model):
    path: str = Field(min_length=1, max_length=512)
    sha256: Digest

    @field_validator("path")
    @classmethod
    def portable_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or any(p in ("..", ".") for p in value.split("/"))
            or ":" in value
            or "\\" in value
            or "//" in value
        ):
            raise ValueError("Assets require portable relative paths without traversal")
        return value


class Conditions(Model):
    day_night: Literal["day", "night", "twilight", "unknown"]
    weather: Literal["dry", "rain", "snow", "fog", "mixed", "unknown"]
    traffic_density: Literal["low", "moderate", "high", "unknown"]
    occlusion: Literal["none", "light", "heavy", "unknown"]
    camera_angle: Literal["overhead", "oblique", "low", "unknown"]
    lighting: Literal["good", "low", "glare", "backlit", "unknown"]
    tilt_degrees: float | None = Field(default=None, ge=0, le=90)


class SourceVideo(Model):
    source_id: Identifier
    recording_id: Identifier  # SAME ID for all clips/transcodes of one original recording.
    camera_id: Identifier
    intersection_id: Identifier
    video: Asset
    captured_at: datetime  # Keep acquisition timezone: date/time derive from this.
    duration_seconds: float = Field(gt=0, le=604800)
    fps: float = Field(gt=0, le=240)
    width: int = Field(gt=0, le=16384)
    height: int = Field(gt=0, le=16384)
    conditions: Conditions
    provenance: str = Field(min_length=1)
    usage: Literal["field", "demo", "synthetic_fixture"]
    pretrained_exposure: Literal["unknown", "known_overlap", "audited_no_known_overlap"]
    geometry: Asset | None = None
    alignment: Asset | None = None  # Recorded independent static-landmark observations.

    @field_validator("captured_at")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("Acquisition time needs timezone")
        return value


class ObjectAnnotation(Model):
    annotation_id: Identifier
    bbox: BoundingBox  # visible xyxy pixel extent, no fabricated amodal box
    class_id: int = Field(ge=0)
    domain_class: ObjectKind
    track_id: Identifier | None = None
    lane_id: Identifier | None = None
    approach: Direction | None = None
    queue_member: bool | None = None
    stopped: bool | None = None
    pedestrian_state: Literal["waiting", "crossing", "clearing", "unknown"] | None = None
    occluded: bool = False
    truncated: bool = False
    ignore: bool = False


class EventAnnotation(Model):
    event_id: Identifier
    kind: Literal[
        "entry",
        "stop",
        "started_moving",
        "joined_queue",
        "stop_line_crossing",
        "exit",
        "pedestrian_arrived",
        "pedestrian_started_crossing",
        "pedestrian_cleared",
    ]
    timestamp: float = Field(ge=0)
    track_id: Identifier
    approach: Direction
    lane_id: Identifier | None = None
    uncertainty_seconds: float = Field(default=0, ge=0)


class FeatureTruth(Model):
    feature: str
    approach: Direction
    value: float = Field(ge=0)
    unit: str
    method: Literal["manual_count", "survey", "timed_track", "derived_reviewed"]

    @model_validator(mode="after")
    def units(self) -> "FeatureTruth":
        if self.feature not in FEATURE_UNITS or FEATURE_UNITS[self.feature] != self.unit:
            raise ValueError("Unknown feature or wrong physical unit")
        if self.unit in ("vehicle", "person") and not self.value.is_integer():
            raise ValueError("Counts must be integers")
        if self.feature == "occupancy" and self.value > 1:
            raise ValueError("Occupancy must lie in [0,1]")
        return self


class FrameAnnotation(Model):
    schema_version: Literal["1.0"] = "1.0"
    frame_id: Identifier
    layers: tuple[Layer, ...] = ()
    exhaustive_classes: tuple[int, ...] = ()
    provenance: Literal[
        "human", "assisted_human_reviewed", "model_predictions", "synthetic_fixture", "context_only"
    ]
    reviewed_by: str | None = None
    control_observable: bool | None = None  # Human-reviewed all-approach observability, if labeled.
    objects: tuple[ObjectAnnotation, ...] = Field(default=(), max_length=2000)
    events: tuple[EventAnnotation, ...] = Field(default=(), max_length=2000)
    traffic: tuple[FeatureTruth, ...] = Field(default=(), max_length=512)

    @model_validator(mode="after")
    def reviewed(self) -> "FrameAnnotation":
        if not self.layers and (
            self.provenance != "context_only"
            or self.objects
            or self.events
            or self.traffic
            or self.exhaustive_classes
        ):
            raise ValueError("Unlabeled context must be explicit and contain no ground truth")
        if self.layers and self.provenance == "context_only":
            raise ValueError("Context frames cannot declare annotation layers")
        if self.provenance in ("human", "assisted_human_reviewed") and not self.reviewed_by:
            raise ValueError("Human annotations need reviewer attribution")
        if len(set(self.layers)) != len(self.layers) or len(set(self.exhaustive_classes)) != len(
            self.exhaustive_classes
        ):
            raise ValueError("Duplicate annotation layer/class coverage")
        return self


class FrameRecord(Model):
    frame_id: Identifier
    source_id: Identifier
    sequence_id: Identifier | None = None
    frame_index: int = Field(ge=0)
    timestamp: float = Field(ge=0)
    image: Asset
    annotation: Asset


class SequenceSpec(Model):
    sequence_id: Identifier
    source_id: Identifier
    start_frame: int = Field(ge=0)
    end_frame: int = Field(ge=0)
    stride: int = Field(default=1, ge=1)
    layers: tuple[Layer, ...] = Field(min_length=1)
    # State-estimation warmup is explicit; all frames still run inference.
    score_from_seconds: float = Field(default=0, ge=0)

    @model_validator(mode="after")
    def ordered(self) -> "SequenceSpec":
        if self.end_frame < self.start_frame or (self.end_frame - self.start_frame) % self.stride:
            raise ValueError("Invalid sequence interval/stride")
        return self


class SplitDefinition(Model):
    seed: int = 2026
    group_by: tuple[Literal["recording", "camera", "day"], ...] = ("recording", "camera", "day")
    assignments: dict[str, Split] = Field(
        default_factory=dict
    )  # Source-video IDs, NEVER frame IDs.
    independence_exceptions: str | None = None

    @model_validator(mode="after")
    def independent(self) -> "SplitDefinition":
        if "recording" not in self.group_by or len(set(self.group_by)) != len(self.group_by):
            raise ValueError("Recording grouping is mandatory and keys must be unique")
        if (
            set(self.group_by) != {"recording", "camera", "day"}
            and not self.independence_exceptions
        ):
            raise ValueError("Reduced independence requires an explicit documented exception")
        return self


class DatasetSpec(Model):
    dataset_id: Identifier
    version: Identifier
    annotation_schema_version: Literal["1.0"] = "1.0"
    created_at: datetime
    purpose: Literal["field", "synthetic_fixture"]
    class_mapping: dict[int, ObjectKind]
    sources: tuple[SourceVideo, ...] = Field(default=(), max_length=10000)
    frames: tuple[FrameRecord, ...] = Field(default=(), max_length=1000000)
    sequences: tuple[SequenceSpec, ...] = ()
    split: SplitDefinition

    @model_validator(mode="after")
    def valid_mapping(self) -> "DatasetSpec":
        if self.created_at.tzinfo is None:
            raise ValueError("Release timestamp requires timezone")
        if not self.class_mapping or set(self.class_mapping) != set(range(len(self.class_mapping))):
            raise ValueError("Domain class IDs must be contiguous from zero")
        if ObjectKind.UNKNOWN in self.class_mapping.values() or len(
            set(self.class_mapping.values())
        ) != len(self.class_mapping):
            raise ValueError("Unknown/duplicate domain class mapping")
        return self


class ReleaseManifest(Model):
    format: Literal["rohai-dataset-release-1"] = "rohai-dataset-release-1"
    dataset: DatasetSpec
    files: dict[str, str]
    statistics: dict[str, object]
    manifest_sha256: Digest

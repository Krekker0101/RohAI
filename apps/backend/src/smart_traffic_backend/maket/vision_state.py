"""Frame-derived estimates, not ground truth or independent control authority."""

from typing import Literal

from pydantic import Field
from traffic_core.models import Model, ObjectKind
from traffic_vision.models import AnalysisSnapshot, Detection, PipelineStatus, TrackSnapshot

from smart_traffic_backend.maket.config import MaketCalibration


class DirectionEstimate(Model):
    direction_id: str
    lane_ids: tuple[str, ...] = ()
    moving_count: int | None = None
    stopped_count: int | None = None
    occluded_count: int = 0
    queue_count: int | None = None
    estimated_queue_length: float | None = None
    queue_length_unit: str
    estimated_wait_seconds: float | None = None
    confidence: float = Field(ge=0, le=1)
    fresh: bool


class StandVisionState(Model):
    provenance: Literal["FRAME_DERIVED_ESTIMATE"] = "FRAME_DERIVED_ESTIMATE"
    frame_timestamp: float
    captured_at_monotonic: float
    camera_id: str
    frame_id: int
    stream_epoch: int
    frame_width: int
    frame_height: int
    calibration_version: int | None
    calibration_confirmed: bool
    detected_objects: tuple[Detection, ...]
    active_tracks: tuple[TrackSnapshot, ...]
    track_lanes: dict[str, str | None]
    directions: tuple[DirectionEstimate, ...]
    data_age_ms: float
    fresh: bool
    pipeline_health: PipelineStatus


def estimate(
    analysis: AnalysisSnapshot,
    *,
    captured_at: float,
    now: float,
    width: int,
    height: int,
    calibration: MaketCalibration | None,
    confirmed: bool,
    status: PipelineStatus,
    slow_speed: float,
    max_age: float,
) -> StandVisionState:
    age = max(0, now - captured_at)
    fresh = age <= max_age and status.state == "running"
    directions = []
    track_lanes: dict[str, str | None] = {t.track_uid: None for t in analysis.tracks}
    if confirmed and calibration:
        from traffic_vision.models import Point

        for track in analysis.tracks:
            point = Point(x=(track.bbox.x1 + track.bbox.x2) / 2 / width, y=track.bbox.y2 / height)
            matches = [
                f"{a.direction.value}:{name}"
                for a in calibration.geometry.approaches
                for name, polygon in a.lanes.items()
                if polygon.contains(point)
            ]
            track_lanes[track.track_uid] = matches[0] if len(matches) == 1 else None
    for direction, metrics in analysis.traffic.directions.items():
        tracks = [
            t
            for t in analysis.tracks
            if t.lane.value == direction and t.kind != ObjectKind.PEDESTRIAN
        ]
        visible = [t for t in tracks if t.visible and t.speed_px_per_second is not None]
        reliable = fresh and confirmed and metrics.observation.reliable
        approach = (
            next(
                (a for a in calibration.geometry.approaches if a.direction.value == direction), None
            )
            if calibration
            else None
        )
        directions.append(
            DirectionEstimate(
                direction_id=direction,
                lane_ids=tuple(approach.lanes) if approach and confirmed else (),
                moving_count=sum(
                    t.speed_px_per_second > slow_speed
                    for t in visible
                    if t.speed_px_per_second is not None
                )
                if reliable
                else None,
                stopped_count=sum(
                    t.speed_px_per_second <= slow_speed
                    for t in visible
                    if t.speed_px_per_second is not None
                )
                if reliable
                else None,
                occluded_count=sum(not t.visible for t in tracks),
                queue_count=metrics.queue_vehicle_count if reliable else None,
                estimated_queue_length=metrics.estimated_queue_length if reliable else None,
                queue_length_unit=metrics.queue_length_unit,
                estimated_wait_seconds=metrics.max_wait_seconds if reliable else None,
                confidence=metrics.observation.measurement_confidence if reliable else 0,
                fresh=fresh,
            )
        )
    return StandVisionState(
        frame_timestamp=analysis.traffic.timestamp,
        captured_at_monotonic=captured_at,
        camera_id=analysis.traffic.source_id,
        frame_id=analysis.traffic.frame_id,
        stream_epoch=analysis.traffic.stream_epoch,
        frame_width=width,
        frame_height=height,
        calibration_version=calibration.version if calibration else None,
        calibration_confirmed=confirmed,
        detected_objects=analysis.detections,
        active_tracks=analysis.tracks,
        track_lanes=track_lanes,
        directions=tuple(directions),
        data_age_ms=age * 1000,
        fresh=fresh,
        pipeline_health=status,
    )

import math
from collections import deque
from dataclasses import dataclass, field

from traffic_core.models import ApproachState, Direction, ObjectKind, TrafficState
from traffic_core.state import build_approach_state

from traffic_vision.geometry import IntersectionGeometry
from traffic_vision.models import (
    AnalysisSnapshot,
    Detection,
    DirectionMetrics,
    Lane,
    Point,
    TrackSnapshot,
    TrajectoryPoint,
    VisionTrafficState,
)
from traffic_vision.settings import AnalyticsSettings


@dataclass
class Track:
    identity: int
    first_seen: float
    last_seen: float
    detection: Detection
    trajectory: deque[TrajectoryPoint] = field(default_factory=deque)
    lane: Lane = Lane.UNKNOWN
    speed: float | None = None
    visible: bool = True
    slow_frames: int = 0
    candidate_since: float | None = None
    waiting_started_at: float | None = None
    current_wait: float = 0
    total_wait: float = 0
    queued: bool = False

    def reset_wait(self) -> None:
        self.slow_frames = 0
        self.candidate_since = None
        self.waiting_started_at = None
        self.current_wait = 0
        self.queued = False


class TrafficAnalyzer:
    """Track lifecycle and traffic state, independent of YOLO, OpenCV and transport."""

    def __init__(self, geometry: IntersectionGeometry, settings: AnalyticsSettings) -> None:
        self.geometry = geometry
        self.settings = settings
        self.tracks: dict[int, Track] = {}
        self.counted: set[int] = set()
        self.arrivals: dict[Lane, deque[float]] = {Lane(d.value): deque() for d in Direction}
        self.totals: dict[Lane, int] = dict.fromkeys(self.arrivals, 0)
        self.vehicle_arrivals = 0
        self.pedestrian_arrivals = 0
        self._epoch: int | None = None
        self._first_timestamp = 0.0
        self._last_frame_id = -1
        self._last_timestamp = -1.0
        self._latest: AnalysisSnapshot | None = None

    def process(
        self,
        detections: tuple[Detection, ...],
        *,
        source_id: str,
        timestamp: float,
        frame_id: int,
        width: int,
        height: int,
        stream_epoch: int = 0,
    ) -> AnalysisSnapshot:
        if source_id != self.geometry.source_id:
            raise ValueError("Geometry belongs to a different camera")
        if width <= 0 or height <= 0 or not math.isfinite(timestamp) or timestamp < 0:
            raise ValueError("Invalid frame dimensions or timestamp")
        if stream_epoch != self._epoch:
            self._reset(stream_epoch, timestamp)
        if frame_id == self._last_frame_id:
            if timestamp != self._last_timestamp or self._latest is None:
                raise ValueError("Duplicate frame ID with a different timestamp")
            return self._latest
        if frame_id < self._last_frame_id or timestamp <= self._last_timestamp:
            raise ValueError("Frames must be strictly ordered within a stream epoch")
        self._last_timestamp = timestamp
        self._last_frame_id = frame_id
        for identity in list(self.tracks):
            if timestamp - self.tracks[identity].last_seen > self.settings.lost_track_seconds:
                del self.tracks[identity]
        # A tracker should not emit duplicate IDs, but duplicate proposals must never double-count.
        observed: dict[int, Detection] = {}
        for detection in detections:
            if detection.timestamp != timestamp:
                raise ValueError("Detection timestamp does not match its frame")
            if detection.track_id is not None:
                previous = observed.get(detection.track_id)
                if previous is None or previous.confidence < detection.confidence:
                    observed[detection.track_id] = detection
        for identity, track in self.tracks.items():
            if identity not in observed:
                track.visible = False
                track.reset_wait()
        for identity, detection in observed.items():
            self._observe(identity, detection, timestamp, width, height)
        for history in self.arrivals.values():
            while history and history[0] <= timestamp - self.settings.arrival_window_seconds:
                history.popleft()
        tracks = tuple(
            self._snapshot(t, source_id, stream_epoch) for t in self.tracks.values() if t.visible
        )
        directions = {
            d.value: self._metrics(Lane(d.value), tracks, timestamp, width, height)
            for d in Direction
        }
        self._latest = AnalysisSnapshot(
            traffic=VisionTrafficState(
                timestamp=timestamp,
                source_id=source_id,
                stream_epoch=stream_epoch,
                frame_id=frame_id,
                directions=directions,
                unknown_count=sum(t.lane == Lane.UNKNOWN for t in tracks),
                unique_vehicle_arrivals=self.vehicle_arrivals,
                unique_pedestrian_arrivals=self.pedestrian_arrivals,
            ),
            tracks=tracks,
            detections=detections,
        )
        return self._latest

    def _reset(self, epoch: int, timestamp: float) -> None:
        self._epoch = epoch
        self._first_timestamp = timestamp
        self._last_timestamp = -1
        self._last_frame_id = -1
        self.tracks.clear()
        self.counted.clear()
        for history in self.arrivals.values():
            history.clear()
        self.totals = dict.fromkeys(self.arrivals, 0)
        self.vehicle_arrivals = self.pedestrian_arrivals = 0

    def _observe(
        self, identity: int, detection: Detection, now: float, width: int, height: int
    ) -> None:
        point = Point(x=detection.footpoint.x / width, y=detection.footpoint.y / height)
        track = self.tracks.get(identity)
        if track is None:
            if len(self.tracks) >= self.settings.max_session_tracks:
                raise RuntimeError("Track capacity exceeded")
            track = Track(
                identity, now, now, detection, deque(maxlen=self.settings.trajectory_length)
            )
            self.tracks[identity] = track
        gap = now - track.last_seen
        continuous = (
            bool(track.trajectory)
            and track.visible
            and 0 < gap <= self.settings.max_observation_gap_seconds
        )
        if continuous:
            previous = track.trajectory[-1].point
            speed = (
                math.hypot(
                    (point.x - previous.x) * self.geometry.reference_width,
                    (point.y - previous.y) * self.geometry.reference_height,
                )
                / gap
            )
            alpha = self.settings.speed_smoothing
            track.speed = (
                speed if track.speed is None else alpha * speed + (1 - alpha) * track.speed
            )
        else:
            track.speed = None
            track.reset_wait()
        track.lane = self.geometry.assign(
            point, track.lane, detection.kind == ObjectKind.PEDESTRIAN
        )
        track.detection = detection
        track.last_seen = now
        track.visible = True
        track.trajectory.append(TrajectoryPoint(timestamp=now, point=point))
        approach = self.geometry.approach(track.lane)
        eligible = (
            detection.kind != ObjectKind.PEDESTRIAN
            and approach is not None
            and approach.queue_zone.contains(point)
            and approach.stop_line.is_before(point)
            and track.speed is not None
            and track.speed <= self.settings.slow_speed_px_per_second
        )
        if eligible:
            track.slow_frames += 1
            if track.candidate_since is None:
                track.candidate_since = now
            if track.slow_frames >= self.settings.slow_frames:
                previous_wait = track.current_wait
                track.waiting_started_at = track.candidate_since
                track.current_wait = now - track.candidate_since
                track.total_wait += track.current_wait - previous_wait
                track.queued = True
        else:
            track.reset_wait()
        if track.lane != Lane.UNKNOWN and identity not in self.counted:
            if len(self.counted) >= self.settings.max_session_tracks:
                raise RuntimeError(
                    "Session identity budget exceeded; start a new monitored session"
                )
            self.counted.add(identity)
            self.arrivals[track.lane].append(now)
            self.totals[track.lane] += 1
            if detection.kind == ObjectKind.PEDESTRIAN:
                self.pedestrian_arrivals += 1
            else:
                self.vehicle_arrivals += 1

    def _snapshot(self, track: Track, source_id: str, epoch: int) -> TrackSnapshot:
        return TrackSnapshot(
            track_id=track.identity,
            track_uid=f"{source_id}:{epoch}:{track.identity}",
            kind=track.detection.kind,
            confidence=track.detection.confidence,
            bbox=track.detection.bbox,
            lane=track.lane,
            first_seen=track.first_seen,
            last_seen=track.last_seen,
            trajectory=tuple(track.trajectory),
            speed_px_per_second=track.speed,
            waiting_started_at=track.waiting_started_at,
            current_wait_time=track.current_wait,
            total_observed_wait_seconds=track.total_wait,
            consecutive_slow_frames=track.slow_frames,
            queued=track.queued,
            queue_confidence=track.detection.confidence if track.queued else 0,
        )

    def _metrics(
        self, lane: Lane, tracks: tuple[TrackSnapshot, ...], now: float, width: int, height: int
    ) -> DirectionMetrics:
        active = [t for t in tracks if t.lane == lane]
        queued = [t for t in active if t.queued]
        approach = self.geometry.approach(lane)
        scale = approach.meters_per_pixel if approach is not None else None
        length = 0.0
        if approach is not None:
            for track in queued:
                for x, y in (
                    (track.bbox.x1, track.bbox.y1),
                    (track.bbox.x1, track.bbox.y2),
                    (track.bbox.x2, track.bbox.y1),
                    (track.bbox.x2, track.bbox.y2),
                ):
                    point = Point(x=x / width, y=y / height)
                    length = max(
                        length,
                        approach.stop_line.distance_pixels(
                            point, self.geometry.reference_width, self.geometry.reference_height
                        ),
                    )
        window = min(now - self._first_timestamp, self.settings.arrival_window_seconds)
        rate = len(self.arrivals[lane]) * 60 / window if window > 0 else 0
        core = self._core_approach(lane, queued, rate)
        return DirectionMetrics(
            car_count=sum(t.kind == ObjectKind.CAR for t in active),
            bus_count=sum(t.kind == ObjectKind.BUS for t in active),
            truck_count=sum(t.kind == ObjectKind.TRUCK for t in active),
            motorcycle_count=sum(t.kind == ObjectKind.MOTORCYCLE for t in active),
            bicycle_count=sum(t.kind == ObjectKind.BICYCLE for t in active),
            pedestrian_count=sum(t.kind == ObjectKind.PEDESTRIAN for t in active),
            queue_count=len(queued),
            queue_vehicle_count=len(queued),
            estimated_queue_length=length * scale if scale is not None else length,
            queue_length_unit="m" if scale is not None else "px",
            queue_confidence=sum(t.queue_confidence for t in queued) / len(queued) if queued else 0,
            average_wait_seconds=core.mean_wait_seconds,
            max_wait_seconds=core.oldest_wait_seconds,
            arrival_rate=rate,
            unique_arrivals=self.totals[lane],
            traffic_score=core.traffic_score,
            congestion=core.congestion.value,
        )

    def _core_approach(self, lane: Lane, tracks: list[TrackSnapshot], rate: float) -> ApproachState:
        return build_approach_state(
            Direction(lane.value),
            [(t.kind, t.current_wait_time) for t in tracks],
            rate,
            waiting_weight=self.settings.waiting_score_per_second,
            moderate_queue=self.settings.moderate_queue,
            high_queue=self.settings.high_queue,
        )

    def to_core_state(self, snapshot: AnalysisSnapshot) -> TrafficState:
        return TrafficState(
            simulation_time=snapshot.traffic.timestamp,
            approaches=tuple(
                self._core_approach(
                    Lane(d.value),
                    [t for t in snapshot.tracks if t.queued and t.lane.value == d.value],
                    snapshot.traffic.directions[d.value].arrival_rate,
                )
                for d in Direction
            ),
            pedestrians_waiting=sum(
                m.pedestrian_count for m in snapshot.traffic.directions.values()
            ),
            pedestrian_oldest_wait_seconds=0,
        )

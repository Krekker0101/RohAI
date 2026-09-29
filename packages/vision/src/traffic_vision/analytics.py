import math
from collections import deque
from datetime import datetime

from traffic_core.models import (
    ApproachState,
    Congestion,
    Direction,
    ObjectKind,
    ObservationEvidence,
    ObservationStatus,
    TrafficState,
    Uncertainty,
)
from traffic_core.state import build_approach_state
from traffic_core.traffic_events import TrafficEventType

from traffic_vision.alignment import CameraAlignmentMonitor
from traffic_vision.estimator import Track, TrackStateEstimator
from traffic_vision.geometry import IntersectionGeometry
from traffic_vision.models import (
    AnalysisSnapshot,
    Detection,
    DirectionMetrics,
    Lane,
    PedestrianState,
    Point,
    TrackSnapshot,
    TrackVisibility,
    TrajectoryPoint,
    VisionTrafficState,
)
from traffic_vision.settings import AnalyticsSettings
from traffic_vision.state_estimation import TrafficStateEstimator


class TrafficAnalyzer:
    def __init__(
        self,
        geometry: IntersectionGeometry,
        settings: AnalyticsSettings,
        alignment: CameraAlignmentMonitor | None = None,
    ) -> None:
        self.geometry = geometry
        self.road_estimator = TrafficStateEstimator(
            geometry.road, geometry.camera_calibration, alignment
        )
        self.settings = settings
        self.tracks: dict[int, Track] = {}
        self.estimator = TrackStateEstimator(settings)
        self.evidence: dict[str, ObservationEvidence] = {}
        self.pedestrian_arrivals: dict[Lane, deque[float]] = {
            Lane(d.value): deque() for d in Direction
        }
        self._uncertain_until: dict[Lane, float] = {}
        self.counted: set[int] = set()
        self.arrivals: dict[Lane, deque[float]] = {Lane(d.value): deque() for d in Direction}
        self.totals: dict[Lane, int] = dict.fromkeys(self.arrivals, 0)
        self.vehicle_arrivals = 0
        self.pedestrian_total = 0
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
        health: dict[str, ObservationEvidence] | None = None,
        calibration_time: datetime | None = None,
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
        self.evidence = health or {}
        self._frame_detections = detections
        self._last_timestamp = timestamp
        self._last_frame_id = frame_id
        # A tracker should not emit duplicate IDs, but duplicate proposals must never double-count.
        observed: dict[int, Detection] = {}
        for detection in detections:
            if detection.timestamp != timestamp:
                raise ValueError("Detection timestamp does not match its frame")
            if detection.track_id is not None:
                previous = observed.get(detection.track_id)
                if previous is None or previous.confidence < detection.confidence:
                    observed[detection.track_id] = detection
        observed = self.estimator.resolve(observed, self.tracks, timestamp)
        for identity, track in list(self.tracks.items()):
            if identity not in observed:
                self.estimator.missing(track, timestamp)
                if track.state == TrackVisibility.EXPIRED:
                    if track.lane != Lane.UNKNOWN:
                        self._uncertain_until[track.lane] = (
                            track.last_seen
                            + self.settings.lost_track_seconds
                            + self.settings.lost_uncertainty_seconds
                        )
                    del self.tracks[identity]
        for identity, detection in observed.items():
            self._observe(identity, detection, timestamp, width, height)
        estimation = self.road_estimator.process(
            detections,
            now=calibration_time,
            source=source_id,
            observation_reasons=tuple(
                sorted(
                    {
                        reason
                        for e in self.evidence.values()
                        if not e.reliable
                        for reason in (e.failure_reasons or ("observation_unreliable",))
                    }
                )
            )
            if self.evidence
            else ("observation_missing",),
            session=f"{source_id}:{stream_epoch}",
            timestamp=timestamp,
            width=width,
            height=height,
        )
        for event in estimation.events:
            lane = Lane(event.approach)
            if event.kind == TrafficEventType.VEHICLE_ENTERED_APPROACH:
                self.arrivals[lane].append(event.timestamp)
                self.totals[lane] += 1
                self.vehicle_arrivals += 1
            elif event.kind == TrafficEventType.PEDESTRIAN_ARRIVED:
                self.pedestrian_arrivals[lane].append(event.timestamp)
                self.pedestrian_total += 1
        self._estimation = estimation
        for history in (*self.arrivals.values(), *self.pedestrian_arrivals.values()):
            while history and history[0] <= timestamp - self.settings.arrival_window_seconds:
                history.popleft()
        tracks = tuple(self._snapshot(t, source_id, stream_epoch) for t in self.tracks.values())
        directions = {
            d.value: self._metrics(Lane(d.value), tracks, timestamp, width, height)
            for d in Direction
        }
        self._latest = AnalysisSnapshot(
            traffic=VisionTrafficState(
                estimation=estimation,
                timestamp=timestamp,
                source_id=source_id,
                stream_epoch=stream_epoch,
                frame_id=frame_id,
                directions=directions,
                unknown_count=sum(t.lane == Lane.UNKNOWN for t in tracks),
                unique_vehicle_arrivals=self.vehicle_arrivals,
                unique_pedestrian_arrivals=self.pedestrian_total,
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
        self.estimator.aliases.clear()
        self._uncertain_until.clear()
        for history in (*self.arrivals.values(), *self.pedestrian_arrivals.values()):
            history.clear()
        self.totals = dict.fromkeys(self.arrivals, 0)
        self.vehicle_arrivals = self.pedestrian_total = 0

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
        continuous = bool(track.trajectory) and 0 < gap <= self.settings.max_motion_gap_seconds
        if continuous:
            reference = next(
                (
                    p
                    for p in reversed(track.trajectory)
                    if now - p.timestamp >= self.settings.motion_window_seconds
                ),
                track.trajectory[0],
            )
            distance = math.hypot(
                (point.x - reference.point.x) * self.geometry.reference_width,
                (point.y - reference.point.y) * self.geometry.reference_height,
            )
            speed = (
                distance / (now - reference.timestamp)
                if distance > self.settings.jitter_tolerance_px
                else 0.0
            )
            alpha = 1 - (1 - self.settings.speed_smoothing) ** (gap / 0.1)
            track.speed = (
                speed if track.speed is None else alpha * speed + (1 - alpha) * track.speed
            )
        else:
            track.speed = None
            if gap > self.settings.occlusion_hold_seconds:
                track.reset_wait()
        track.lane = self.geometry.assign(
            point, track.lane, detection.kind == ObjectKind.PEDESTRIAN
        )
        track.detection = detection
        track.last_seen = now
        track.visible = True
        track.state = TrackVisibility.CONFIRMED_VISIBLE
        track.tracking_confidence = 1.0 if continuous else 0.5
        track.motion_confidence = 1.0 if track.speed is not None else 0.0
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
                track.candidate_since = now - gap
            if now - track.candidate_since + 1e-9 >= self.settings.stop_confirmation_seconds:
                previous_wait = track.current_wait
                track.waiting_started_at = track.candidate_since
                track.current_wait = now - track.candidate_since
                track.total_wait += track.current_wait - previous_wait
                track.queued = True
        elif track.speed is not None or gap > self.settings.occlusion_hold_seconds:
            track.reset_wait()
        if detection.kind == ObjectKind.PEDESTRIAN:
            state = PedestrianState.UNKNOWN
            if approach is not None:
                matches = [
                    state
                    for zone, state in (
                        (approach.waiting_zone, PedestrianState.WAITING),
                        (
                            approach.crosswalk_zone or approach.pedestrian_crossing,
                            PedestrianState.CROSSING,
                        ),
                        (approach.exit_zone, PedestrianState.CLEARING),
                    )
                    if zone is not None and zone.contains(point)
                ]
                if len(matches) == 1:
                    state = matches[0]
            if state != track.pedestrian_state:
                track.pedestrian_since = now
            track.pedestrian_state = state

    def _snapshot(self, track: Track, source_id: str, epoch: int) -> TrackSnapshot:
        raw = track.detection.track_id
        uid = self.road_estimator.identities.get(raw) if raw is not None else None
        road_state = next((s for s in self._estimation.tracks if s.track_uid == uid), None)
        return TrackSnapshot(
            road_state=road_state,
            visible=track.visible,
            track_state=track.state,
            pedestrian_state=PedestrianState(road_state.pedestrian_state)
            if road_state
            else track.pedestrian_state,
            uncertainty=Uncertainty(
                detection_confidence=track.detection.confidence,
                tracking_confidence=track.tracking_confidence,
                lane_assignment_confidence=1 if track.lane != Lane.UNKNOWN else 0,
                motion_confidence=track.motion_confidence,
                queue_confidence=min(track.tracking_confidence, track.motion_confidence)
                if track.queued
                else 0,
                observation_confidence=1 if track.visible else 0,
            ),
            track_id=track.identity,
            track_uid=f"{source_id}:{epoch}:{track.identity}",
            kind=track.detection.kind,
            confidence=track.detection.confidence,
            bbox=track.detection.bbox,
            lane=Lane(road_state.current_approach)
            if road_state and road_state.current_approach
            else track.lane,
            first_seen=track.first_seen,
            last_seen=track.last_seen,
            trajectory=tuple(track.trajectory),
            speed_px_per_second=track.speed,
            waiting_started_at=track.waiting_started_at,
            current_wait_time=road_state.wait_seconds
            if road_state
            else (
                max(0, track.last_seen - track.pedestrian_since)
                if track.pedestrian_state == PedestrianState.WAITING
                and track.pedestrian_since is not None
                else road_state.wait_seconds
                if road_state is not None
                else track.current_wait
            ),
            total_observed_wait_seconds=track.total_wait,
            consecutive_slow_frames=track.slow_frames,
            queued=road_state.queued if road_state is not None else track.queued,
            queue_confidence=min(track.tracking_confidence, track.motion_confidence)
            if track.queued
            else 0,
        )

    def _metrics(
        self, lane: Lane, tracks: tuple[TrackSnapshot, ...], now: float, width: int, height: int
    ) -> DirectionMetrics:
        active = [t for t in tracks if t.lane == lane]
        queued = [t for t in active if t.queued]
        approach = self.geometry.approach(lane)
        length = 0.0
        if approach is not None:
            for track in queued:
                point = Point(
                    x=(track.bbox.x1 + track.bbox.x2) / 2 / width, y=track.bbox.y2 / height
                )
                length = max(
                    length,
                    approach.stop_line.distance_pixels(
                        point, self.geometry.reference_width, self.geometry.reference_height
                    ),
                )
        window = min(now - self._first_timestamp, self.settings.arrival_window_seconds)
        rate = len(self.arrivals[lane]) * 60 / window if window > 0 else 0
        core = self._core_approach(lane, queued, rate)
        evidence = self.evidence.get(lane.value, ObservationEvidence())
        reasons = list(evidence.failure_reasons)
        if evidence.source_camera_ids != ("simulator",):
            reasons.extend(self._estimation.reasons)
            if lane.value not in self._estimation.metrics:
                reasons.append("road_approach_not_covered")
        if self.estimator.ambiguous:
            reasons.append("identity_ambiguous")
        if approach is None:
            reasons.append("direction_not_covered")
        if any(t.track_state != TrackVisibility.CONFIRMED_VISIBLE for t in active):
            reasons.append("occluded_or_lost_track")
        if self._uncertain_until.get(lane, -1) > now:
            reasons.append("expired_track_uncertainty")
        if any(
            t.kind != ObjectKind.PEDESTRIAN
            and (
                t.road_state.estimated_speed_mps.value is None
                if t.road_state is not None
                else (
                    t.speed_px_per_second is None
                    or (
                        not t.queued
                        and t.speed_px_per_second <= self.settings.slow_speed_px_per_second
                    )
                )
            )
            for t in active
        ):
            reasons.append("motion_unconfirmed")
        if any(
            d.confidence < self.settings.min_detection_confidence
            or d.track_id is None
            or d.kind == ObjectKind.UNKNOWN
            for d in self._frame_detections
        ):
            reasons.append("unreliable_detection")
        if any(
            t.kind == ObjectKind.PEDESTRIAN and t.pedestrian_state == PedestrianState.UNKNOWN
            for t in active
        ):
            reasons.append("pedestrian_state_unknown")
        if evidence.source_camera_ids != ("simulator",) and (
            evidence.measurement_timestamp != now
            or evidence.source_camera_ids != (self.geometry.source_id,)
        ):
            reasons.append("evidence_frame_mismatch")
        if any(t.lane == Lane.UNKNOWN for t in tracks):
            reasons.append("unassigned_object")
        status = evidence.observation_status
        if approach is None:
            status = ObservationStatus.UNOBSERVED
        elif status == ObservationStatus.OBSERVED and reasons:
            status = (
                ObservationStatus.PARTIALLY_OBSERVED
                if "occluded_or_lost_track" in reasons
                else ObservationStatus.DEGRADED
            )
        tq = min((t.uncertainty.tracking_confidence for t in active), default=1.0)
        mc = min(
            (t.uncertainty.motion_confidence for t in active if t.kind != ObjectKind.PEDESTRIAN),
            default=1.0,
        )
        dc = min((t.confidence for t in active), default=1.0)
        confidence = min(evidence.measurement_confidence, tq, mc, dc)
        uncertainty = Uncertainty(
            detection_confidence=dc,
            tracking_confidence=tq,
            lane_assignment_confidence=1 if approach is not None else 0,
            motion_confidence=mc,
            queue_confidence=min(tq, mc),
            observation_confidence=confidence if not reasons else 0,
        )
        evidence = ObservationEvidence.model_validate(
            {
                **evidence.model_dump(),
                "observation_status": status,
                "calibration_status": evidence.calibration_status
                if evidence.source_camera_ids == ("simulator",)
                else self._estimation.calibration_status.lower(),
                "failure_reasons": tuple(dict.fromkeys(reasons)),
                "tracking_quality": tq,
                "measurement_confidence": confidence if not reasons else 0,
                "uncertainty": uncertainty,
            }
        )
        pedestrians = [t for t in active if t.kind == ObjectKind.PEDESTRIAN]
        waiting = [t for t in pedestrians if t.pedestrian_state == PedestrianState.WAITING]
        waits = [t.current_wait_time for t in waiting]
        ped_rate = len(self.pedestrian_arrivals[lane]) * 60 / window if window > 0 else 0
        result = DirectionMetrics(
            observation=evidence,
            estimated_queue_count=len(queued) if queued or evidence.reliable else None,
            pedestrian_count_waiting=len(waiting),
            pedestrian_oldest_wait_seconds=max(waits, default=0),
            pedestrian_mean_wait_seconds=sum(waits) / len(waits) if waits else 0,
            pedestrian_arrival_rate=ped_rate,
            pedestrian_crossing_count=sum(
                t.pedestrian_state == PedestrianState.CROSSING for t in pedestrians
            ),
            pedestrian_clearing_count=sum(
                t.pedestrian_state == PedestrianState.CLEARING for t in pedestrians
            ),
            pedestrian_unknown_count=sum(
                t.pedestrian_state == PedestrianState.UNKNOWN for t in pedestrians
            ),
            car_count=sum(t.kind == ObjectKind.CAR for t in active),
            bus_count=sum(t.kind == ObjectKind.BUS for t in active),
            truck_count=sum(t.kind == ObjectKind.TRUCK for t in active),
            motorcycle_count=sum(t.kind == ObjectKind.MOTORCYCLE for t in active),
            bicycle_count=sum(t.kind == ObjectKind.BICYCLE for t in active),
            pedestrian_count=sum(t.kind == ObjectKind.PEDESTRIAN for t in active),
            queue_count=len(queued),
            queue_vehicle_count=len(queued),
            estimated_queue_length=length,
            queue_length_unit="px",
            queue_confidence=sum(t.queue_confidence for t in queued) / len(queued) if queued else 0,
            average_wait_seconds=core.mean_wait_seconds,
            max_wait_seconds=core.oldest_wait_seconds,
            arrival_rate=rate,
            unique_arrivals=self.totals[lane],
            traffic_score=core.traffic_score,
            congestion=core.congestion.value if core.congestion is not None else None,
        )

        physical = self._estimation.metrics.get(lane.value)
        if physical is not None and not self._estimation.reasons and evidence.reliable:
            values = {
                **result.model_dump(),
                "queue_count": physical["queue_vehicle_count"].value,
                "queue_vehicle_count": physical["queue_vehicle_count"].value,
                "estimated_queue_length": physical["queue_length_m"].value,
                "queue_length_unit": "m",
                "average_wait_seconds": physical["mean_wait"].value,
                "max_wait_seconds": physical["max_wait"].value,
                "arrival_rate": physical["arrival_rate"].value,
            }
            result = DirectionMetrics.model_validate(values)
        return result

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
        approaches = []
        for direction in Direction:
            metrics = snapshot.traffic.directions[direction.value]
            core = self._core_approach(
                Lane(direction.value),
                [t for t in snapshot.tracks if t.queued and t.lane.value == direction.value],
                metrics.arrival_rate if metrics.arrival_rate is not None else 0,
            )
            approaches.append(
                ApproachState.model_validate(
                    {
                        **core.model_dump(),
                        "observation": metrics.observation,
                        "queue_length": metrics.queue_count,
                        "traffic_score": metrics.traffic_score,
                        "mean_wait_seconds": metrics.average_wait_seconds,
                        "oldest_wait_seconds": metrics.max_wait_seconds,
                        "arrival_rate_per_minute": metrics.arrival_rate,
                        "congestion": Congestion(metrics.congestion)
                        if metrics.congestion is not None
                        else None,
                        "estimated_queue_count": metrics.estimated_queue_count,
                        **{
                            name: getattr(metrics, name)
                            for name in (
                                "pedestrian_count_waiting",
                                "pedestrian_oldest_wait_seconds",
                                "pedestrian_mean_wait_seconds",
                                "pedestrian_arrival_rate",
                                "pedestrian_crossing_count",
                            )
                        },
                    }
                )
            )
        reliable = all(
            a.observation.reliable
            and a.pedestrian_count_waiting is not None
            and a.pedestrian_oldest_wait_seconds is not None
            for a in approaches
        )
        return TrafficState(
            simulation_time=snapshot.traffic.timestamp,
            approaches=tuple(approaches),
            pedestrians_waiting=sum(a.pedestrian_count_waiting or 0 for a in approaches)
            if reliable
            else None,
            pedestrian_oldest_wait_seconds=max(
                (a.pedestrian_oldest_wait_seconds or 0 for a in approaches), default=0
            )
            if reliable
            else None,
        )

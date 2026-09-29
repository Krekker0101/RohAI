"""Metric traffic state from contact points, surveyed lanes and fresh alignment evidence."""

import math
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime

from traffic_core.models import ObjectKind
from traffic_core.traffic_events import (
    IdentityDiagnostics,
    MetricEstimate,
    RoadTrackState,
    TrafficEstimation,
    TrafficEvent,
)
from traffic_core.traffic_events import (
    TrafficEventType as Event,
)

from traffic_vision.alignment import CameraAlignmentMonitor, LandmarkAlignmentMonitor
from traffic_vision.calibration import (
    CalibrationStatus,
    CalibrationValidator,
    CameraCalibration,
    RoadPlaneTransform,
)
from traffic_vision.models import Detection, Point
from traffic_vision.road_geometry import RoadIntersection, RoadLane


@dataclass
class _Memory:
    uid: str
    raw_id: int
    kind: ObjectKind
    point: Point
    time: float
    lane: RoadLane | None = None
    stopped_since: float | None = None
    stopped: bool = False
    queued: bool = False
    missing: bool = False
    crossed: set[Event] = field(default_factory=set)
    anchors: dict[Event, tuple[Point, float]] = field(default_factory=dict)
    pedestrian_zone: tuple[str, str] | None = None
    pedestrian_since: float = 0
    crossing_times: dict[Event, float] = field(default_factory=dict)
    state: RoadTrackState | None = None


UNITS = {
    "queue_vehicle_count": "vehicle",
    "queue_length_m": "m",
    "mean_wait": "s",
    "max_wait": "s",
    "arrival_rate": "vehicle/min",
    "departure_rate": "vehicle/min",
    "throughput": "vehicle",
    "occupancy": "fraction",
    "estimated_speed_mps": "m/s",
}


class TrafficStateEstimator:
    def __init__(
        self,
        road: RoadIntersection | None,
        calibration: CameraCalibration | None,
        alignment: CameraAlignmentMonitor | None = None,
        *,
        hold_seconds: float = 1.0,
        recovery_distance_m: float = 1.0,
        stop_speed_mps: float = 0.3,
        stop_seconds: float = 0.5,
        crossing_hysteresis_m: float = 0.1,
        window_seconds: float = 60,
        max_tracks: int = 10000,
    ) -> None:
        if (
            min(
                hold_seconds,
                recovery_distance_m,
                stop_speed_mps,
                stop_seconds,
                crossing_hysteresis_m,
                window_seconds,
                max_tracks,
            )
            <= 0
        ):
            raise ValueError("Estimation bounds must be positive")
        self.road, self.calibration = road, calibration
        self.alignment = alignment or LandmarkAlignmentMonitor()
        self.hold, self.recovery, self.stop_speed, self.stop_seconds = (
            hold_seconds,
            recovery_distance_m,
            stop_speed_mps,
            stop_seconds,
        )
        self.hysteresis, self.window, self.max_tracks = (
            crossing_hysteresis_m,
            window_seconds,
            max_tracks,
        )
        self.memory: dict[str, _Memory] = {}
        self.identities: dict[int, str] = {}
        self.history: deque[TrafficEvent] = deque()
        self.counters = IdentityDiagnostics()
        self.session = ""
        self.started = 0.0
        self.last = -1.0
        self.sequence = 0
        self.event_sequence = 0
        self.departures: dict[str, int] = {}
        self._was_valid = False
        self._calibration_latched = False
        self._uncertain_until = -1.0

    def _increment(self, name: str) -> None:
        self.counters = self.counters.model_copy(update={name: getattr(self.counters, name) + 1})

    def process(
        self,
        detections: tuple[Detection, ...],
        *,
        source: str,
        session: str,
        timestamp: float,
        width: int,
        height: int,
        now: datetime | None = None,
        observation_reasons: tuple[str, ...] = (),
    ) -> TrafficEstimation:
        if not math.isfinite(timestamp) or timestamp < 0 or width <= 0 or height <= 0:
            raise ValueError("Invalid frame metadata")
        if session != self.session:
            self.memory.clear()
            self.identities.clear()
            self.history.clear()
            self.departures.clear()
            self.counters = IdentityDiagnostics()
            self.session, self.started, self.last = session, timestamp, -1
            self._was_valid = False
            self._uncertain_until = -1.0
        if timestamp <= self.last:
            raise ValueError("Estimation timestamps must advance")
        self.last = timestamp
        status, reasons = CalibrationStatus.MISSING, ["road_geometry_or_calibration_missing"]
        c, road = self.calibration, self.road
        if c is not None and road is not None:
            validation = CalibrationValidator.validate(
                c, camera_id=source, width=width, height=height, now=now
            )
            status, reasons = validation.status, list(validation.reasons)
            if c.metadata.world_frame != road.world_frame:
                status, reasons = CalibrationStatus.INVALID, ["world_frame_mismatch"]
            if status == CalibrationStatus.VALID:
                aligned = self.alignment.check(c, timestamp, width, height)
                status = aligned.status
                reasons = [] if status == CalibrationStatus.VALID else [aligned.reason]
        if status == CalibrationStatus.SUSPECT:
            self._calibration_latched = True
        if self._calibration_latched:
            status = CalibrationStatus.SUSPECT
            reasons.append("calibration_reverification_required")
        if status != CalibrationStatus.VALID:
            self.memory.clear()
            self.identities.clear()
            self.history.clear()
            self._was_valid = False
            return self._result(timestamp, status, reasons, (), (), set())
        if observation_reasons:
            self._uncertain_until = timestamp + 5
            return self._result(timestamp, status, list(observation_reasons), (), (), set())
        assert c is not None and road is not None
        if not self._was_valid:
            self.started = timestamp
            self.departures.clear()
        self._was_valid = True
        transform = RoadPlaneTransform(c)
        points: dict[int, tuple[Detection, Point]] = {}
        for d in detections:
            if d.timestamp != timestamp:
                raise ValueError("Detection/frame timestamp mismatch")
            if d.track_id is None or d.kind == ObjectKind.UNKNOWN or d.confidence < 0.25:
                reasons.append("unreliable_detection")
                continue
            if d.track_id in points:
                reasons.append("duplicate_track_id")
                continue
            # A truncated box cannot establish a visible ground contact without explicit evidence.
            if d.ground_contact is None and (
                d.bbox.x1 <= 0 or d.bbox.y1 <= 0 or d.bbox.x2 >= width or d.bbox.y2 >= height
            ):
                reasons.append("partial_bbox_contact_unknown")
                continue
            try:
                point = transform.project(d.footpoint, width, height)
            except ValueError:
                reasons.append("contact_projection_invalid")
                continue
            points[d.track_id] = (d, point)
        present = set(points)
        for uid, m in list(self.memory.items()):
            if m.raw_id not in present and not m.missing:
                m.missing = True
                self._increment("lost_tracks")
            if timestamp - m.time > self.hold:
                self.identities.pop(m.raw_id, None)
                del self.memory[uid]
                self._uncertain_until = timestamp + 5
        # Mutual uniqueness is required. No nearest-neighbour winner for ambiguous candidates.
        candidates: dict[int, list[str]] = {}
        for raw, (d, p) in points.items():
            if raw not in self.identities:
                candidates[raw] = [
                    uid
                    for uid, m in self.memory.items()
                    if m.raw_id not in present
                    and m.kind == d.kind
                    and timestamp - m.time <= self.hold
                    and math.hypot(p.x - m.point.x, p.y - m.point.y) <= self.recovery
                ]
        events: list[TrafficEvent] = []
        states: list[RoadTrackState] = []
        observed_uids: set[str] = set()
        for raw, (d, p) in points.items():
            resolved_uid = self.identities.get(raw)
            uid = resolved_uid or ""
            possible = candidates.get(raw, [])
            if (
                not uid
                and len(possible) == 1
                and sum(possible[0] in x for x in candidates.values()) == 1
            ):
                uid = possible[0]
                m = self.memory[uid]
                self.identities.pop(m.raw_id, None)
                m.raw_id = raw
                self.identities[raw] = uid
                self._increment("reconciled_id_changes")
            elif not uid and possible:
                reasons.append("identity_reconciliation_ambiguous")
            previous = self.memory.get(uid) if uid is not None else None
            if previous is None:
                if len(self.memory) >= self.max_tracks:
                    reasons.append("track_capacity_exceeded")
                    continue
                self.sequence += 1
                uid = f"{session}:{self.sequence}"
                m = _Memory(uid, raw, d.kind, p, timestamp)
                self.memory[uid] = m
                self.identities[raw] = uid
            else:
                m = previous
                if m.kind != d.kind:
                    reasons.append("track_class_changed")
                    continue
                if m.missing:
                    self._increment("recovered_tracks")
                    m.missing = False
            observed_uids.add(m.uid)
            lane = road.assign(p, m.lane.lane_id if m.lane else None)
            speed = (
                None
                if previous is None
                else math.hypot(p.x - m.point.x, p.y - m.point.y) / (timestamp - m.time)
            )
            ped_approach: str | None = None
            ped_state = "pedestrian_unknown"
            if d.kind == ObjectKind.PEDESTRIAN:
                lane = None
                zones = [
                    (cw, zone)
                    for cw in road.crosswalks
                    for zone, polygon in (
                        ("waiting", cw.waiting_zone),
                        ("crossing", cw.crossing_zone),
                        ("exit", cw.exit_zone),
                    )
                    if polygon.contains(p)
                ]
                zone = (zones[0][0].crosswalk_id, zones[0][1]) if len(zones) == 1 else None
                if previous is not None and zone is not None and zone != m.pedestrian_zone:
                    kind = (
                        Event.PEDESTRIAN_ARRIVED
                        if zone[1] == "waiting" and m.pedestrian_zone is None
                        else None
                    )
                    if m.pedestrian_zone == (zone[0], "waiting") and zone[1] == "crossing":
                        kind = Event.PEDESTRIAN_STARTED_CROSSING
                    if m.pedestrian_zone == (zone[0], "crossing") and zone[1] == "exit":
                        kind = Event.PEDESTRIAN_CLEARED_CROSSING
                    if kind is not None:
                        events.append(
                            self._event(
                                kind, m, d, timestamp, zones[0][0].approach.value, None, source
                            )
                        )
                if zone != m.pedestrian_zone:
                    m.pedestrian_since = timestamp
                if zone is not None:
                    ped_approach = zones[0][0].approach.value
                    ped_state = {
                        "waiting": "pedestrian_waiting",
                        "crossing": "pedestrian_crossing",
                        "exit": "pedestrian_clearing",
                    }[zone[1]]
                else:
                    reasons.append("pedestrian_state_unknown")
                m.pedestrian_zone = zone
            elif lane is None:
                reasons.append("lane_assignment_unknown")
            else:
                if previous is not None and m.lane is not None and m.lane.lane_id != lane.lane_id:
                    m.anchors.clear()
                    m.crossing_times.clear()
                for kind, line in (
                    (Event.VEHICLE_ENTERED_APPROACH, lane.entry_line),
                    (Event.VEHICLE_CROSSED_STOP_LINE, lane.stop_line),
                    (Event.VEHICLE_EXITED_INTERSECTION, lane.exit_line),
                ):
                    side = line.side(p)
                    if previous is not None:
                        crossing = line.crossing(m.point, p)
                        if crossing is not None:
                            m.crossing_times[kind] = m.time + crossing * (timestamp - m.time)
                    if side > self.hysteresis:
                        m.anchors[kind] = (p, timestamp)
                        m.crossing_times.pop(kind, None)
                    elif side < -self.hysteresis and kind in m.anchors:
                        anchor, at = m.anchors.pop(kind)
                        fraction = line.crossing(anchor, p)
                        if fraction is not None and timestamp - at <= self.hold:
                            if kind not in m.crossed:
                                m.crossed.add(kind)
                                events.append(
                                    self._event(
                                        kind,
                                        m,
                                        d,
                                        m.crossing_times.pop(
                                            kind, at + fraction * (timestamp - at)
                                        ),
                                        lane.approach.value,
                                        lane.lane_id,
                                        source,
                                    )
                                )
                            elif kind == Event.VEHICLE_ENTERED_APPROACH:
                                self._increment("duplicate_arrivals")
                slow = speed is not None and speed <= self.stop_speed
                if slow and m.stopped_since is None:
                    m.stopped_since = m.time
                elif not slow:
                    m.stopped_since = None
                stopped = (
                    m.stopped_since is not None and timestamp - m.stopped_since >= self.stop_seconds
                )
                queued = stopped and lane.queue_zone.contains(p) and lane.stop_line.side(p) >= 0
                for condition, kind in (
                    (stopped and not m.stopped, Event.VEHICLE_STOPPED),
                    (queued and not m.queued, Event.VEHICLE_JOINED_QUEUE),
                    (m.stopped and not stopped and speed is not None, Event.VEHICLE_STARTED_MOVING),
                ):
                    if condition:
                        events.append(
                            self._event(
                                kind, m, d, timestamp, lane.approach.value, lane.lane_id, source
                            )
                        )
                m.stopped, m.queued = stopped, queued
            state = RoadTrackState(
                track_uid=m.uid,
                pedestrian_state=ped_state,
                current_approach=lane.approach.value if lane else ped_approach,
                current_lane=lane.lane_id if lane else None,
                movement_direction=lane.movement_direction if lane else None,
                distance_to_stop_line=MetricEstimate(
                    value=lane.distance_to_stop(p) if lane else None,
                    unit="m",
                    unavailable_reason=None if lane else "lane_unknown",
                ),
                estimated_speed_mps=MetricEstimate(
                    value=speed,
                    unit="m/s",
                    unavailable_reason="insufficient_motion_history" if speed is None else None,
                ),
                crossed_entry_line=Event.VEHICLE_ENTERED_APPROACH in m.crossed,
                crossed_stop_line=Event.VEHICLE_CROSSED_STOP_LINE in m.crossed,
                crossed_exit_line=Event.VEHICLE_EXITED_INTERSECTION in m.crossed,
                queued=m.queued,
                wait_seconds=timestamp - m.pedestrian_since
                if ped_state == "pedestrian_waiting"
                else timestamp - m.stopped_since
                if m.queued and m.stopped_since is not None
                else 0,
            )
            m.point, m.time, m.lane, m.state = p, timestamp, lane, state
            states.append(state)
        if timestamp < self._uncertain_until:
            reasons.append("expired_track_uncertainty")
        if any(m.missing for m in self.memory.values()):
            reasons.append("occluded_or_lost_track")
        if any(s.estimated_speed_mps.value is None for s in states):
            reasons.append("motion_unconfirmed")
        for event in events:
            self.history.append(event)
            if event.kind == Event.VEHICLE_EXITED_INTERSECTION:
                self.departures[event.approach] = self.departures.get(event.approach, 0) + 1
        # Hysteresis can publish backdated crossings; arrival order is not timestamp order.
        self.history = deque(e for e in self.history if e.timestamp > timestamp - self.window)
        events.sort(key=lambda e: (e.timestamp, e.event_id))
        # Bound history even under malformed high-rate sources; reject rather than undercount.
        if len(self.history) > self.max_tracks * 16:
            self.history.clear()
            reasons.append("event_capacity_exceeded")
        return self._result(timestamp, status, reasons, tuple(states), tuple(events), observed_uids)

    def _event(
        self,
        kind: Event,
        m: _Memory,
        d: Detection,
        timestamp: float,
        approach: str,
        lane: str | None,
        source: str,
    ) -> TrafficEvent:
        assert self.road is not None and self.calibration is not None
        self.event_sequence += 1
        return TrafficEvent(
            event_id=f"{self.session}:event:{self.event_sequence}",
            kind=kind,
            timestamp=timestamp,
            track_uid=m.uid,
            session_id=self.session,
            source=source,
            confidence=d.confidence,
            geometry_version=self.road.version,
            calibration_version=self.calibration.metadata.version,
            approach=approach,
            lane_id=lane,
        )

    def _result(
        self,
        timestamp: float,
        status: CalibrationStatus,
        reasons: list[str],
        states: tuple[RoadTrackState, ...],
        events: tuple[TrafficEvent, ...],
        observed: set[str],
    ) -> TrafficEstimation:
        approaches = {lane.approach.value for lane in self.road.lanes} if self.road else set()
        metrics: dict[str, dict[str, MetricEstimate]] = {}
        for approach in approaches:
            active = [
                s
                for s in states
                if s.current_approach == approach
                and self.memory[s.track_uid].kind != ObjectKind.PEDESTRIAN
            ]
            queued = [s for s in active if s.queued]
            waits = [s.wait_seconds for s in queued]
            speeds = [
                s.estimated_speed_mps.value
                for s in active
                if s.estimated_speed_mps.value is not None
            ]
            duration = min(self.window, timestamp - self.started)
            arrivals = sum(
                e.approach == approach and e.kind == Event.VEHICLE_ENTERED_APPROACH
                for e in self.history
            )
            departures = sum(
                e.approach == approach and e.kind == Event.VEHICLE_CROSSED_STOP_LINE
                for e in self.history
            )
            lanes = (
                [lane for lane in self.road.lanes if lane.approach.value == approach]
                if self.road
                else []
            )
            occupied = {s.current_lane for s in active}
            values = {
                "queue_vehicle_count": float(len(queued)),
                "queue_length_m": max(
                    (max(0, s.distance_to_stop_line.value or 0) for s in queued), default=0
                ),
                "mean_wait": sum(waits) / len(waits) if waits else 0,
                "max_wait": max(waits, default=0),
                "arrival_rate": arrivals * 60 / duration if duration else None,
                "departure_rate": departures * 60 / duration if duration else None,
                "throughput": float(self.departures.get(approach, 0)),
                "occupancy": len(occupied) / len(lanes) if lanes else None,
                "estimated_speed_mps": sum(speeds) / len(speeds) if speeds else None,
            }
            metrics[approach] = {
                name: MetricEstimate(
                    value=value if not reasons else None,
                    unit=unit,
                    unavailable_reason=",".join(dict.fromkeys(reasons))
                    if reasons
                    else ("insufficient_observation" if value is None else None),
                )
                for name, unit in UNITS.items()
                for value in (values[name],)
            }
        return TrafficEstimation(
            timestamp=timestamp,
            calibration_status=status.value,
            reasons=tuple(dict.fromkeys(reasons)),
            tracks=states,
            events=events,
            metrics=metrics,
            identity=self.counters,
        )

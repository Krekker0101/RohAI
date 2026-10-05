"""Camera track events in relative/model coordinates. Missing tracks are not exits."""

import math
from collections import deque
from typing import Any

from traffic_core.models import ObjectKind
from traffic_vision.models import AnalysisSnapshot, Point

from smart_traffic_backend.maket.config import MaketCalibration


class MaketEvents:
    def __init__(self) -> None:
        self.previous: dict[str, Point] = {}
        self.counted: set[tuple[str, str]] = set()
        self.departures: deque[float] = deque(maxlen=10000)
        self.exit_count = 0
        self.frame = -1
        self.epoch: int | None = None
        self.approaches: dict[str, str] = {}

    def update(self, analysis: AnalysisSnapshot, calibration: MaketCalibration) -> dict[str, Any]:
        traffic = analysis.traffic
        if self.epoch != traffic.stream_epoch:
            self.previous.clear()
            self.counted.clear()
            self.approaches.clear()
            self.departures.clear()
            self.exit_count = 0
            self.frame = -1
            self.epoch = traffic.stream_epoch
        events = []
        tracks = []
        for t in analysis.tracks:
            if not t.visible or not t.trajectory:
                continue
            p = t.trajectory[-1].point
            if t.track_uid not in self.approaches and t.lane.value != "unknown":
                self.approaches[t.track_uid] = t.lane.value
            approach_id = self.approaches.get(t.track_uid, t.lane.value)
            approach = next(
                (a for a in calibration.geometry.approaches if a.direction.value == approach_id),
                None,
            )
            prior = self.previous.get(t.track_uid)
            if (
                traffic.frame_id != self.frame
                and approach
                and prior
                and t.kind not in (ObjectKind.PEDESTRIAN, ObjectKind.UNKNOWN)
            ):
                for name, line in (
                    ("entry", approach.entry_line),
                    ("stop", approach.stop_line),
                    ("exit", approach.exit_line),
                ):
                    key = (t.track_uid, name)
                    if (
                        line
                        and key not in self.counted
                        and line.is_before(prior)
                        and not line.is_before(p)
                    ):
                        self.counted.add(key)
                        events.append(
                            {
                                "event": name,
                                "track_uid": t.track_uid,
                                "approach": approach_id,
                                "timestamp": traffic.timestamp,
                            }
                        )
                        if name == "stop":
                            self.departures.append(traffic.timestamp)
                        if name == "exit":
                            self.exit_count += 1
            self.previous[t.track_uid] = p
            distance = (
                approach.stop_line.distance_pixels(
                    p, calibration.geometry.reference_width, calibration.geometry.reference_height
                )
                if approach
                else None
            )
            unit = "px"
            position = {"x": p.x, "y": p.y, "unit": "relative"}
            speed = t.speed_px_per_second
            speed_unit = "px/s"
            if calibration.scale.coordinate_unit == "cm":
                x, y = calibration.scale.model_point(p.x, p.y)
                position = {"x": x, "y": y, "unit": "cm"}
                speed = None
                speed_unit = "cm/s"
                if len(t.trajectory) > 1:
                    previous, last = t.trajectory[-2:]
                    delta = last.timestamp - previous.timestamp
                    px, py = calibration.scale.model_point(previous.point.x, previous.point.y)
                    if delta > 0:
                        speed = math.hypot(x - px, y - py) / delta
                if approach:
                    a, b = approach.stop_line.start, approach.stop_line.end
                    ax, ay = calibration.scale.model_point(a.x, a.y)
                    bx, by = calibration.scale.model_point(b.x, b.y)
                    distance = abs((bx - ax) * (y - ay) - (by - ay) * (x - ax)) / math.hypot(
                        bx - ax, by - ay
                    )
                    unit = "cm"
            lanes = (
                [name for name, polygon in approach.lanes.items() if polygon.contains(p)]
                if approach
                else []
            )
            tracks.append(
                {
                    **t.model_dump(mode="json"),
                    "position": position,
                    "distance_to_stop_line": distance,
                    "distance_unit": unit,
                    "speed_estimate": speed,
                    "speed_unit": speed_unit,
                    "lane_id": lanes[0] if len(lanes) == 1 else None,
                    "motion_state": "STOPPED" if t.queued else "MOVING_OR_UNCONFIRMED",
                }
            )
        self.frame = traffic.frame_id
        # Same bounded session capacity rule as analyzer, never silently erase identity evidence.
        if len(self.previous) > 100000:
            raise RuntimeError("Maket event identity capacity exceeded; new session required")
        while self.departures and traffic.timestamp - self.departures[0] > 60:
            self.departures.popleft()
        return {
            "events": events,
            "tracks": tracks,
            "stop_line_departures_per_minute": len(self.departures),
            "confirmed_exit_events": self.exit_count,
        }

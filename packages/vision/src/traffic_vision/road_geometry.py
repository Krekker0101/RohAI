"""Surveyed road geometry. All coordinates and distances are metres."""

import math
from typing import Literal

from pydantic import Field, model_validator
from traffic_core.models import Direction, Model

from traffic_vision.models import Point


def cross(a: Point, b: Point, p: Point) -> float:
    return (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)


class RoadPolygon(Model):
    points: tuple[Point, ...] = Field(min_length=3, max_length=64)

    @model_validator(mode="after")
    def valid(self) -> "RoadPolygon":
        from traffic_vision.geometry import Polygon

        xs, ys = [p.x for p in self.points], [p.y for p in self.points]
        w, h = max(xs) - min(xs), max(ys) - min(ys)
        if w <= 0 or h <= 0:
            raise ValueError("Degenerate road polygon")
        Polygon(
            points=tuple(Point(x=(p.x - min(xs)) / w, y=(p.y - min(ys)) / h) for p in self.points)
        )
        return self

    def contains(self, p: Point) -> bool:
        inside = False
        for a, b in zip(self.points, (*self.points[1:], self.points[0]), strict=True):
            if (
                abs(cross(a, b, p)) < 1e-8
                and min(a.x, b.x) - 1e-8 <= p.x <= max(a.x, b.x) + 1e-8
                and min(a.y, b.y) - 1e-8 <= p.y <= max(a.y, b.y) + 1e-8
            ):
                return True
            if (a.y > p.y) != (b.y > p.y) and p.x < a.x + (p.y - a.y) * (b.x - a.x) / (b.y - a.y):
                inside = not inside
        return inside


class RoadLine(Model):
    start: Point
    end: Point
    upstream: Point

    @model_validator(mode="after")
    def valid(self) -> "RoadLine":
        if abs(cross(self.start, self.end, self.upstream)) < 1e-8:
            raise ValueError("Line needs a distinct upstream side")
        return self

    def side(self, p: Point) -> float:
        sign = 1 if cross(self.start, self.end, self.upstream) > 0 else -1
        return (
            sign
            * cross(self.start, self.end, p)
            / math.hypot(self.end.x - self.start.x, self.end.y - self.start.y)
        )

    def crossing(self, previous: Point, current: Point) -> float | None:
        a, b = self.side(previous), self.side(current)
        if a <= 0 or b > 0:
            return None
        fraction = a / (a - b)
        p = Point(
            x=previous.x + fraction * (current.x - previous.x),
            y=previous.y + fraction * (current.y - previous.y),
        )
        if (
            min(self.start.x, self.end.x) - 1e-8 <= p.x <= max(self.start.x, self.end.x) + 1e-8
            and min(self.start.y, self.end.y) - 1e-8 <= p.y <= max(self.start.y, self.end.y) + 1e-8
        ):
            return fraction
        return None


class RoadLane(Model):
    lane_id: str = Field(min_length=1)
    approach: Direction
    region: RoadPolygon
    entry_zone: RoadPolygon
    queue_zone: RoadPolygon
    exit_zone: RoadPolygon
    entry_line: RoadLine
    stop_line: RoadLine
    exit_line: RoadLine
    centerline: tuple[Point, ...] = Field(min_length=2, max_length=64)
    movement_direction: Literal["straight", "left", "right", "shared"]

    def chainage(self, p: Point) -> float:
        offset, best, result = 0.0, math.inf, 0.0
        for a, b in zip(self.centerline[:-1], self.centerline[1:], strict=True):
            dx, dy = b.x - a.x, b.y - a.y
            length = math.hypot(dx, dy)
            t = min(1.0, max(0.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / (length * length)))
            distance = math.hypot(p.x - a.x - t * dx, p.y - a.y - t * dy)
            if distance < best:
                best, result = distance, offset + t * length
            offset += length
        return result

    def line_chainage(self, line: RoadLine) -> float:
        offset = 0.0
        hits: list[float] = []
        for a, b in zip(self.centerline[:-1], self.centerline[1:], strict=True):
            length = math.hypot(b.x - a.x, b.y - a.y)
            fraction = line.crossing(a, b)
            if fraction is not None:
                hits.append(offset + fraction * length)
            offset += length
        if len(hits) != 1:
            raise ValueError("Centerline must cross each oriented line exactly once")
        return hits[0]

    @model_validator(mode="after")
    def valid(self) -> "RoadLane":
        if any(a == b for a, b in zip(self.centerline[:-1], self.centerline[1:], strict=True)):
            raise ValueError("Repeated centerline point")
        if any(
            not self.region.contains(p)
            for zone in (self.entry_zone, self.queue_zone, self.exit_zone)
            for p in zone.points
        ):
            raise ValueError("Lane task zones must lie inside its region")
        entry, stop, exit_ = (
            self.line_chainage(line) for line in (self.entry_line, self.stop_line, self.exit_line)
        )
        if not entry < stop < exit_:
            raise ValueError("Expected entry, stop, exit in travel order")
        return self

    def distance_to_stop(self, p: Point) -> float:
        return self.line_chainage(self.stop_line) - self.chainage(p)


class Crosswalk(Model):
    crosswalk_id: str = Field(min_length=1)
    approach: Direction
    waiting_zone: RoadPolygon
    crossing_zone: RoadPolygon
    exit_zone: RoadPolygon


class RoadIntersection(Model):
    intersection_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    world_frame: str = Field(min_length=1)
    unit: Literal["m"] = "m"
    lanes: tuple[RoadLane, ...] = Field(min_length=1, max_length=64)
    crosswalks: tuple[Crosswalk, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def unique(self) -> "RoadIntersection":
        if len({x.lane_id for x in self.lanes}) != len(self.lanes) or len(
            {x.crosswalk_id for x in self.crosswalks}
        ) != len(self.crosswalks):
            raise ValueError("Duplicate road identity")
        return self

    def assign(self, p: Point, previous: str | None = None) -> RoadLane | None:
        matches = [lane for lane in self.lanes if lane.region.contains(p)]
        old = next((lane for lane in matches if lane.lane_id == previous), None)
        return old or (matches[0] if len(matches) == 1 else None)

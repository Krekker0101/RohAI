import json
import math
import os
import tempfile
from pathlib import Path

from pydantic import Field, model_validator
from traffic_core.models import Direction, Model

from traffic_vision.models import Lane, Point

EPSILON = 1e-9


def cross(a: Point, b: Point, p: Point) -> float:
    return (b.x - a.x) * (p.y - a.y) - (b.y - a.y) * (p.x - a.x)


def on_segment(a: Point, b: Point, p: Point) -> bool:
    return (
        abs(cross(a, b, p)) <= EPSILON
        and min(a.x, b.x) - EPSILON <= p.x <= max(a.x, b.x) + EPSILON
        and min(a.y, b.y) - EPSILON <= p.y <= max(a.y, b.y) + EPSILON
    )


def segments_intersect(a: Point, b: Point, c: Point, d: Point) -> bool:
    if any((on_segment(a, b, c), on_segment(a, b, d), on_segment(c, d, a), on_segment(c, d, b))):
        return True
    return cross(a, b, c) * cross(a, b, d) < 0 and cross(c, d, a) * cross(c, d, b) < 0


class Polygon(Model):
    points: tuple[Point, ...] = Field(min_length=3, max_length=64)

    @model_validator(mode="after")
    def simple_normalized(self) -> "Polygon":
        points = self.points
        if any(not (0 <= p.x <= 1 and 0 <= p.y <= 1) for p in points):
            raise ValueError("ROI vertices must be normalized to [0, 1]")
        if len({(p.x, p.y) for p in points}) != len(points):
            raise ValueError("Polygon vertices must be distinct; do not repeat the first vertex")
        edges = [(p, points[(i + 1) % len(points)]) for i, p in enumerate(points)]
        if abs(sum(a.x * b.y - b.x * a.y for a, b in edges)) <= EPSILON:
            raise ValueError("Polygon must have nonzero area")
        for i, (a, b) in enumerate(edges):
            for j, (c, d) in enumerate(edges):
                if j <= i + 1 or (i == 0 and j == len(edges) - 1):
                    continue
                if segments_intersect(a, b, c, d):
                    raise ValueError("Polygon must not self-intersect")
        return self

    def contains(self, point: Point) -> bool:
        """Boundary-inclusive ray casting, equivalent to pointPolygonTest >= 0."""
        inside = False
        for i, a in enumerate(self.points):
            b = self.points[(i + 1) % len(self.points)]
            if on_segment(a, b, point):
                return True
            if (a.y > point.y) != (b.y > point.y):
                crossing_x = a.x + (point.y - a.y) * (b.x - a.x) / (b.y - a.y)
                if point.x < crossing_x:
                    inside = not inside
        return inside


class StopLine(Model):
    start: Point
    end: Point
    upstream: Point

    @model_validator(mode="after")
    def oriented(self) -> "StopLine":
        if any(
            not (0 <= p.x <= 1 and 0 <= p.y <= 1) for p in (self.start, self.end, self.upstream)
        ):
            raise ValueError("Stop line and upstream anchor must be normalized")
        if abs(cross(self.start, self.end, self.upstream)) <= EPSILON:
            raise ValueError("The upstream anchor must be off the stop line")
        return self

    def is_before(self, point: Point) -> bool:
        return (
            cross(self.start, self.end, point) * cross(self.start, self.end, self.upstream)
            >= -EPSILON
        )

    def distance_pixels(self, point: Point, width: int, height: int) -> float:
        a = Point(x=self.start.x * width, y=self.start.y * height)
        b = Point(x=self.end.x * width, y=self.end.y * height)
        p = Point(x=point.x * width, y=point.y * height)
        return abs(cross(a, b, p)) / math.hypot(b.x - a.x, b.y - a.y)


class ApproachGeometry(Model):
    direction: Direction
    incoming: Polygon
    queue_zone: Polygon
    stop_line: StopLine
    pedestrian_crossing: Polygon | None = None
    meters_per_pixel: float | None = Field(default=None, gt=0)
    enabled: bool = True


class IntersectionGeometry(Model):
    schema_version: int = 1
    source_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    reference_width: int = Field(default=1280, ge=100, le=16384)
    reference_height: int = Field(default=720, ge=100, le=16384)
    detection_zone: Polygon
    approaches: tuple[ApproachGeometry, ...]

    @model_validator(mode="after")
    def complete(self) -> "IntersectionGeometry":
        if self.schema_version != 1:
            raise ValueError("Unsupported geometry schema version")
        if len(self.approaches) != 4 or {a.direction for a in self.approaches} != set(Direction):
            raise ValueError("Exactly four distinct directions are required")
        return self

    def approach(self, lane: Lane) -> ApproachGeometry | None:
        return next(
            (a for a in self.approaches if a.enabled and a.direction.value == lane.value), None
        )

    def assign(self, point: Point, previous: Lane = Lane.UNKNOWN, pedestrian: bool = False) -> Lane:
        if not self.detection_zone.contains(point):
            return Lane.UNKNOWN
        candidates = [
            Lane(a.direction.value)
            for a in self.approaches
            if a.enabled
            and (
                (a.pedestrian_crossing is not None and a.pedestrian_crossing.contains(point))
                if pedestrian
                else a.incoming.contains(point)
            )
        ]
        if previous in candidates:
            return previous
        return candidates[0] if len(candidates) == 1 else Lane.UNKNOWN


def load_geometry(path: Path) -> IntersectionGeometry:
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml  # type: ignore[import-untyped]

        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    else:
        value = json.loads(path.read_text(encoding="utf-8"))
    return IntersectionGeometry.model_validate(value)


class GeometryStore:
    """Validated camera IDs and atomic replace prevent path traversal and partial files."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory.resolve()

    def load(self, source_id: str) -> IntersectionGeometry:
        import re

        if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", source_id) is None:
            raise ValueError("Invalid source ID")
        result = load_geometry(self.directory / f"{source_id}.json")
        if result.source_id != source_id:
            raise ValueError("Stored geometry source ID does not match its filename")
        return result

    def save(self, geometry: IntersectionGeometry) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = self.directory / f"{geometry.source_id}.json"
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.directory, suffix=".json", delete=False
            ) as output:
                temporary = output.name
                output.write(geometry.model_dump_json(indent=2))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            if temporary is not None and Path(temporary).exists():
                Path(temporary).unlink()

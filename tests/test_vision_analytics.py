from pathlib import Path

import pytest
from pydantic import ValidationError
from traffic_core.models import ObjectKind
from traffic_vision.analytics import TrafficAnalyzer
from traffic_vision.contracts import BoundingBox
from traffic_vision.geometry import (
    GeometryStore,
    IntersectionGeometry,
    Polygon,
    StopLine,
    load_geometry,
)
from traffic_vision.models import Detection, Lane, Point
from traffic_vision.settings import AnalyticsSettings


@pytest.fixture
def geometry() -> IntersectionGeometry:
    return load_geometry(Path("configs/cameras/demo.json"))


def observation(
    timestamp: float,
    identity: int = 1,
    x: float = 0.43,
    y: float = 0.2,
    kind: ObjectKind = ObjectKind.CAR,
) -> Detection:
    return Detection(
        kind=kind,
        confidence=0.9,
        timestamp=timestamp,
        track_id=identity,
        bbox=BoundingBox(x1=x * 1280 - 20, y1=y * 720 - 60, x2=x * 1280 + 20, y2=y * 720),
    )


def test_polygon_boundary_concavity_and_invalid_vertices() -> None:
    polygon = Polygon(
        points=(
            Point(x=0, y=0),
            Point(x=1, y=0),
            Point(x=1, y=0.3),
            Point(x=0.3, y=0.3),
            Point(x=0.3, y=1),
            Point(x=0, y=1),
        )
    )
    assert polygon.contains(Point(x=0.1, y=0.8))
    assert polygon.contains(Point(x=0.3, y=0.5))
    assert polygon.contains(Point(x=0, y=0))
    assert not polygon.contains(Point(x=0.8, y=0.8))
    with pytest.raises(ValidationError):
        Polygon(points=(Point(x=0, y=0), Point(x=1, y=1), Point(x=1, y=0), Point(x=0, y=1)))
    with pytest.raises(ValidationError):
        Polygon(points=(Point(x=0, y=0), Point(x=1.1, y=0), Point(x=0, y=1)))


def test_direction_and_crossing_assignment(geometry: IntersectionGeometry) -> None:
    for point, expected in (
        (Point(x=0.43, y=0.2), Lane.NORTH),
        (Point(x=0.56, y=0.8), Lane.SOUTH),
        (Point(x=0.8, y=0.42), Lane.EAST),
        (Point(x=0.2, y=0.57), Lane.WEST),
    ):
        assert geometry.assign(point) == expected
    assert geometry.assign(Point(x=0.55, y=0.5)) == Lane.UNKNOWN
    assert geometry.assign(Point(x=0.43, y=0.38), pedestrian=True) == Lane.NORTH
    assert geometry.assign(Point(x=0.43, y=0.2), pedestrian=True) == Lane.UNKNOWN


def test_oriented_stop_line_and_distance() -> None:
    line = StopLine(
        start=Point(x=0.2, y=0.5), end=Point(x=0.8, y=0.5), upstream=Point(x=0.4, y=0.1)
    )
    assert line.is_before(Point(x=0.5, y=0.4))
    assert line.is_before(Point(x=0.5, y=0.5))
    assert not line.is_before(Point(x=0.5, y=0.6))
    assert line.distance_pixels(Point(x=0.5, y=0.4), 1000, 1000) == pytest.approx(100)
    with pytest.raises(ValidationError):
        StopLine(start=line.start, end=line.end, upstream=Point(x=0.4, y=0.5))


def test_stationary_confirmation_wait_and_moving_reset(geometry: IntersectionGeometry) -> None:
    analyzer = TrafficAnalyzer(geometry, AnalyticsSettings())
    for frame_id in range(10):
        now = frame_id / 10
        snapshot = analyzer.process(
            (observation(now),),
            source_id="demo",
            timestamp=now,
            frame_id=frame_id,
            width=1280,
            height=720,
        )
        track = snapshot.tracks[0]
        assert track.queued == (frame_id >= 3)
    assert track.waiting_started_at == pytest.approx(0.1)
    assert track.current_wait_time == pytest.approx(0.8)
    assert snapshot.traffic.directions["north"].queue_count == 1
    assert snapshot.traffic.directions["north"].queue_confidence == pytest.approx(0.9)
    assert snapshot.traffic.directions["north"].estimated_queue_length > 100
    moved = analyzer.process(
        (observation(1, x=0.47),),
        source_id="demo",
        timestamp=1,
        frame_id=10,
        width=1280,
        height=720,
    )
    assert moved.tracks[0].current_wait_time == 0
    assert not moved.tracks[0].queued
    assert moved.tracks[0].total_observed_wait_seconds == pytest.approx(0.8)


def test_queue_requires_roi_and_before_stop_line(geometry: IntersectionGeometry) -> None:
    north = geometry.approaches[0]
    expanded = north.model_copy(update={"queue_zone": north.incoming})
    geometry = geometry.model_copy(update={"approaches": (expanded, *geometry.approaches[1:])})
    analyzer = TrafficAnalyzer(geometry, AnalyticsSettings())
    for frame_id in range(8):
        now = frame_id / 10
        result = analyzer.process(
            (observation(now, y=0.355),),
            source_id="demo",
            timestamp=now,
            frame_id=frame_id,
            width=1280,
            height=720,
        )
    assert result.tracks[0].lane == Lane.NORTH
    assert result.tracks[0].speed_px_per_second == 0
    assert not result.tracks[0].queued


def test_no_duplicate_counting_for_frames_detections_or_reappearance(
    geometry: IntersectionGeometry,
) -> None:
    analyzer = TrafficAnalyzer(geometry, AnalyticsSettings())
    for frame_id in range(10):
        now = frame_id / 10
        duplicate = (observation(now), observation(now))
        result = analyzer.process(
            duplicate, source_id="demo", timestamp=now, frame_id=frame_id, width=1280, height=720
        )
        assert (
            analyzer.process(
                duplicate,
                source_id="demo",
                timestamp=now,
                frame_id=frame_id,
                width=1280,
                height=720,
            )
            is result
        )
        assert result.traffic.unique_vehicle_arrivals == 1
        assert result.traffic.directions["north"].car_count == 1
    analyzer.process((), source_id="demo", timestamp=5, frame_id=50, width=1280, height=720)
    assert not analyzer.tracks
    result = analyzer.process(
        (observation(5.1),), source_id="demo", timestamp=5.1, frame_id=51, width=1280, height=720
    )
    assert result.traffic.unique_vehicle_arrivals == 1
    assert result.tracks[0].first_seen == 5.1


def test_missing_detection_and_large_gap_do_not_accrue_wait(geometry: IntersectionGeometry) -> None:
    analyzer = TrafficAnalyzer(geometry, AnalyticsSettings())
    for frame_id in range(8):
        now = frame_id / 10
        analyzer.process(
            (observation(now),),
            source_id="demo",
            timestamp=now,
            frame_id=frame_id,
            width=1280,
            height=720,
        )
    analyzer.process((), source_id="demo", timestamp=0.8, frame_id=8, width=1280, height=720)
    result = analyzer.process(
        (observation(0.9),), source_id="demo", timestamp=0.9, frame_id=9, width=1280, height=720
    )
    assert result.tracks[0].current_wait_time == 0
    assert result.tracks[0].speed_px_per_second is None
    result = analyzer.process(
        (observation(2),), source_id="demo", timestamp=2, frame_id=20, width=1280, height=720
    )
    assert result.tracks[0].current_wait_time == 0
    assert result.tracks[0].first_seen == 0
    assert len(result.tracks[0].trajectory) <= 32


def test_epoch_resets_identity_and_timestamps_are_ordered(geometry: IntersectionGeometry) -> None:
    analyzer = TrafficAnalyzer(geometry, AnalyticsSettings())
    first = analyzer.process(
        (observation(0),), source_id="demo", timestamp=0, frame_id=0, width=1280, height=720
    )
    second = analyzer.process(
        (observation(0),),
        source_id="demo",
        timestamp=0,
        frame_id=0,
        width=1280,
        height=720,
        stream_epoch=1,
    )
    assert first.tracks[0].track_uid != second.tracks[0].track_uid
    with pytest.raises(ValueError):
        analyzer.process(
            (observation(0),),
            source_id="demo",
            timestamp=0,
            frame_id=1,
            width=1280,
            height=720,
            stream_epoch=1,
        )
    with pytest.raises(ValueError):
        analyzer.process((), source_id="wrong", timestamp=1, frame_id=1, width=1280, height=720)


def test_unknown_ids_and_untracked_detections_do_not_create_arrivals(
    geometry: IntersectionGeometry,
) -> None:
    analyzer = TrafficAnalyzer(geometry, AnalyticsSettings())
    unknown = observation(0, x=0.8, y=0.8)
    untracked = observation(0).model_copy(update={"track_id": None})
    result = analyzer.process(
        (unknown, untracked), source_id="demo", timestamp=0, frame_id=0, width=1280, height=720
    )
    assert result.traffic.unique_vehicle_arrivals == 0
    assert result.traffic.unknown_count == 1
    assert len(result.detections) == 2


def test_geometry_store_atomic_roundtrip_and_camera_id_validation(
    tmp_path: Path, geometry: IntersectionGeometry
) -> None:
    store = GeometryStore(tmp_path)
    store.save(geometry)
    assert store.load("demo") == geometry
    assert list(tmp_path.iterdir()) == [tmp_path / "demo.json"]
    with pytest.raises(ValueError):
        store.load("../demo")


def test_length_units_and_core_state_adapter(geometry: IntersectionGeometry) -> None:
    north = geometry.approaches[0].model_copy(update={"meters_per_pixel": 0.05})
    geometry = geometry.model_copy(update={"approaches": (north, *geometry.approaches[1:])})
    analyzer = TrafficAnalyzer(geometry, AnalyticsSettings())
    for frame_id in range(5):
        now = frame_id / 10
        result = analyzer.process(
            (observation(now),),
            source_id="demo",
            timestamp=now,
            frame_id=frame_id,
            width=1280,
            height=720,
        )
    metrics = result.traffic.directions["north"]
    assert metrics.queue_length_unit == "m"
    assert metrics.estimated_queue_length == pytest.approx(8.4)
    core = analyzer.to_core_state(result)
    assert core.approaches[0].queue_length == 1
    assert core.approaches[0].traffic_score == metrics.traffic_score

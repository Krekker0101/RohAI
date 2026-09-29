from datetime import UTC, datetime, timedelta

import pytest
from traffic_core.models import Direction, ObjectKind
from traffic_core.traffic_events import TrafficEstimation
from traffic_core.traffic_events import TrafficEventType as Event
from traffic_vision.alignment import AlignmentEvidence, LandmarkAlignmentMonitor
from traffic_vision.calibration import (
    CalibrationMetadata,
    CalibrationReference,
    CalibrationStatus,
    CalibrationValidator,
    CameraCalibration,
    RoadPlaneTransform,
)
from traffic_vision.contracts import BoundingBox
from traffic_vision.models import Detection, Point
from traffic_vision.road_geometry import RoadIntersection, RoadLane, RoadLine, RoadPolygon
from traffic_vision.state_estimation import TrafficStateEstimator
from traffic_vision.validation import (
    BenchmarkRequest,
    IdentitySample,
    MetricSample,
    benchmark,
    benchmark_identities,
)

NOW = datetime.now(UTC)


def world(x: float, y: float) -> Point:
    return Point(x=0.1 * x / (1 + 0.002 * y), y=0.1 * y / (1 + 0.002 * y))


def image(x: float, y: float) -> Point:
    return Point(x=10 * x / (1 - 0.02 * y), y=10 * y / (1 - 0.02 * y))


def calibration() -> CameraCalibration:
    points = (
        (10, 10),
        (990, 10),
        (990, 990),
        (10, 990),
        (200, 200),
        (800, 200),
        (200, 800),
        (800, 800),
    )
    refs = tuple(
        CalibrationReference(point_id=str(i), image=Point(x=x, y=y), world=world(x, y))
        for i, (x, y) in enumerate(points)
    )
    return CalibrationValidator.fit(
        width=1000,
        height=1000,
        references=refs[:4],
        validation=refs[4:],
        metadata=CalibrationMetadata(
            camera_id="cam",
            version="c1",
            timestamp=NOW,
            image_model="rectified",
            world_frame="survey",
            validation_error_m=0,
            validation_max_error_m=0,
        ),
    )


def polygon(x1: float, y1: float, x2: float, y2: float) -> RoadPolygon:
    return RoadPolygon(
        points=(Point(x=x1, y=y1), Point(x=x2, y=y1), Point(x=x2, y=y2), Point(x=x1, y=y2))
    )


def lane(name: str = "n1", x1: float = 2, x2: float = 18) -> RoadLane:
    def line(y: float) -> RoadLine:
        return RoadLine(
            start=Point(x=x1, y=y), end=Point(x=x2, y=y), upstream=Point(x=(x1 + x2) / 2, y=y - 1)
        )

    return RoadLane(
        lane_id=name,
        approach=Direction.NORTH,
        region=polygon(x1, 0, x2, 30),
        entry_zone=polygon(x1, 3, x2, 7),
        queue_zone=polygon(x1, 5, x2, 20),
        exit_zone=polygon(x1, 25, x2, 30),
        entry_line=line(5),
        stop_line=line(20),
        exit_line=line(25),
        centerline=(Point(x=(x1 + x2) / 2, y=0), Point(x=(x1 + x2) / 2, y=30)),
        movement_direction="straight",
    )


def road(lanes: tuple[RoadLane, ...] | None = None) -> RoadIntersection:
    return RoadIntersection(
        intersection_id="i1", version="g1", world_frame="survey", lanes=lanes or (lane(),)
    )


def detection(
    t: float, y: float, x: float = 10, identity: int = 1, large: bool = False
) -> Detection:
    p = image(x, y)
    return Detection(
        kind=ObjectKind.TRUCK if large else ObjectKind.CAR,
        confidence=0.95,
        track_id=identity,
        timestamp=t,
        ground_contact=p,
        bbox=BoundingBox(
            x1=max(0, p.x - 10), y1=max(0, p.y - (150 if large else 10)), x2=p.x + 10, y2=p.y
        ),
    )


class Scene:
    def __init__(self, geometry: RoadIntersection | None = None) -> None:
        self.calibration = calibration()
        self.monitor = LandmarkAlignmentMonitor()
        self.estimator = TrafficStateEstimator(geometry or road(), self.calibration, self.monitor)

    def process(
        self, t: float, *detections: Detection, width: int = 1000, session: str = "cam:0"
    ) -> TrafficEstimation:
        anchors = tuple(
            Point(x=x, y=y) for x, y in ((100, 100), (900, 100), (900, 900), (100, 900))
        )
        self.monitor.submit(
            AlignmentEvidence(
                camera_id="cam",
                calibration_version="c1",
                timestamp=t,
                width=width,
                height=1000,
                reference=anchors,
                observed=anchors,
                source="synthetic-static-truth",
            )
        )
        return self.estimator.process(
            detections,
            source="cam",
            session=session,
            timestamp=t,
            width=width,
            height=1000,
            now=NOW,
        )


@pytest.mark.parametrize("x,y", [(5, 5), (5, 25), (15, 5), (15, 25), (10, 15)])
def test_perspective_near_far_projection(x: float, y: float) -> None:
    c = calibration()
    p = RoadPlaneTransform(c).project(image(x, y), 1000, 1000)
    assert p.x == pytest.approx(x, abs=1e-5)
    assert p.y == pytest.approx(y, abs=1e-5)
    assert c.metadata.validation_error_m < 1e-5


def test_invalid_calibration_and_resolution() -> None:
    c = calibration()
    assert (
        CalibrationValidator.validate(c, camera_id="cam", width=500, height=1000, now=NOW).status
        == CalibrationStatus.SUSPECT
    )
    assert (
        CalibrationValidator.validate(c, camera_id="other", width=1000, height=1000, now=NOW).status
        == CalibrationStatus.INVALID
    )
    assert (
        CalibrationValidator.validate(
            c, camera_id="cam", width=1000, height=1000, now=NOW + timedelta(days=31)
        ).status
        == CalibrationStatus.STALE
    )
    broken = c.model_copy(
        update={"homography": ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 1.0))}
    )
    assert (
        CalibrationValidator.validate(
            broken, camera_id="cam", width=1000, height=1000, now=NOW
        ).status
        == CalibrationStatus.INVALID
    )
    with pytest.raises(ValueError):
        RoadPlaneTransform(c).project(image(10, 10), 500, 1000)


def test_validation_holdout_is_independent() -> None:
    c = calibration()
    with pytest.raises(ValueError, match="independent"):
        CameraCalibration.model_validate(
            {**c.model_dump(), "validation_points": c.reference_points}
        )


def test_movement_latches_suspect() -> None:
    scene = Scene()
    assert scene.process(0).calibration_status == "VALID"
    e = scene.monitor.evidence
    assert e is not None
    scene.monitor.submit(
        e.model_copy(
            update={
                "timestamp": 0.1,
                "observed": tuple(Point(x=p.x + 10, y=p.y) for p in e.observed),
            }
        )
    )
    result = scene.estimator.process(
        (), source="cam", session="cam:0", timestamp=0.1, width=1000, height=1000, now=NOW
    )
    assert result.calibration_status == "SUSPECT"
    assert scene.process(0.2).calibration_status == "SUSPECT"
    assert all(m.value is None for m in result.metrics["north"].values())


def test_missing_alignment_never_valid() -> None:
    est = TrafficStateEstimator(road(), calibration())
    result = est.process(
        (), source="cam", session="s", timestamp=0, width=1000, height=1000, now=NOW
    )
    assert result.calibration_status == "SUSPECT"


def test_geometric_arrival_stop_exit_and_units() -> None:
    scene = Scene()
    first = scene.process(0, detection(0, 4))
    assert not first.events
    entered = scene.process(0.5, detection(0.5, 6))
    assert [e.kind for e in entered.events] == [Event.VEHICLE_ENTERED_APPROACH]
    assert entered.events[0].timestamp == pytest.approx(0.25, abs=1e-5)
    assert entered.tracks[0].estimated_speed_mps.value == pytest.approx(4, abs=1e-5)
    stopped = scene.process(1, detection(1, 6))
    assert stopped.tracks[0].queued
    assert stopped.metrics["north"]["queue_length_m"].value == pytest.approx(14, abs=1e-5)
    assert stopped.metrics["north"]["mean_wait"].unit == "s"
    assert {e.kind for e in stopped.events} == {Event.VEHICLE_STOPPED, Event.VEHICLE_JOINED_QUEUE}
    scene.process(1.5, detection(1.5, 19))
    crossed = scene.process(2, detection(2, 21))
    assert Event.VEHICLE_CROSSED_STOP_LINE in [e.kind for e in crossed.events]
    exited = scene.process(2.5, detection(2.5, 26))
    assert Event.VEHICLE_EXITED_INTERSECTION in [e.kind for e in exited.events]
    assert exited.metrics["north"]["throughput"].value == 1
    assert all(e.calibration_version == "c1" and e.geometry_version == "g1" for e in exited.events)


def test_first_seen_inside_not_arrival() -> None:
    scene = Scene()
    scene.process(0, detection(0, 10))
    result = scene.process(0.5, detection(0.5, 10))
    assert result.metrics["north"]["arrival_rate"].value == 0
    assert all(e.kind != Event.VEHICLE_ENTERED_APPROACH for e in result.events)


def test_multiple_lanes_boundary_and_same_speed() -> None:
    geometry = road((lane("n1", 2, 10), lane("n2", 10, 18)))
    assert geometry.assign(Point(x=10, y=10)) is None
    assert geometry.assign(Point(x=10, y=10), "n1").lane_id == "n1"  # type: ignore[union-attr]
    scene = Scene(geometry)
    scene.process(0, detection(0, 6, 5, 1), detection(0, 20, 15, 2))
    result = scene.process(0.5, detection(0.5, 7, 5, 1), detection(0.5, 21, 15, 2))
    assert {s.current_lane for s in result.tracks} == {"n1", "n2"}
    assert all(s.estimated_speed_mps.value == pytest.approx(2, abs=1e-5) for s in result.tracks)
    assert result.metrics["north"]["occupancy"].value == 1


def test_large_vehicle_uses_ground_contact() -> None:
    lengths = []
    for large in (False, True):
        scene = Scene()
        scene.process(0, detection(0, 10, large=large))
        result = scene.process(0.5, detection(0.5, 10, large=large))
        lengths.append(result.metrics["north"]["queue_length_m"].value)
    assert lengths[0] == pytest.approx(lengths[1])


def test_partial_bbox_requires_explicit_contact() -> None:
    scene = Scene()
    d = detection(0, 10).model_copy(
        update={"ground_contact": None, "bbox": BoundingBox(x1=0, y1=1, x2=50, y2=100)}
    )
    result = scene.process(0, d)
    assert "partial_bbox_contact_unknown" in result.reasons
    assert result.metrics["north"]["queue_length_m"].value is None


def test_occlusion_recovery_id_change_without_duplicate_arrival() -> None:
    scene = Scene()
    scene.process(0, detection(0, 4))
    entered = scene.process(0.2, detection(0.2, 6))
    uid = entered.tracks[0].track_uid
    missing = scene.process(0.4)
    assert missing.identity.lost_tracks == 1
    assert missing.metrics["north"]["queue_vehicle_count"].value is None
    recovered = scene.process(0.6, detection(0.6, 6, identity=99))
    assert recovered.tracks[0].track_uid == uid
    assert recovered.identity.recovered_tracks == 1
    assert recovered.identity.reconciled_id_changes == 1
    assert recovered.identity.id_switches is None
    assert all(e.kind != Event.VEHICLE_ENTERED_APPROACH for e in recovered.events)


def test_resolution_change_suppresses_metres() -> None:
    scene = Scene()
    scene.process(0, detection(0, 10))
    result = scene.process(0.5, detection(0.5, 10), width=500)
    assert result.calibration_status == "SUSPECT"
    assert result.metrics["north"]["queue_length_m"].value is None


def test_benchmark_errors_bias_percentiles_and_missing() -> None:
    request = BenchmarkRequest(
        samples=tuple(
            MetricSample(metric="distance", unit="m", estimated=e, ground_truth=t)
            for e, t in ((12, 10), (8, 10), (None, 10), (1, 0))
        )
    )
    result = benchmark(request)
    summary = result.summaries[0]
    assert summary.coverage == 0.75
    assert summary.mae == pytest.approx(5 / 3)
    assert summary.bias == pytest.approx(1 / 3)
    assert summary.absolute_error_percentiles["p95"] == 2
    assert result.errors[-1].relative_error is None
    with pytest.raises(ValueError, match="unit"):
        BenchmarkRequest(
            samples=(
                MetricSample(metric="x", unit="m", estimated=1, ground_truth=1),
                MetricSample(metric="x", unit="px", estimated=1, ground_truth=1),
            )
        )


def test_identity_ground_truth_benchmark() -> None:
    report = benchmark_identities(
        tuple(
            IdentitySample(
                timestamp=i,
                ground_truth_id="car",
                estimated_id=identity,
                arrival_events=1 if identity else 0,
            )
            for i, identity in enumerate(("a", None, "b", "b"))
        )
    )
    assert (
        report.id_switches
        == report.track_fragmentation
        == report.lost_tracks
        == report.recovered_tracks
        == 1
    )
    assert report.duplicate_arrivals == 2


def test_pedestrian_geometric_events() -> None:
    from traffic_vision.road_geometry import Crosswalk

    geometry = road().model_copy(
        update={
            "crosswalks": (
                Crosswalk(
                    crosswalk_id="cw",
                    approach=Direction.NORTH,
                    waiting_zone=polygon(3, 8, 7, 10),
                    crossing_zone=polygon(3, 11, 7, 13),
                    exit_zone=polygon(3, 14, 7, 16),
                ),
            )
        }
    )
    scene = Scene(geometry)
    kinds: list[Event] = []
    for i, y in enumerate((7, 9, 12, 15)):
        t = i * 0.5
        d = detection(t, y, 5).model_copy(update={"kind": ObjectKind.PEDESTRIAN})
        kinds.extend(e.kind for e in scene.process(t, d).events)
    assert kinds == [
        Event.PEDESTRIAN_ARRIVED,
        Event.PEDESTRIAN_STARTED_CROSSING,
        Event.PEDESTRIAN_CLEARED_CROSSING,
    ]


def test_ambiguous_reconciliation_is_not_a_claimed_recovery() -> None:
    scene = Scene()
    scene.process(0, detection(0, 10, 9.8, 1), detection(0, 10, 10.2, 2))
    result = scene.process(0.5, detection(0.5, 10, 10, 3))
    assert "identity_reconciliation_ambiguous" in result.reasons
    assert result.identity.reconciled_id_changes == 0


def test_crossing_finite_segment_and_reverse() -> None:
    line = lane().stop_line
    assert line.crossing(Point(x=50, y=19), Point(x=50, y=21)) is None
    assert line.crossing(Point(x=10, y=21), Point(x=10, y=19)) is None


def test_expired_track_does_not_immediately_become_empty() -> None:
    scene = Scene()
    scene.process(0, detection(0, 10))
    result = scene.process(2)
    assert "expired_track_uncertainty" in result.reasons
    assert result.metrics["north"]["queue_vehicle_count"].value is None


def test_resolution_change_requires_reverification_after_restore() -> None:
    scene = Scene()
    scene.process(0)
    scene.process(0.5, width=500)
    assert scene.process(1).calibration_status == "SUSPECT"


def test_benchmark_http_api() -> None:
    from fastapi.testclient import TestClient
    from smart_traffic_backend.config import Settings
    from smart_traffic_backend.main import create_app

    with TestClient(create_app(Settings())) as client:
        response = client.post(
            "/api/v1/vision/benchmark",
            json={
                "samples": [
                    {"metric": "queue_length_m", "unit": "m", "estimated": 11, "ground_truth": 10}
                ]
            },
        )
        assert response.status_code == 200
        assert response.json()["summaries"][0]["mae"] == 1
        assert client.post("/api/v1/vision/benchmark", json={"samples": []}).status_code == 422


def test_survey_support_and_error_sensitivity() -> None:
    transform = RoadPlaneTransform(calibration())
    with pytest.raises(ValueError, match="support"):
        transform.project(Point(x=1, y=500), 1000, 1000)
    assert (
        transform.positional_error_bound(image(10, 6), 1000, 1000)
        > transform.positional_error_bound(image(10, 20), 1000, 1000)
        > 0
    )


def test_analyzer_adapts_metric_state_to_controller() -> None:
    from pathlib import Path

    from traffic_core.models import ObservationEvidence
    from traffic_vision.analytics import TrafficAnalyzer
    from traffic_vision.geometry import load_geometry
    from traffic_vision.settings import AnalyticsSettings

    scene = Scene()
    geometry = load_geometry(Path("configs/cameras/demo.json")).model_copy(
        update={"source_id": "cam", "road": road(), "camera_calibration": scene.calibration}
    )
    analyzer = TrafficAnalyzer(geometry, AnalyticsSettings(), scene.monitor)
    for i in range(3):
        t = i * 0.5
        scene.process(t)
        evidence = ObservationEvidence.simulated(t).model_copy(
            update={
                "source_camera_ids": ("cam",),
                "camera_health": dict.fromkeys(
                    (
                        "camera_connected",
                        "frame_fresh",
                        "frame_rate_adequate",
                        "image_not_frozen",
                        "not_black",
                        "not_underexposed",
                        "not_overexposed",
                        "not_blurred",
                        "no_major_occlusion_suspected",
                        "roi_visible",
                        "inference_alive",
                        "analytics_alive",
                        "calibration_available",
                        "calibration_fresh",
                    ),
                    True,
                ),
            }
        )
        snapshot = analyzer.process(
            (detection(t, 10),),
            source_id="cam",
            stream_epoch=0,
            timestamp=t,
            frame_id=i,
            width=1000,
            height=1000,
            health={d.value: evidence for d in Direction},
        )
    metric = snapshot.traffic.directions["north"]
    assert metric.observation.reliable, metric.observation.failure_reasons
    assert metric.queue_length_unit == "m"
    assert metric.estimated_queue_length == pytest.approx(10, abs=1e-5)
    core = analyzer.to_core_state(snapshot)
    north = next(a for a in core.approaches if a.direction == Direction.NORTH)
    assert north.queue_length == 1
    assert north.mean_wait_seconds == pytest.approx(1)
    assert not next(
        a for a in core.approaches if a.direction == Direction.SOUTH
    ).observation.reliable

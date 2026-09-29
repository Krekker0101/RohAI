"""Reproducible synthetic metric benchmark; does not evaluate detector accuracy."""

import argparse
import json
import math
from datetime import UTC, datetime
from pathlib import Path

from traffic_core.models import Direction, ObjectKind
from traffic_vision.alignment import AlignmentEvidence, LandmarkAlignmentMonitor
from traffic_vision.calibration import (
    CalibrationMetadata,
    CalibrationReference,
    CalibrationValidator,
)
from traffic_vision.contracts import BoundingBox
from traffic_vision.models import Detection, Point
from traffic_vision.road_geometry import RoadIntersection, RoadLane, RoadLine, RoadPolygon
from traffic_vision.state_estimation import TrafficStateEstimator
from traffic_vision.validation import BenchmarkRequest, MetricSample, benchmark


def run_benchmark() -> dict[str, object]:
    now = datetime.now(UTC)

    def world(x: float, y: float) -> Point:
        return Point(x=0.1 * x / (1 + 0.002 * y), y=0.1 * y / (1 + 0.002 * y))

    def pixel(x: float, y: float) -> Point:
        return Point(x=10 * x / (1 - 0.02 * y), y=10 * y / (1 - 0.02 * y))

    locations = (
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
        for i, (x, y) in enumerate(locations)
    )
    calibration = CalibrationValidator.fit(
        width=1000,
        height=1000,
        references=refs[:4],
        validation=refs[4:],
        metadata=CalibrationMetadata(
            camera_id="benchmark",
            version="synthetic-v1",
            timestamp=now,
            image_model="rectified",
            world_frame="survey",
            validation_error_m=0,
            validation_max_error_m=0,
        ),
    )

    def polygon(lo: float, hi: float) -> RoadPolygon:
        return RoadPolygon(
            points=(Point(x=2, y=lo), Point(x=18, y=lo), Point(x=18, y=hi), Point(x=2, y=hi))
        )

    def line(y: float) -> RoadLine:
        return RoadLine(start=Point(x=2, y=y), end=Point(x=18, y=y), upstream=Point(x=10, y=y - 1))

    lane = RoadLane(
        lane_id="n1",
        approach=Direction.NORTH,
        region=polygon(0, 30),
        entry_zone=polygon(3, 7),
        queue_zone=polygon(5, 24),
        exit_zone=polygon(26, 30),
        entry_line=line(5),
        stop_line=line(24),
        exit_line=line(26),
        centerline=(Point(x=10, y=0), Point(x=10, y=30)),
        movement_direction="straight",
    )
    road = RoadIntersection(
        intersection_id="synthetic", version="g1", world_frame="survey", lanes=(lane,)
    )
    anchors = tuple(Point(x=x, y=y) for x, y in ((100, 100), (900, 100), (900, 900), (100, 900)))
    samples: list[MetricSample] = []
    for scene in range(24):
        base = 6 if scene < 12 else 19
        stratum = "near" if scene < 12 else "far"
        monitor = LandmarkAlignmentMonitor()
        estimator = TrafficStateEstimator(road, calibration, monitor)
        for step in range(4):
            t = step * 0.5
            y = base if step == 0 else base + 1
            contact = pixel(10, y)
            # Deterministic subpixel localization errors, no random/hardware dependence.
            contact = Point(
                x=contact.x + 0.4 * math.sin(scene * 2 + step),
                y=contact.y + 0.4 * math.cos(scene * 3 + step),
            )
            monitor.submit(
                AlignmentEvidence(
                    camera_id="benchmark",
                    calibration_version="synthetic-v1",
                    timestamp=t,
                    width=1000,
                    height=1000,
                    reference=anchors,
                    observed=anchors,
                    source="synthetic-static-landmarks",
                )
            )
            result = estimator.process(
                (
                    Detection(
                        kind=ObjectKind.CAR,
                        confidence=0.95,
                        track_id=1,
                        timestamp=t,
                        ground_contact=contact,
                        bbox=BoundingBox(
                            x1=contact.x - 10, y1=contact.y - 20, x2=contact.x + 10, y2=contact.y
                        ),
                    ),
                ),
                source="benchmark",
                session=f"run{scene}",
                timestamp=t,
                width=1000,
                height=1000,
                now=now,
            )
            state = result.tracks[0]
            samples.append(
                MetricSample(
                    metric="distance_to_stop_line",
                    unit="m",
                    stratum=stratum,
                    estimated=state.distance_to_stop_line.value,
                    ground_truth=24 - y,
                )
            )
            if step:
                samples.append(
                    MetricSample(
                        metric="estimated_speed_mps",
                        unit="m/s",
                        stratum=stratum,
                        estimated=state.estimated_speed_mps.value,
                        ground_truth=2 if step == 1 else 0,
                    )
                )
            if step >= 2:
                for name, truth in (
                    ("queue_length_m", 24 - y),
                    ("queue_vehicle_count", 1),
                    ("mean_wait", t - 0.5),
                    ("max_wait", t - 0.5),
                    ("arrival_rate", 0),
                    ("departure_rate", 0),
                    ("throughput", 0),
                    ("occupancy", 1),
                ):
                    metric = result.metrics["north"][name]
                    samples.append(
                        MetricSample(
                            metric=name,
                            unit=metric.unit,
                            stratum=stratum,
                            estimated=metric.value,
                            ground_truth=truth,
                        )
                    )
    monitor = LandmarkAlignmentMonitor()
    estimator = TrafficStateEstimator(road, calibration, monitor)
    for step, y in enumerate((4, 6, 19, 21, 26, 28)):
        t = step * 0.5
        contact = pixel(10, y)
        monitor.submit(
            AlignmentEvidence(
                camera_id="benchmark",
                calibration_version="synthetic-v1",
                timestamp=t,
                width=1000,
                height=1000,
                reference=anchors,
                observed=anchors,
                source="synthetic-static-landmarks",
            )
        )
        result = estimator.process(
            (
                Detection(
                    kind=ObjectKind.CAR,
                    confidence=0.95,
                    track_id=1,
                    timestamp=t,
                    ground_contact=contact,
                    bbox=BoundingBox(
                        x1=contact.x - 10, y1=contact.y - 20, x2=contact.x + 10, y2=contact.y
                    ),
                ),
            ),
            source="benchmark",
            session="crossings",
            timestamp=t,
            width=1000,
            height=1000,
            now=now,
        )
        for event in result.events:
            event_truth = {
                "VehicleEnteredApproach": 0.25,
                "VehicleCrossedStopLine": 1.8,
                "VehicleExitedIntersection": 2.0,
            }.get(event.kind.value)
            if event_truth is not None:
                samples.append(
                    MetricSample(
                        metric="event_timestamp",
                        unit="s",
                        stratum=event.kind.value,
                        estimated=event.timestamp,
                        ground_truth=event_truth,
                    )
                )
        if step:
            for name, truth in (
                ("arrival_rate", 60 / t),
                ("departure_rate", 60 / t if step >= 4 else 0),
                ("throughput", 1 if step >= 5 else 0),
            ):
                metric = result.metrics["north"][name]
                samples.append(
                    MetricSample(
                        metric=name,
                        unit=metric.unit,
                        stratum="crossings",
                        estimated=metric.value,
                        ground_truth=truth,
                    )
                )
    report = benchmark(BenchmarkRequest(samples=tuple(samples)))
    return {
        "scenario": "known perspective; 24 trajectories; +/-0.4px contact perturbation",
        "scope": "synthetic geometry only; no detector or real-camera accuracy claim",
        "calibration_rmse_m": calibration.metadata.validation_error_m,
        "sample_count": len(samples),
        "report": report.model_dump(mode="json"),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("docs/state_estimation_benchmark.json"))
    args = parser.parse_args()
    result = run_benchmark()
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"sample_count": result["sample_count"], "output": str(args.output)}))

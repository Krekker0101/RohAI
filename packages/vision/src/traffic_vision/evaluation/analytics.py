"""Replay the deployed observation adapter; never substitute ground truth detections/calibration."""

import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from typing import Any

from traffic_core.models import Direction
from traffic_core.settings import Timing

from traffic_vision.dataset.files import inside
from traffic_vision.dataset.release import open_release, select_frames
from traffic_vision.dataset.schema import CONTROL_FEATURES, FEATURE_UNITS, Layer
from traffic_vision.dataset.validation import read_annotations, validate_dataset
from traffic_vision.evaluation.vision import PredictionRun, finish_report, strata
from traffic_vision.validation import BenchmarkRequest, MetricSample, benchmark


def replay_features(
    release_path: Path, run: PredictionRun
) -> tuple[dict[str, dict[str, dict[str, float | None]]], dict[str, bool]]:
    import cv2
    import numpy as np

    from traffic_vision.alignment import AlignmentEvidence, LandmarkAlignmentMonitor
    from traffic_vision.analytics import TrafficAnalyzer
    from traffic_vision.geometry import load_geometry
    from traffic_vision.health import PerceptionHealthMonitor
    from traffic_vision.models import Detection
    from traffic_vision.settings import VisionSettings
    from traffic_vision.sources import VideoFrame

    release = open_release(release_path)
    root = release_path if release_path.is_dir() else release_path.parent
    sources = {s.source_id: s for s in release.dataset.sources}
    frames = select_frames(release, run.split)
    if {p.frame_id for p in run.frames} != {f.frame_id for f in frames}:
        raise ValueError("Prediction coverage differs from release split")
    predictions = {p.frame_id: p for p in run.frames}
    values: dict[str, dict[str, dict[str, float | None]]] = {}
    reliable: dict[str, bool] = {}
    current: str | None = None
    analyzer: TrafficAnalyzer | None = None
    monitor: LandmarkAlignmentMonitor | None = None
    options = VisionSettings.model_validate(run.settings.get("vision_settings", {}))
    health = PerceptionHealthMonitor(options.health)
    alignment: dict[float, AlignmentEvidence] = {}
    identities: dict[str, int] = {}
    epoch = 0
    for frame in sorted(frames, key=lambda f: (f.sequence_id or f.source_id, f.timestamp)):
        source = sources[frame.source_id]
        sequence = frame.sequence_id or frame.source_id
        if sequence != current:
            current = sequence
            epoch += 1
            monitor = LandmarkAlignmentMonitor()
            analyzer = (
                TrafficAnalyzer(
                    load_geometry(inside(root, source.geometry.path)), options.analytics, monitor
                )
                if source.geometry
                else None
            )
            identities = {}
            alignment = {}
            if source.alignment:
                alignment_rows = [
                    AlignmentEvidence.model_validate(row)
                    for row in json.loads(
                        inside(root, source.alignment.path).read_text(encoding="utf-8")
                    )
                ]
                alignment = {row.timestamp: row for row in alignment_rows}
        if analyzer is None:
            values[frame.frame_id] = {d.value: dict.fromkeys(FEATURE_UNITS) for d in Direction}
            reliable[frame.frame_id] = False
            continue
        assert monitor is not None
        if frame.timestamp in alignment:
            monitor.submit(alignment[frame.timestamp])
        image = cv2.imdecode(
            np.frombuffer(inside(root, frame.image.path).read_bytes(), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        if image is None:
            raise ValueError("Frame became unreadable after validation")
        video = VideoFrame(
            np.asarray(image, dtype=np.uint8),
            frame.timestamp,
            frame.frame_index,
            source.camera_id,
            frame.timestamp,
            epoch,
        )
        detections: list[Detection] = []
        for pred in predictions[frame.frame_id].tracks:
            identity = None
            if pred.track_id is not None:
                if pred.track_id not in identities:
                    identities[pred.track_id] = len(identities) + 1
                identity = identities[pred.track_id]
            detections.append(
                Detection(
                    kind=release.dataset.class_mapping[pred.class_id],
                    confidence=pred.confidence,
                    bbox=pred.bbox,
                    track_id=identity,
                    timestamp=frame.timestamp,
                )
            )
        wall_time = source.captured_at + timedelta(seconds=frame.timestamp)
        evidence = health.inspect(video, analyzer.geometry, frame.timestamp, wall_time=wall_time)
        snapshot = analyzer.process(
            tuple(detections),
            source_id=source.camera_id,
            timestamp=frame.timestamp,
            frame_id=frame.frame_index,
            width=source.width,
            height=source.height,
            stream_epoch=epoch,
            health=evidence,
            calibration_time=wall_time,
        )
        core = analyzer.to_core_state(snapshot)
        by_approach: dict[str, dict[str, float | None]] = {}
        for approach in core.approaches:
            key = approach.direction.value
            metrics = snapshot.traffic.directions[key]
            metric_values: dict[str, float | None] = dict.fromkeys(FEATURE_UNITS)
            if approach.observation.reliable:
                metric_values.update(
                    {
                        "vehicle_count": sum(
                            t.visible and t.kind.value != "pedestrian" and t.lane.value == key
                            for t in snapshot.tracks
                        ),
                        "arrival_count": metrics.unique_arrivals,
                        "queue_count": approach.queue_length,
                        "queue_length_m": metrics.estimated_queue_length
                        if metrics.queue_length_unit == "m"
                        else None,
                        "mean_wait_seconds": approach.mean_wait_seconds,
                        "oldest_wait_seconds": approach.oldest_wait_seconds,
                        "arrival_rate_per_minute": approach.arrival_rate_per_minute,
                        "traffic_score": approach.traffic_score,
                    }
                )
                for name in FEATURE_UNITS:
                    if name.startswith("counts.") and approach.counts is not None:
                        metric_values[name] = float(getattr(approach.counts, name.split(".")[1]))
                    elif name.startswith("pedestrian_"):
                        metric_values[name] = getattr(metrics, name, None)
                if snapshot.traffic.estimation is not None:
                    physical = snapshot.traffic.estimation.metrics.get(key, {})
                    for name in ("throughput", "occupancy", "estimated_speed_mps"):
                        metric_values[name] = physical[name].value if name in physical else None
            by_approach[key] = metric_values
        values[frame.frame_id] = by_approach
        reliable[frame.frame_id] = all(a.observation.reliable for a in core.approaches)
    return values, reliable


def analytics_benchmark(release_path: Path, prediction_path: Path) -> dict[str, Any]:
    release = open_release(release_path)
    root = release_path if release_path.is_dir() else release_path.parent
    run = PredictionRun.model_validate_json(prediction_path.read_text(encoding="utf-8"))
    run.verify()
    if run.dataset_manifest_sha256 != release.manifest_sha256:
        raise ValueError("Predictions belong to another dataset release")
    checked = validate_dataset(release.dataset, root)
    if not checked.valid:
        raise ValueError(checked.model_dump_json())
    frames = select_frames(release, run.split)
    annotations = read_annotations(release.dataset, root)
    values, reliable = replay_features(release_path, run)
    groups = strata(release.dataset, frames)
    sequence_specs = {s.sequence_id: s for s in release.dataset.sequences}
    comparisons: list[dict[str, Any]] = []
    samples: dict[tuple[str, str], list[MetricSample]] = defaultdict(list)
    safety_truth = safety_false_reliable = safety_false_unreliable = 0
    score_errors: dict[tuple[str, str], dict[str, tuple[float, float | None]]] = defaultdict(dict)
    for frame in frames:
        ann = annotations[frame.frame_id]
        if Layer.ANALYTICS not in ann.layers:
            continue
        sequence = sequence_specs.get(frame.sequence_id or "")
        if sequence and frame.timestamp < sequence.score_from_seconds:
            continue
        if ann.control_observable is not None:
            safety_truth += 1
            safety_false_reliable += reliable[frame.frame_id] and not ann.control_observable
            safety_false_unreliable += not reliable[frame.frame_id] and ann.control_observable
        for truth in ann.traffic:
            estimated = values[frame.frame_id][truth.approach.value][truth.feature]
            comparisons.append(
                {
                    "frame_id": frame.frame_id,
                    "source_id": frame.source_id,
                    "approach": truth.approach.value,
                    "feature": truth.feature,
                    "unit": truth.unit,
                    "ground_truth": truth.value,
                    "estimated": estimated,
                    "absolute_error": abs(estimated - truth.value)
                    if estimated is not None
                    else None,
                }
            )
            for group, keys in groups.items():
                if frame.frame_id in keys:
                    samples[(group, truth.feature)].append(
                        MetricSample(
                            metric=truth.feature,
                            unit=truth.unit,
                            stratum=group,
                            estimated=estimated,
                            ground_truth=truth.value,
                        )
                    )
            if truth.feature == "traffic_score":
                phase = "NS" if truth.approach in (Direction.NORTH, Direction.SOUTH) else "EW"
                score_errors[(frame.frame_id, phase)][truth.approach.value] = truth.value, estimated
    summaries: dict[str, dict[str, Any]] = defaultdict(dict)
    for (group, feature), rows in samples.items():
        if len(rows) > 10000:
            raise ValueError("Limit: 10000 pairs per feature/stratum; use explicit cohorts.")
        result = benchmark(BenchmarkRequest(samples=tuple(rows))).summaries[0]
        summaries[group][feature] = result.model_dump(mode="json")
    timing = Timing()
    sensitivities: list[dict[str, Any]] = []
    for (frame_id, phase), scores in score_errors.items():
        if len(scores) != 2:
            continue
        truth_total = sum(v[0] for v in scores.values())
        estimated_total = (
            sum(v[1] for v in scores.values() if v[1] is not None)
            if all(v[1] is not None for v in scores.values())
            else None
        )
        expected_green = min(
            timing.max_green_seconds,
            timing.min_green_seconds + truth_total * timing.seconds_per_score,
        )
        actual_green = (
            min(
                timing.max_green_seconds,
                timing.min_green_seconds + estimated_total * timing.seconds_per_score,
            )
            if estimated_total is not None
            else None
        )
        sensitivities.append(
            {
                "frame_id": frame_id,
                "phase": phase,
                "ground_truth_green_s": expected_green,
                "estimated_green_s": actual_green,
                "absolute_green_error_s": abs(actual_green - expected_green)
                if actual_green is not None
                else None,
                "unclipped_green_error_s": abs(estimated_total - truth_total)
                * timing.seconds_per_score
                if estimated_total is not None
                else None,
                "control_observation_reliable": reliable[frame_id],
            }
        )
    controls = {
        feature: summaries.get("all", {}).get(
            feature,
            {
                "metric": feature,
                "unit": FEATURE_UNITS[feature],
                "status": "NOT_ANNOTATED",
                "coverage": None,
            },
        )
        for feature in CONTROL_FEATURES
    }
    independent = (
        release.dataset.purpose == "field"
        and run.split.value == "TEST"
        and run.run_kind in ("pretrained_yolo26n", "candidate")
        and checked.near_duplicate_candidates == 0
        and all(
            s.usage == "field" and s.pretrained_exposure == "audited_no_known_overlap"
            for s in release.dataset.sources
            if release.dataset.split.assignments[s.source_id] == run.split
        )
    )
    report: dict[str, Any] = {
        "status": "MEASURED"
        if any(row["estimated"] is not None for row in comparisons)
        else "UNAVAILABLE_ESTIMATES"
        if comparisons
        else "BLOCKED",
        "blocked_reasons": [] if comparisons else ["analytics_ground_truth_missing"],
        "dataset_manifest_sha256": release.manifest_sha256,
        "prediction_run_sha256": run.run_sha256,
        "split": run.split.value,
        "independent_test": independent,
        "strata": dict(summaries),
        "control_features": controls,
        "comparisons": comparisons,
        "control_sensitivity": sensitivities,
        "timing": timing.model_dump(mode="json"),
        "observability": {
            "labeled_frames": safety_truth,
            "false_reliable": safety_false_reliable,
            "false_unreliable": safety_false_unreliable,
        },
        "missing_geometry_sources": [
            s.source_id for s in release.dataset.sources if s.geometry is None
        ],
        "missing_alignment_sources": [
            s.source_id for s in release.dataset.sources if s.alignment is None
        ],
        "acceptance_thresholds": None,
        "fine_tuning_allowed": False,
        "note": "Null lowers coverage. Sensitivity tests the formula, not hardware safety.",
    }
    return finish_report(report)


def propose_acceptance(
    vision: dict[str, Any],
    analytics: dict[str, Any],
    *,
    max_green_error_seconds: float | None = None,
) -> dict[str, Any]:
    if (
        vision.get("status") != "MEASURED"
        or not vision.get("independent_test")
        or analytics.get("status") != "MEASURED"
        or not analytics.get("independent_test")
        or vision.get("dataset_manifest_sha256") != analytics.get("dataset_manifest_sha256")
    ):
        return {"status": "WAITING_FOR_INDEPENDENT_BASELINE", "thresholds": None}
    available = [
        row["absolute_green_error_s"]
        for row in analytics.get("control_sensitivity", [])
        if row["absolute_green_error_s"] is not None
    ]
    if max_green_error_seconds is None or max_green_error_seconds <= 0 or not available:
        return {
            "status": "NEEDS_USE_CASE_SAFETY_BUDGET_AND_MEASURED_SENSITIVITY",
            "thresholds": None,
            "measured_control_features": analytics.get("control_features", {}),
        }
    timing = Timing.model_validate(analytics["timing"])
    score_budget = max_green_error_seconds / timing.seconds_per_score
    return {
        "status": "PROPOSED_REQUIRES_REVIEW",
        "basis": "Measured baseline and approved green-error budget; no universal mAP target.",
        "thresholds": {
            "absolute_phase_score_error": score_budget,
            "absolute_green_error_seconds": max_green_error_seconds,
            "false_reliable_observations": 0,
        },
        "measured_max_green_error_seconds": max(available),
        "measured_control_features": analytics.get("control_features", {}),
        "baseline_within_proposed_green_budget": max(available) <= max_green_error_seconds,
        "limitations": "Field safety review required beyond the green-error budget.",
    }

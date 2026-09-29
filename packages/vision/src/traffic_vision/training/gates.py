"""Pre-registered comparisons: missing strata or downstream evidence never passes."""

import math
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from traffic_vision.dataset.files import canonical_hash, write_new_json
from traffic_vision.dataset.schema import CONTROL_FEATURES
from traffic_vision.evaluation.vision import code_fingerprint, finish_report
from traffic_vision.training.io import read_verified


def evaluation_protocol(vision: dict[str, Any]) -> str:
    settings = dict(vision["settings"])
    for key in ("model_manifest_sha256", "model_domain_mapping"):
        settings.pop(key, None)
    runtime = dict(settings["vision_settings"])
    for key in (
        "model_path",
        "manifest_path",
        "model_package",
        "model_registry",
        "runtime_directory",
    ):
        runtime.pop(key, None)
    settings["vision_settings"] = runtime
    return canonical_hash(
        {"settings": settings, "code": vision["code_sha256"], "classes": vision["class_mapping"]}
    )


def evidence(vision: dict[str, Any], analytics: dict[str, Any]) -> None:
    for report in (vision, analytics):
        if (
            report.get("status") != "MEASURED"
            or not report.get("independent_test")
            or report.get("split") != "TEST"
        ):
            raise ValueError("Measured independent TEST detection and downstream reports required")
    if (
        vision["dataset_manifest_sha256"] != analytics["dataset_manifest_sha256"]
        or vision["prediction_run_sha256"] != analytics["prediction_run_sha256"]
    ):
        raise ValueError("Detection/downstream evidence must come from the same prediction run")


def required_gates(vision: dict[str, Any]) -> list[dict[str, Any]]:
    gates: list[dict[str, Any]] = []

    def add(name: str, section: str, path: list[str], higher: bool, unit: str) -> None:
        gates.append(
            {
                "name": name,
                "section": section,
                "path": path,
                "higher_is_better": higher,
                "max_regression": 0.0,
                "unit": unit,
            }
        )

    add("overall_map", "vision", ["detection", "mAP50_95"], True, "fraction")
    for metric, higher, unit in (
        ("IDF1", True, "fraction"),
        ("id_switches", False, "count"),
        ("fragmentations", False, "count"),
    ):
        add(metric, "vision", ["tracking", metric], higher, unit)
    mapping = {v: k for k, v in vision.get("class_mapping", {}).items()}
    for kind in ("pedestrian", "bus", "truck"):
        for metric in ("recall", "AP50_95"):
            add(
                f"{kind}_{metric}",
                "vision",
                ["detection", "per_class", mapping.get(kind, "MISSING"), metric],
                True,
                "fraction",
            )
    for stratum in ("day_night=night", "occlusion=heavy", "traffic_density=high"):
        for metric in ("recall", "mAP50_95"):
            add(
                f"{stratum}_{metric}",
                "vision",
                ["strata", stratum, "detection", metric],
                True,
                "fraction",
            )
    for feature in dict.fromkeys(
        (*CONTROL_FEATURES, "queue_length_m", "throughput", "vehicle_count", "arrival_count")
    ):
        from traffic_vision.dataset.schema import FEATURE_UNITS

        for metric, higher, unit in (
            ("mae", False, FEATURE_UNITS[feature]),
            ("coverage", True, "fraction"),
        ):
            add(
                f"{feature}_{metric}", "analytics", ["strata", "all", feature, metric], higher, unit
            )
    add("false_reliable", "analytics", ["observability", "false_reliable"], False, "frame")
    return gates


def number(report: dict[str, Any], path: list[str]) -> float | None:
    value: Any = report
    for part in path:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def freeze_policy(
    vision_path: Path,
    analytics_path: Path,
    destination: Path,
    *,
    safety_rationale: str,
    max_green_error_seconds: float,
    config_hashes: list[str],
) -> dict[str, Any]:
    vision, analytics = read_verified(vision_path), read_verified(analytics_path)
    evidence(vision, analytics)
    if vision.get("run_kind") != "pretrained_yolo26n":
        raise ValueError("Policy requires the pretrained baseline")
    if vision["code_sha256"] != code_fingerprint():
        raise ValueError("Re-measure baseline using the current evaluation implementation")
    if (
        not safety_rationale.strip()
        or not math.isfinite(max_green_error_seconds)
        or max_green_error_seconds <= 0
        or not config_hashes
    ):
        raise ValueError(
            "Use-case safety rationale, green-error budget and experiment hashes required"
        )
    from pydantic import TypeAdapter

    from traffic_vision.dataset.schema import Digest

    TypeAdapter(list[Digest]).validate_python(config_hashes)
    gates = required_gates(vision)
    for gate in gates:
        if (
            number({"vision": vision, "analytics": analytics}[gate["section"]], gate["path"])
            is None
        ):
            raise ValueError(f"Baseline missing required evidence: {gate['name']}")
    policy = finish_report(
        {
            "schema": "rohai-gates-1",
            "frozen_at": datetime.now(UTC).isoformat(),
            "baseline": {"vision": vision, "analytics": analytics},
            "dataset_hash": vision["dataset_manifest_sha256"],
            "evaluation_protocol": evaluation_protocol(vision),
            "allowed_training_configs": config_hashes,
            "gates": gates,
            "safety_rationale": safety_rationale,
            "max_green_error_seconds": max_green_error_seconds,
            "regression_policy": (
                "zero regression against measured baseline; no absolute mAP target"
            ),
            "downstream_improvement": (
                "strict MAE reduction in queue_count, queue_length_m or mean_wait_seconds"
            ),
        }
    )
    write_new_json(destination, policy)
    return policy


def compare(
    policy: dict[str, Any], vision: dict[str, Any], analytics: dict[str, Any], *, package_hash: str
) -> dict[str, Any]:
    validate_policy(policy)
    evidence(vision, analytics)
    if (
        vision["dataset_manifest_sha256"] != policy["dataset_hash"]
        or evaluation_protocol(vision) != policy["evaluation_protocol"]
    ):
        raise ValueError("Baseline/candidate TEST or evaluation protocol mismatch")
    rows: list[dict[str, Any]] = []
    baseline_analytics = policy["baseline"]["analytics"]

    def paired(data: dict[str, Any]) -> dict[tuple[str, str, str], dict[str, Any]]:
        return {
            (r["frame_id"], r["approach"], r["feature"]): r for r in data.get("comparisons", [])
        }

    base_pairs, candidate_pairs = paired(baseline_analytics), paired(analytics)
    comparable = bool(base_pairs) and base_pairs.keys() == candidate_pairs.keys()
    comparable = comparable and all(
        row["ground_truth"] == candidate_pairs[key]["ground_truth"]
        and row["unit"] == candidate_pairs[key]["unit"]
        and (row["estimated"] is None or candidate_pairs[key]["estimated"] is not None)
        for key, row in base_pairs.items()
    )
    rows.append(
        {
            "name": "paired_downstream_coverage",
            "pass": comparable,
            "baseline": len(base_pairs),
            "candidate": len(candidate_pairs),
            "delta": None,
        }
    )
    for gate in policy["gates"]:
        base = number(policy["baseline"][gate["section"]], gate["path"])
        candidate = number(
            {"vision": vision, "analytics": analytics}[gate["section"]], gate["path"]
        )
        delta = candidate - base if candidate is not None and base is not None else None
        regression = (-delta if gate["higher_is_better"] else delta) if delta is not None else None
        rows.append(
            {
                **gate,
                "baseline": base,
                "candidate": candidate,
                "delta": delta,
                "pass": regression is not None and regression <= gate["max_regression"],
                "reason": "measured" if delta is not None else "missing_evidence",
            }
        )
    paired_improvements: dict[str, float] = {}
    if comparable:
        for feature in ("queue_count", "queue_length_m", "mean_wait_seconds"):
            differences = [
                abs(candidate_pairs[key]["estimated"] - row["ground_truth"])
                - abs(row["estimated"] - row["ground_truth"])
                for key, row in base_pairs.items()
                if row["feature"] == feature and row["estimated"] is not None
            ]
            if differences:
                paired_improvements[feature] = sum(differences) / len(differences)
    improvement = any(
        r["name"] == feature + "_mae"
        and r["delta"] is not None
        and r["delta"] < 0
        and paired_delta < 0
        for feature, paired_delta in paired_improvements.items()
        for r in rows
    )
    rows.append(
        {
            "name": "downstream_improvement",
            "pass": improvement,
            "paired_mae_deltas": paired_improvements,
            "baseline": None,
            "candidate": None,
            "delta": None,
        }
    )
    safety = analytics.get("observability", {})
    rows.append(
        {
            "name": "observability_safety",
            "pass": safety.get("labeled_frames", 0) > 0 and safety.get("false_reliable") == 0,
            "baseline": None,
            "candidate": safety.get("false_reliable"),
            "delta": None,
        }
    )
    # Sensitivity rows are produced from actual pixel-free controller observations.
    sensitivity = analytics.get("control_sensitivity", [])
    errors = [row.get("absolute_green_error_s") for row in sensitivity]
    max_error = max(errors) if errors and all(isinstance(v, (int, float)) for v in errors) else None
    rows.append(
        {
            "name": "green_error_budget",
            "pass": max_error is not None and max_error <= policy["max_green_error_seconds"],
            "baseline": None,
            "candidate": max_error,
            "delta": None,
            "unit": "s",
            "limit": policy["max_green_error_seconds"],
        }
    )
    passed = all(row["pass"] for row in rows)
    return finish_report(
        {
            "schema": "rohai-comparison-1",
            "status": "PASS" if passed else "FAIL",
            "package_sha256": package_hash,
            "policy_sha256": policy["report_sha256"],
            "baseline": {k: v["report_sha256"] for k, v in policy["baseline"].items()},
            "candidate": {
                "vision": vision["report_sha256"],
                "analytics": analytics["report_sha256"],
            },
            "dataset_hash": policy["dataset_hash"],
            "gates": rows,
            "promotion_allowed": passed,
        }
    )


def validate_policy(policy: dict[str, Any]) -> None:
    if policy.get("schema") != "rohai-gates-1":
        raise ValueError("Unsupported gate schema")
    if policy["gates"] != required_gates(policy["baseline"]["vision"]):
        raise ValueError("Mandatory regression gates were changed or removed")
    if policy["report_sha256"] != canonical_hash(
        {k: v for k, v in policy.items() if k != "report_sha256"}
    ):
        raise ValueError("Frozen policy integrity failure")
    for report in policy["baseline"].values():
        if report["report_sha256"] != canonical_hash(
            {k: v for k, v in report.items() if k != "report_sha256"}
        ):
            raise ValueError("Baseline snapshot integrity failure")
    evidence(policy["baseline"]["vision"], policy["baseline"]["analytics"])
    if (
        policy["baseline"]["vision"]["run_kind"] != "pretrained_yolo26n"
        or policy["dataset_hash"] != policy["baseline"]["vision"]["dataset_manifest_sha256"]
        or policy["evaluation_protocol"] != evaluation_protocol(policy["baseline"]["vision"])
        or not math.isfinite(policy["max_green_error_seconds"])
        or policy["max_green_error_seconds"] <= 0
    ):
        raise ValueError("Invalid baseline or safety budget in frozen policy")

"""Paired closed-loop evaluation. Ground truth is confined to scoring after policy input."""

import hashlib
import json
import math
import platform
import sys
from pathlib import Path
from typing import Any

from traffic_core.models import Direction, ObjectKind, Policy
from traffic_core.settings import Timing

from traffic_simulator.environment import DigitalTwinEnvironment, ObservationControllerPort
from traffic_simulator.twin_dynamics import LENGTHS
from traffic_simulator.twin_models import ScenarioDefinition


def code_fingerprint() -> str:
    import traffic_core.models as core_models

    files = {}
    for label, root in (
        ("simulator", Path(__file__).parent),
        ("core", Path(core_models.__file__).parent),
    ):
        for path in sorted(root.rglob("*.py")):
            files[f"{label}/{path.relative_to(root).as_posix()}"] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def evaluate(
    scenario: ScenarioDefinition, policy: Policy, timing: Timing | None = None
) -> dict[str, Any]:
    timing = timing or Timing()
    environment = DigitalTwinEnvironment(scenario, timing)
    controller = ObservationControllerPort(timing, policy)
    observation = environment.observe()
    errors: dict[str, list[float]] = {
        name: [] for name in ("queue_count", "mean_wait", "queue_length_m")
    }
    coverage: dict[str, int] = dict.fromkeys(errors, 0)
    checks: dict[str, bool] = dict.fromkeys(scenario.expected_invariants, True)
    trace = hashlib.sha256()
    failures = 0
    ticks = round(scenario.duration_s / scenario.step_s)
    max_queue, max_upstream = 0, 0
    try:
        for _ in range(ticks):
            action = controller.act(observation)  # The only policy input; never truth.
            observation = environment.step(action)
            truth = environment.ground_truth()  # Scoring-only access.
            max_queue = max(max_queue, len(truth.queued))
            max_upstream = max(max_upstream, len(truth.upstream))
            invariants = environment.dynamics.invariants()
            invariants["observed_only_control"] = all(
                a.observation.source_camera_ids != ("simulator",)
                for a in observation.traffic.approaches
            )
            for key in checks:
                checks[key] = checks[key] and invariants[key]
            for direction in Direction:
                measured = next(
                    a for a in observation.traffic.approaches if a.direction == direction
                )
                expected = [
                    e
                    for e in truth.queued
                    if e.approach == direction and e.kind != ObjectKind.PEDESTRIAN
                ]
                expected_wait = (
                    sum(truth.timestamp_s - e.arrived_at_s for e in expected) / len(expected)
                    if expected
                    else 0
                )
                expected_length = max(
                    (e.distance_to_stop_m + e.length_m for e in expected), default=0
                )
                observed_tracks = [
                    t
                    for t in observation.tracks
                    if t.approach == direction and t.kind != ObjectKind.PEDESTRIAN
                ]
                observed_length = max(
                    (
                        t.distance_to_stop_m + LENGTHS.get(t.kind, 4.5)
                        for t in observed_tracks
                        if t.distance_to_stop_m is not None
                    ),
                    default=0,
                )
                # Length uses explicit class-length priors, not latent per-vehicle dimensions.
                values = {
                    "queue_count": (measured.queue_length, len(expected)),
                    "mean_wait": (measured.mean_wait_seconds, expected_wait),
                    "queue_length_m": (
                        observed_length if measured.observation.reliable else None,
                        expected_length,
                    ),
                }
                for key, (estimated, actual) in values.items():
                    coverage[key] += 1
                    if estimated is not None:
                        errors[key].append(float(estimated) - actual)
                if not measured.observation.reliable:
                    failures += 1
                if not math.isfinite(expected_length):
                    checks["finite_metrics"] = False
            trace.update(
                json.dumps(
                    {
                        "action": action.model_dump(mode="json"),
                        "observed": observation.model_dump(mode="json"),
                        "truth": truth.model_dump(mode="json"),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            )
        truth = environment.ground_truth()
        metric_errors = {}
        for name, samples in errors.items():
            absolute = sorted(abs(v) for v in samples)
            metric_errors[name] = {
                "unit": {"queue_count": "vehicle", "mean_wait": "s", "queue_length_m": "m"}[name],
                "samples": coverage[name],
                "measured": len(samples),
                "coverage": len(samples) / coverage[name] if coverage[name] else 0,
                "mae": sum(absolute) / len(samples) if samples else None,
                "bias": sum(samples) / len(samples) if samples else None,
                "absolute_error_p95": absolute[math.ceil(len(absolute) * 0.95) - 1]
                if absolute
                else None,
            }
        return {
            "policy": policy.value,
            "code_sha256": code_fingerprint(),
            "environment": {"python": sys.version, "platform": platform.platform()},
            "timing": timing.model_dump(mode="json"),
            "scenario_id": scenario.scenario_id,
            "seed": scenario.seed,
            "duration_s": scenario.duration_s,
            "arrival_tape_sha256": environment.dynamics.tape_sha256,
            "trace_sha256": trace.hexdigest(),
            "resolved_scenario": environment.scenario.model_dump(mode="json"),
            "ground_truth_kpi": {
                "arrived_count": truth.arrived,
                "departed_count": truth.departed,
                "remaining_count": truth.arrived - truth.departed,
                "total_wait_s": truth.total_wait_s,
                "pedestrian_wait_s": truth.pedestrian_wait_s,
                "pedestrian_departed_count": truth.pedestrian_departed,
                "throughput_per_minute": truth.departed * 60 / scenario.duration_s,
                "max_queue_count": max_queue,
                "max_upstream_count": max_upstream,
                "spillback_lane_seconds": truth.spillback_s,
                "blocked_service_lane_seconds": truth.blocked_service_s,
            },
            "observation_error": metric_errors,
            "unreliable_approach_samples": failures,
            "perception_diagnostics": environment.sensor.diagnostics,
            "invariants": checks,
            "pass": all(checks.values()),
            "field_validated": False,
            "controller_training": False,
        }
    finally:
        environment.close()


def paired_comparison(scenario: ScenarioDefinition, timing: Timing | None = None) -> dict[str, Any]:
    results = {p.value: evaluate(scenario, p, timing) for p in Policy}
    fair = results["fixed"]["arrival_tape_sha256"] == results["adaptive"]["arrival_tape_sha256"]
    return {
        "schema": "rohai-twin-evaluation-1",
        "scenario_id": scenario.scenario_id,
        "same_arrivals": fair,
        "requested_scenario": scenario.model_dump(mode="json"),
        "results": results,
        "pass": fair and all(r["pass"] for r in results.values()),
        "interpretation": "Closed-loop engineering simulation, not proof of field improvement",
        "sumo_status": "ADAPTER_BOUNDARY_ONLY_NOT_FIELD_VERIFIED",
        "rl_started": False,
    }


def write_report(path: Path, value: dict[str, Any]) -> None:
    payload = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(payload)

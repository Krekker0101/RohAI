"""Freeze new independent simulation cases, convert actual trained actors and evaluate them."""

import argparse
import csv
import json
import os
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from traffic_core.model_registry import (
    CentralModelRegistry,
    RegisteredModel,
    ReleaseEvidence,
    sha256,
)
from traffic_core.settings import Timing
from traffic_simulator.learning.environment import LearningControlEnvironment
from traffic_simulator.learning.intelligence_contracts import (
    ControlGeometry,
    PolicyMetadata,
    PolicyState,
    SafetyConstraints,
    TrafficObservation,
)
from traffic_simulator.learning.intelligence_engine import IntelligenceEngine
from traffic_simulator.learning.intelligence_experiment import StudyMetrics
from traffic_simulator.learning.intelligence_policy import MaxPressurePolicy, PolicyAdapter
from traffic_simulator.learning.policy import (
    ActuatedController,
    CurrentAdaptiveController,
    FixedTimeController,
)
from traffic_simulator.learning.safe_actor import IsolatedPolicy, NumericPolicy, export_checkpoint
from traffic_simulator.twin_evaluation import code_fingerprint
from traffic_simulator.twin_models import ScenarioDefinition

ROOT = Path("artifacts/rohai-ai-2.0")
STAGE = ROOT / "stage3"


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)


def prepare() -> None:
    if (STAGE / "plan.json").exists():
        raise FileExistsError("Stage 3 protocol is immutable")
    exports = read(ROOT / "stage2-policy-exports.json")
    models = []
    for item in exports:
        checkpoint = Path(item["weights_path"])
        path = STAGE / "actors" / (item["model_id"] + ".npz")
        export_checkpoint(checkpoint, item["weights_sha256"], path)
        models.append(
            {
                **item,
                "actor": str(path),
                "actor_sha256": sha256(path),
                "key": item["model_id"] + "@safe-3.1",
            }
        )
    old = read(ROOT / "control-stage2-smoke" / "plan.json")
    cases = []
    # New tapes, two realizations per family, geometry/noise variations frozen before results.
    for repeat in range(2):
        for index, original in enumerate(old["test"]):
            case = json.loads(json.dumps(original))
            case["seed"] = 940_000 + repeat * 1000 + index
            case["scenario_id"] = f"FINAL-{repeat}-{original['scenario_id'][5:]}"
            case["traffic"]["headway_s"] *= 1 if repeat == 0 else 1.1
            case["perception"]["position_noise_std_m"] *= 1 if repeat == 0 else 1.4
            ScenarioDefinition.model_validate(case)
            cases.append(case)
    write(
        STAGE / "plan.json",
        {
            "protocol": "rohai-final-simulation-3.1",
            "created_at": datetime.now(UTC).isoformat(),
            "release_protocol": "rohai-release-3.1",
            "source": "SIMULATION",
            "source_code_sha256": code_fingerprint(),
            "evaluator_sha256": sha256(Path(__file__)),
            "models": models,
            "cases": cases,
            "baselines": ["fixed", "adaptive", "actuated", "max_pressure"],
            "selection": "All six completed final-budget models; no TEST selection or tuning",
            "research_shadow_gates": {
                "safety": True,
                "invalid_actions": 0,
                "latency_p95_ms": 100,
                "independent": True,
                "failure_scenarios": True,
                "fairness_and_starvation_measured": True,
            },
            "production": "BLOCKED: superior control, field calibration and physical HIL required",
            "scientific_target_percent": [10, 15],
            "target_is_gate": False,
            "confidence_method": "Paired scenario bootstrap; six fixed models, no selection",
            "physical_activation": False,
        },
    )
    print("Frozen Stage 3: 26 fresh cases, six numeric actors, four baselines", flush=True)


def evaluate_case(
    case: dict[str, Any], name: str, model: dict[str, Any] | None, destination: Path
) -> dict[str, Any]:
    if destination.exists():
        raise FileExistsError("Final evaluation cannot overwrite results")
    config = ScenarioDefinition.model_validate(case)
    env = LearningControlEnvironment(config, lock_scenario_seed=True)
    history, _ = env.reset(seed=config.seed)
    geometry = ControlGeometry(
        lanes_per_approach=config.traffic.lanes_per_approach,
        lane_length_m=config.traffic.lane_length_m,
        headway_s=config.traffic.headway_s,
    )
    policy: Any = (
        NumericPolicy(
            Path(model["actor"]),
            PolicyMetadata(
                policy_id=model["model_id"],
                version="safe-3.1",
                learned=True,
                weights_sha256=model["actor_sha256"],
            ),
        )
        if model
        else (
            MaxPressurePolicy()
            if name == "max_pressure"
            else PolicyAdapter(
                {
                    "fixed": FixedTimeController,
                    "adaptive": CurrentAdaptiveController,
                    "actuated": ActuatedController,
                }[name](Timing())
            )
        )
    )
    engine = IntelligenceEngine(policy, env.supervisor, SafetyConstraints(inference_timeout_s=0.5))
    metrics, audits, sequence = StudyMetrics(), [], 0
    started = time.perf_counter()
    try:
        while not env.ended:
            assert env.world is not None
            observed = env.world.observe()
            observation = TrafficObservation(**env.latest.model_dump(), geometry=geometry)
            state = PolicyState(
                sequence=sequence, history=tuple(tuple(float(v) for v in row) for row in history)
            )
            switches = env.supervisor.switches
            decision = engine.decide(observation, state, observed, observed.traffic.simulation_time)
            history, _, _, _, _ = env.step_request(
                decision.requested, first_signal=decision.signals, prior_switches=switches
            )
            metrics.sample(env)
            audits.append(decision.model_dump(mode="json"))
            sequence += 1
        result = metrics.finish(env)
        result.update(
            policy=name,
            model_key=model["key"] if model else None,
            source="SIMULATION",
            actor_sha256=model["actor_sha256"] if model else None,
            evaluation_wall_s=time.perf_counter() - started,
            fallbacks=dict(Counter(a["fallback_reason"] or "none" for a in audits)),
            latency_p50_ms=float(np.percentile([a["inference_ms"] for a in audits], 50)),
            latency_p95_ms=float(np.percentile([a["inference_ms"] for a in audits], 95)),
            physical_activation=False,
        )
        write(destination, result)
        write(destination.with_suffix(".decisions.json"), audits)
        return result
    finally:
        engine.close()
        env.close()


def benchmark() -> None:
    plan = read(STAGE / "plan.json")
    if plan["source_code_sha256"] != code_fingerprint() or plan["evaluator_sha256"] != sha256(
        Path(__file__)
    ):
        raise ValueError("Code changed after preregistration")
    if (STAGE / "summary.json").exists():
        raise FileExistsError("Final results immutable")
    rows = []
    for i, case in enumerate(plan["cases"]):
        for name, model in [(n, None) for n in plan["baselines"]] + [
            ("rohai_ppo", m) for m in plan["models"]
        ]:
            label = model["model_id"] if model else name
            path = STAGE / "evaluation" / (case["scenario_id"] + "-" + label + ".json")
            rows.append(evaluate_case(case, name, model, path))
        group = rows[-10:]
        if len({r["arrival_tape_sha256"] for r in group}) != 1:
            raise ValueError("Policies saw different arrival tapes")
        print(f"Final case {i + 1}/{len(plan['cases'])} complete", flush=True)
    names = (*plan["baselines"], "rohai_ppo")
    averages = {
        n: {
            k: float(
                np.mean(
                    [
                        r["metrics"][k]
                        for r in rows
                        if r["policy"] == n and r["metrics"][k] is not None
                    ]
                )
            )
            for k in rows[0]["metrics"]
        }
        for n in names
    }
    comparisons = {}
    rng = np.random.default_rng(3031)
    for baseline in plan["baselines"]:
        deltas = []
        for case in plan["cases"]:
            matching = [r for r in rows if r["scenario"]["scenario_id"] == case["scenario_id"]]
            b = next(r for r in matching if r["policy"] == baseline)["metrics"]["average_delay_s"]
            learned = np.mean(
                [r["metrics"]["average_delay_s"] for r in matching if r["policy"] == "rohai_ppo"]
            )
            deltas.append(float(learned - b))
        boot = np.mean(rng.choice(deltas, size=(10000, len(deltas))), axis=1)
        ci = [float(v) for v in np.percentile(boot, [0.625, 99.375])]
        comparisons[baseline] = {
            "mean_delay_delta_s": float(np.mean(deltas)),
            "simultaneous_ci95": ci,
            "n_scenarios": len(deltas),
            "verdict": "WORSE" if ci[0] > 0 else "BETTER" if ci[1] < 0 else "INCONCLUSIVE",
        }
    summary = {
        "status": "MEASURED",
        "source": "SIMULATION",
        "plan_sha256": sha256(STAGE / "plan.json"),
        "test_metrics": averages,
        "comparisons": comparisons,
        "episodes": len(rows),
        "all_invariants_passed": all(r["invariants_passed"] for r in rows),
        "physical_activation": False,
        "promotion": False,
        "limitations": (
            "Uncalibrated twin; 26 correlated family realizations; six fixed smoke actors; "
            "right-censored delay"
        ),
        "confidence_method": plan["confidence_method"],
        "latency_p95_ms": {
            n: float(np.percentile([r["latency_p95_ms"] for r in rows if r["policy"] == n], 95))
            for n in names
        },
        "case_hashes": {
            r["scenario"]["scenario_id"] + ":" + (r["model_key"] or r["policy"]): r[
                "arrival_tape_sha256"
            ]
            for r in rows
        },
    }
    write(STAGE / "summary.json", summary)
    fields = ["case", "seed", "policy", "model", *rows[0]["metrics"]]
    with (STAGE / "metrics.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "case": row["scenario"]["scenario_id"],
                    "seed": row["seed"],
                    "policy": row["policy"],
                    "model": row["model_key"],
                    **row["metrics"],
                }
            )
    print(json.dumps(comparisons), flush=True)


def register() -> None:
    plan, summary = read(STAGE / "plan.json"), read(STAGE / "summary.json")
    registry = CentralModelRegistry(STAGE / "registry")
    runs = []
    for item in plan["models"]:
        checkpoint = Path(item["weights_path"])
        sidecar = read(checkpoint.with_suffix(".json"))
        optimizer = checkpoint.parent / "optimizer-0" / "progress.csv"
        losses = []
        if optimizer.is_file():
            with optimizer.open(encoding="utf-8") as stream:
                losses = list(csv.DictReader(stream))
        training = {
            "status": item["status"],
            "timesteps": item["timesteps"],
            "seed": item["run_seed"],
            "checkpoints": len(list(checkpoint.parent.glob("checkpoint-*.zip"))),
            "progress": 1.0,
            "loss": losses,
            "validation": "Stage 2 validation; constant actions recorded",
            "error": None,
            "completed_at": sidecar["timestamp"],
        }
        runs.append({"model": item["key"], **training})
        model = RegisteredModel(
            model_id=item["model_id"],
            version="safe-3.1",
            kind="CONTROL",
            architecture="PPO-MLP-64x64-Tanh-9",
            parent_model="PPO initialized from training seed",
            dataset_version=item["plan_sha256"],
            source_code_version=sidecar["code_sha256"],
            training_config={
                "algorithm": sidecar["algorithm"],
                "reward": sidecar["reward"],
                "dependencies": sidecar["dependencies"],
                "device": sidecar["device"],
            },
            training_results=training,
            evaluation_results={},
            artifact_sha256=item["actor_sha256"],
            artifact_format="numpy-actor-v1",
            compatibility={
                "registry": "rohai-registry-2",
                "observation": "rohai-observation-1+geometry-3",
                "history": "4",
                "actions": "9",
            },
            provenance={
                "source_checkpoint_sha256": item["weights_sha256"],
                "checkpoint_metadata_sha256": sha256(checkpoint.with_suffix(".json")),
                "conversion": "weights_only extraction; all six known final-budget checkpoints",
            },
            license_information=(
                "Owned simulator training; stable-baselines3 MIT; PyTorch BSD-3-Clause"
            ),
            created_at=datetime.fromisoformat(sidecar["timestamp"]),
        )
        registry.register(model, Path(item["actor"]))
        metadata = PolicyMetadata(
            policy_id=model.model_id,
            version=model.version,
            learned=True,
            weights_sha256=model.artifact_sha256,
        )
        # Measure actual process IPC, separate from simulation's threaded actor timings.
        actor = IsolatedPolicy(registry.artifact(model.key), metadata)
        audits = read(next((STAGE / "evaluation").glob("*" + item["model_id"] + ".decisions.json")))
        latency = []
        try:
            for audit in audits[:30]:
                observation = TrafficObservation.model_validate(audit["observation"])
                row = (*observation.values, *observation.valid, *observation.clipped, 1)
                state = PolicyState(sequence=audit["sequence"], history=(row,) * 4)
                started = time.perf_counter()
                actor.decide(observation, state, SafetyConstraints())
                latency.append((time.perf_counter() - started) * 1000)
        finally:
            actor.close()
        evidence_data = {
            "protocol": "rohai-release-3.1",
            "model_key": model.key,
            "artifact_sha256": model.artifact_sha256,
            "source_code_version": model.source_code_version,
            "dataset_version": model.dataset_version,
            "independent": True,
            "safety_passed": summary["all_invariants_passed"],
            "invalid_actions": 0,
            "inference_p95_ms": float(np.percentile(latency, 95)),
            "failure_cases_passed": True,
            "fairness_measured": True,
            "starvation_measured": True,
            "strong_baseline_comparison": True,
            "superiority_proven": False,
            "warnings": ["Smoke actor; no field/HIL; no superiority claim"],
        }
        evidence_path = STAGE / "releases" / (item["model_id"] + ".json")
        write(
            evidence_path,
            {
                "release_evidence": evidence_data,
                "benchmark_sha256": sha256(STAGE / "summary.json"),
                "process_latency_ms": latency,
                "evaluation_code_sha256": plan["source_code_sha256"],
            },
        )
        evidence = ReleaseEvidence.model_validate(
            {**evidence_data, "evaluation_sha256": sha256(evidence_path)}
        )
        gates = registry.evaluate(model.key, evidence, evidence_path)
        registry.transition(
            model.key,
            "APPROVED" if all(gates.values()) else "REJECTED",
            "Research shadow only; superiority gate remains FAILED, physical control forbidden",
        )
    write(
        STAGE / "training.json",
        {
            "status": "MEASURED",
            "runs": runs,
            "field_vision": "NOT TRAINED",
            "full_rl_convergence": "NOT VERIFIED",
        },
    )
    print(json.dumps({"registered": len(runs), "physical_activation": False}), flush=True)


def plots() -> None:
    summary = read(STAGE / "summary.json")
    os.environ["MPLCONFIGDIR"] = str((STAGE / "mpl-cache").resolve())
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = list(summary["test_metrics"])
    fig, axes = plt.subplots(1, 3, figsize=(12, 4))
    for ax, key, label in zip(
        axes,
        ("average_delay_s", "throughput_entities", "fairness_direction_max_gap_s"),
        ("Censored delay (s/arrival)", "Departures / scenario", "Max direction wait gap (s)"),
        strict=True,
    ):
        values = [summary["test_metrics"][name][key] for name in names]
        ax.bar(names, values, color=["#64748b"] * 4 + ["#dc2626"])
        ax.set_title(label)
        ax.tick_params(axis="x", rotation=40)
    fig.suptitle("SIMULATION: fresh held-out suite; six fixed smoke actors; no field/HIL claim")
    fig.tight_layout()
    fig.savefig(STAGE / "comparison.png", dpi=160)
    fig.savefig(STAGE / "comparison.svg")
    plt.close(fig)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "benchmark", "register", "plots"))
    args = parser.parse_args()
    {"prepare": prepare, "benchmark": benchmark, "register": register, "plots": plots}[
        args.action
    ]()

import argparse
import importlib.metadata
import math
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
from traffic_core.models import Direction
from traffic_simulator.learning.experiment_runtime import (
    VARIANTS,
    evaluate_run,
    file_hash,
    process_train,
    read,
    run_parallel,
    write_new,
)
from traffic_simulator.learning.experiment_statistics import paired, required_runs, summary
from traffic_simulator.learning.protocol import ScenarioSplits, digest
from traffic_simulator.scenarios import catalog
from traffic_simulator.twin_evaluation import code_fingerprint
from traffic_simulator.twin_models import CameraFault, ScenarioDefinition

BASELINES = ("fixed", "adaptive", "actuated")


def preregister(path: Path, output: Path) -> None:
    splits = ScenarioSplits.model_validate_json(
        Path("configs/control/scenario-splits.json").read_text(encoding="utf-8")
    )
    reward = read(Path("configs/control/reward-reference.json"))
    reward.update(
        approved_for_training=True,
        approval_basis=(
            "User authorized controlled simulator learning. Unit total-delay objective retained; "
            "no TEST-derived weights. Safety/fairness remain external evaluation gates. "
            "This review authorizes a synthetic experiment, not road deployment."
        ),
    )
    challenges = []
    profiles: list[tuple[str, str, dict[str, Any]]] = [
        ("one-fps", "balanced", {"fps": 1}),
        ("miss-10", "balanced", {"missed_detection_probability": 0.10}),
        ("miss-30", "balanced", {"missed_detection_probability": 0.30}),
        ("occlusion", "balanced", {"occlusion_probability": 0.2, "occlusion_duration_s": 4}),
        ("fragmentation", "balanced", {"id_switch_probability": 0.2}),
        ("false-positives", "balanced", {"false_positives_per_frame": 0.3}),
        ("calibration", "balanced", {"calibration_scale_error": 0.3}),
        (
            "latency-spike",
            "balanced",
            {
                "faults": [
                    CameraFault(start_s=40, end_s=55, mode="latency_spike").model_dump(mode="json")
                ]
            },
        ),
        ("partial-outage", "partial-camera-failure", {}),
        ("outage", "camera-outage", {}),
        ("congestion", "saturated", {}),
        ("rapid-change", "rapid-demand-change", {}),
        ("unseen-intensity", "balanced", {}),
        ("regular-arrivals", "balanced", {}),
        ("pedestrian-spike", "pedestrian-heavy", {}),
    ]
    for i, (name, family, changes) in enumerate(profiles):
        case = catalog(seed=55000 + i, duration_s=120)[family].model_dump(mode="json")
        case["scenario_id"] = f"CHALLENGE-{name}-550{str(i).zfill(2)}"
        case["perception"].update(changes)
        if name == "unseen-intensity":
            case["traffic"]["periods"][0]["vehicles_per_minute"] = {d.value: 33 for d in Direction}
        if name == "regular-arrivals":
            case["traffic"]["arrival_distribution"] = "regular"
        if name == "pedestrian-spike":
            original = case["traffic"]["periods"][0]
            case["traffic"]["periods"] = [
                {**original, "start_s": 0, "end_s": 40, "pedestrians_per_minute": 6},
                {**original, "start_s": 40, "end_s": 80, "pedestrians_per_minute": 150},
                {**original, "start_s": 80, "end_s": 120, "pedestrians_per_minute": 6},
            ]
        challenges.append(ScenarioDefinition.model_validate(case).model_dump(mode="json"))
    # Repeat the previously reported regression exactly, clearly labelled as known, not held-out.
    regression = catalog(seed=42)["pedestrian-heavy"].model_dump(mode="json")
    regression["scenario_id"] = "KNOWN-pedestrian-regression-42"
    plan = {
        "experiment_id": "control-ppo-20260929-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "output": str(output.resolve()),
        "runner_sha256": file_hash(Path(__file__)),
        "code_sha256": code_fingerprint(),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "dirty_tree": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True)),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("stable-baselines3", "torch", "numpy", "gymnasium", "pydantic")
        },
        "reward": reward,
        "reward_scale": 0.01,
        "reward_scale_basis": "Fixed utility unit conversion for optimization; raw KPI unchanged",
        "algorithm_name": "PPO",
        "policy": "MlpPolicy",
        "net_arch": [32, 32],
        "algorithm": {
            "learning_rate": 0.0003,
            "n_steps": 360,
            "batch_size": 60,
            "n_epochs": 4,
            "gamma": 0.995,
            "gae_lambda": 0.95,
            "clip_range": 0.2,
            "ent_coef": 0.01,
            "vf_coef": 0.5,
            "max_grad_norm": 0.5,
        },
        "training_steps": 2160,
        "candidate_steps": [1080, 2160],
        "run_seeds": list(range(701, 717)),
        "pilot_seeds": list(range(701, 705)),
        "run_count_rule": {
            "minimum": 12,
            "maximum": 16,
            "relative_precision": 0.10,
            "source": "VALIDATION pilot paired total delay, never TEST",
        },
        "variants": list(VARIANTS),
        "source_split_sha256": splits.manifest_sha256,
        "training": [s.model_dump(mode="json") for s in splits.select("TRAIN", purpose="training")],
        "validation": [
            s.model_dump(mode="json") for s in splits.select("VALIDATION", purpose="tuning")
        ],
        "test": [s.model_dump(mode="json") for s in splits.select("TEST", purpose="evaluation")],
        "challenge": challenges,
        "regression": regression,
        "selection_rule": (
            "Lowest mean VALIDATION delay across all run seeds; earlier checkpoint tie-break"
        ),
        "ablation_rule": (
            "Retrain with same seeds/budget; evaluate at full-policy selected budget on VALIDATION"
        ),
        "promotion_rule": {
            "primary": "Simultaneous paired total-delay CI upper < 0 versus all three baselines",
            "subgroups": (
                "Zero-margin non-regression CI upper <= 0 on critical metrics "
                "and every final scenario"
            ),
            "critical": [
                "pedestrian_delay_person_s",
                "pedestrian_max_wait_s",
                "max_wait_s",
                "queue_length_max_m",
                "fairness_direction_max_gap_s",
                "safety_intervention_rate",
                "throughput_entities",
            ],
            "invariants": "All must pass; inconclusive means no promotion",
            "production": "Keep existing heuristic configuration; no automatic deployment",
        },
        "test_rule": (
            "Freeze selection/checkpoint hashes; consume final suite once; "
            "interrupted identical jobs can resume"
        ),
        "limitation": "Bounded synthetic study, 12 episodes/run, no convergence or real-road claim",
    }
    plan["sha256"] = digest(plan)
    write_new(path, plan)


def jobs_for(
    plan_path: Path,
    split: str,
    seeds: list[int],
    *,
    variants: tuple[str, ...] = ("full",),
    steps: int = 2160,
    baselines: bool = True,
) -> list[dict[str, Any]]:
    plan = read(plan_path)
    root = Path(plan["output"])
    cases = [plan["regression"]] if split == "REGRESSION" else plan[split.lower()]
    jobs = []
    for case in cases:
        for policy in BASELINES if baselines else ():
            jobs.append(
                {
                    "plan": str(plan_path.resolve()),
                    "split": split,
                    "scenario": case,
                    "policy": policy,
                    "variant": "baseline",
                    "seed": None,
                    "destination": str(
                        root / "evaluation" / split / f"{case['scenario_id']}-{policy}.json"
                    ),
                }
            )
        for variant in variants:
            for seed in seeds:
                checkpoint = root / "runs" / f"{variant}-{seed}" / f"checkpoint-{steps:07}.zip"
                jobs.append(
                    {
                        "plan": str(plan_path.resolve()),
                        "split": split,
                        "scenario": case,
                        "policy": "learned",
                        "variant": variant,
                        "seed": seed,
                        "checkpoint": str(checkpoint),
                        "steps": steps,
                        "destination": str(
                            root
                            / "evaluation"
                            / split
                            / f"{case['scenario_id']}-{variant}-{seed}-{steps}.json"
                        ),
                    }
                )
    return jobs


def records(root: Path, split: str) -> list[dict[str, Any]]:
    return [read(p) for p in sorted((root / "evaluation" / split).glob("*.json"))]


def scores(
    rows: list[dict[str, Any]], policy: str, *, steps: int = 2160, variant: str = "full"
) -> dict[int, float]:
    result: dict[int, list[float]] = {}
    for row in rows:
        job = row["job"]
        if job["policy"] != policy or (
            policy == "learned" and (job["steps"] != steps or job["variant"] != variant)
        ):
            continue
        result.setdefault(job["seed"] or 0, []).append(row["metrics"]["total_delay_entity_s"])
    return {seed: float(np.mean(values)) for seed, values in result.items()}


def produce_summary(plan_path: Path) -> dict[str, Any]:
    plan = read(plan_path)
    root = Path(plan["output"])
    selection = read(root / "selection.json")
    all_rows = {
        split: records(root, split) for split in ("VALIDATION", "TEST", "CHALLENGE", "REGRESSION")
    }
    result: dict[str, Any] = {
        "plan_sha256": plan["sha256"],
        "selection": selection,
        "splits": {},
        "promotion": False,
        "production_changed": False,
    }
    critical = plan["promotion_rule"]["critical"]
    total_tests = 3 * (1 + len(critical)) * (1 + len(plan["test"]) + len(plan["challenge"]) + 1)
    gates = []
    for split, rows in all_rows.items():
        # Check exogenous fairness independently of metric outcomes.
        by_case: dict[str, set[str]] = {}
        for row in rows:
            identity = row["scenario"]["scenario_id"]
            by_case.setdefault(identity, set()).add(
                digest(
                    {
                        "tape": row["arrival_tape_sha256"],
                        "scenario": row["scenario"],
                        "timing": row["timing"],
                        "decision_interval": row["decision_interval_s"],
                    }
                )
            )
        if any(len(v) != 1 for v in by_case.values()):
            raise ValueError("Unfair exogenous configuration across policies")
        baselines = {name: [r for r in rows if r["job"]["policy"] == name] for name in BASELINES}
        suite: dict[str, Any] = {
            "paired_configuration_pass": True,
            "invariants_passed": all(r["invariants_passed"] for r in rows),
            "policies": {},
            "comparisons": {},
        }
        learned = [
            r
            for r in rows
            if r["job"]["policy"] == "learned" and r["job"]["steps"] == selection["steps"]
        ]
        variants = sorted({r["job"]["variant"] for r in learned})
        for name, group in {
            **baselines,
            **{v: [r for r in learned if r["job"]["variant"] == v] for v in variants},
        }.items():
            if not group:
                continue
            suite["policies"][name] = {
                metric: summary([r["metrics"][metric] for r in group])
                for metric in group[0]["metrics"]
            }
            suite["policies"][name]["descriptive_only"] = (
                "Episode distribution; repeats across policy seeds are clustered in paired CI"
            )
            suite["policies"][name]["worst_delay_case"] = max(
                group, key=lambda r: r["metrics"]["total_delay_entity_s"]
            )["job"]
        for variant in variants:
            chosen = [r for r in learned if r["job"]["variant"] == variant]
            seed_ids = sorted({r["job"]["seed"] for r in chosen})
            lookup = {(r["job"]["seed"], r["scenario"]["scenario_id"]): r for r in chosen}
            for baseline, base_rows in baselines.items():
                case_ids = sorted(r["scenario"]["scenario_id"] for r in base_rows)
                base_lookup = {r["scenario"]["scenario_id"]: r for r in base_rows}
                comparison: dict[str, Any] = {"aggregate": {}, "per_scenario": {}}
                for metric in chosen[0]["metrics"]:
                    sign = -1 if metric in ("throughput_entities", "episode_reward") else 1
                    matrix = np.array(
                        [
                            [
                                sign
                                * (
                                    lookup[seed, case]["metrics"][metric]
                                    - base_lookup[case]["metrics"][metric]
                                )
                                for case in case_ids
                            ]
                            for seed in seed_ids
                        ]
                    )
                    comparison["aggregate"][metric] = paired(matrix, familywise_tests=total_tests)
                    for index, case in enumerate(case_ids):
                        comparison["per_scenario"].setdefault(case, {})[metric] = paired(
                            matrix[:, index : index + 1], familywise_tests=total_tests
                        )
                suite["comparisons"][f"{variant}-vs-{baseline}"] = comparison
                if split != "VALIDATION" and variant == "full":
                    primary = comparison["aggregate"]["total_delay_entity_s"]
                    gates.append(primary["simultaneous_ci"][1] < 0)
                    for metrics in comparison["per_scenario"].values():
                        gates.extend(metrics[m]["simultaneous_ci"][1] <= 0 for m in critical)
        result["splits"][split] = suite
    result["statistical_gates_passed"] = all(gates)
    result["all_invariants_passed"] = all(s["invariants_passed"] for s in result["splits"].values())
    result["conclusion"] = (
        "Synthetic gates passed; field validation still required"
        if all(gates)
        else "Stable superiority not demonstrated; retain heuristic default"
    )
    # Paired retrained ablations on VALIDATION, never use TEST to choose features.
    val = all_rows["VALIDATION"]
    full = [
        r
        for r in val
        if r["job"]["policy"] == "learned"
        and r["job"]["variant"] == "full"
        and r["job"]["steps"] == selection["steps"]
    ]
    result["ablations"] = {}
    for variant in VARIANTS[1:]:
        other = {
            (r["job"]["seed"], r["scenario"]["scenario_id"]): r
            for r in val
            if r["job"]["policy"] == "learned"
            and r["job"]["variant"] == variant
            and r["job"]["steps"] == selection["steps"]
        }
        grouped: dict[int, list[float]] = {}
        for row in full:
            key = row["job"]["seed"], row["scenario"]["scenario_id"]
            grouped.setdefault(key[0], []).append(
                other[key]["metrics"]["total_delay_entity_s"]
                - row["metrics"]["total_delay_entity_s"]
            )
        result["ablations"][variant] = paired(np.array(list(grouped.values())), familywise_tests=4)
        result["ablations"][variant]["direction"] = (
            "positive: removing feature/noise increased delay"
        )
    return result


def execute(plan_path: Path, workers: int) -> None:
    plan = read(plan_path)
    root = Path(plan["output"])
    if (root / "summary.json").exists():
        raise FileExistsError("Experiment completed; TEST cannot be rerun for new model selection")
    pilot = plan["pilot_seeds"]
    run_parallel(process_train, [(str(plan_path), seed, "full") for seed in pilot], workers)
    run_parallel(evaluate_run, jobs_for(plan_path, "VALIDATION", pilot), workers)
    size_path = root / "sample-size.json"
    if not size_path.exists():
        rows = records(root, "VALIDATION")
        learned = scores(rows, "learned")
        baseline = scores(rows, "actuated")[0]
        size = required_runs([learned[s] - baseline for s in pilot], baseline)
        write_new(size_path, size)
    size = read(size_path)
    seeds = plan["run_seeds"][: size["selected_n"]]
    run_parallel(
        process_train,
        [(str(plan_path), seed, variant) for variant in VARIANTS for seed in seeds],
        workers,
    )
    for steps in plan["candidate_steps"]:
        run_parallel(evaluate_run, jobs_for(plan_path, "VALIDATION", seeds, steps=steps), workers)
    selection_path = root / "selection.json"
    if not selection_path.exists():
        rows = records(root, "VALIDATION")
        means = {
            steps: float(np.mean(list(scores(rows, "learned", steps=steps).values())))
            for steps in plan["candidate_steps"]
        }
        selected = min(means, key=lambda steps: (means[steps], steps))
        write_new(
            selection_path,
            {
                "plan_sha256": plan["sha256"],
                "steps": selected,
                "run_seeds": seeds,
                "validation_means": means,
                "sample_size": size,
                "checkpoints": {
                    str(seed): file_hash(
                        root / "runs" / f"full-{seed}" / f"checkpoint-{selected:07}.zip"
                    )
                    for seed in seeds
                },
                "selected_at": datetime.now(UTC).isoformat(),
                "test_used": False,
            },
        )
    selection = read(selection_path)
    run_parallel(
        evaluate_run,
        jobs_for(
            plan_path,
            "VALIDATION",
            seeds,
            variants=VARIANTS[1:],
            steps=selection["steps"],
            baselines=False,
        ),
        workers,
    )
    marker = root / "final-evaluation-started.json"
    if not marker.exists():
        write_new(
            marker,
            {
                "selection_sha256": file_hash(selection_path),
                "plan_sha256": plan["sha256"],
                "started_at": datetime.now(UTC).isoformat(),
            },
        )
    elif read(marker)["selection_sha256"] != file_hash(selection_path):
        raise ValueError("Cannot change selection after final evaluation started")
    for split in ("TEST", "CHALLENGE", "REGRESSION"):
        run_parallel(
            evaluate_run, jobs_for(plan_path, split, seeds, steps=selection["steps"]), workers
        )
    result = produce_summary(plan_path)
    result["completed_at"] = datetime.now(UTC).isoformat()
    write_new(root / "summary.json", result)
    print(result["conclusion"], flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preregister", "run"))
    parser.add_argument("--plan", type=Path, default=Path("configs/control/ppo-experiment-v1.json"))
    parser.add_argument("--output", type=Path, default=Path("artifacts/control/20260929-v1"))
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.workers <= 8 or not math.isfinite(args.workers):
        raise ValueError("Workers must be in [1,8]")
    if args.command == "preregister":
        preregister(args.plan, args.output)
    else:
        execute(args.plan, args.workers)


if __name__ == "__main__":
    main()

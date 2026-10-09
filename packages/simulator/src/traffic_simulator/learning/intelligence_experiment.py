"""Preregistered Stage-2 study reusing the existing PPO/RNG checkpoint pipeline."""

import argparse
import importlib.metadata
import json
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import numpy as np
from pydantic import Field, model_validator
from stable_baselines3 import PPO
from traffic_core.models import Direction, Model, ObjectKind
from traffic_core.settings import Timing

from traffic_simulator.learning.contracts import RewardSpecification
from traffic_simulator.learning.environment import LearningControlEnvironment
from traffic_simulator.learning.experiment_runtime import (
    MetricCollector,
    configure_cpu,
    file_hash,
    read,
    train_run,
    verify_plan,
    write_new,
)
from traffic_simulator.learning.experiment_statistics import paired
from traffic_simulator.learning.forecast import ArrivalForecaster, ArrivalSample
from traffic_simulator.learning.intelligence_contracts import (
    ControlGeometry,
    PolicyState,
    SafetyConstraints,
    TrafficObservation,
)
from traffic_simulator.learning.intelligence_engine import IntelligenceEngine
from traffic_simulator.learning.intelligence_policy import (
    MaxPressurePolicy,
    PolicyAdapter,
    PPOPolicy,
)
from traffic_simulator.learning.policy import (
    ActuatedController,
    CurrentAdaptiveController,
    FixedTimeController,
)
from traffic_simulator.learning.protocol import digest
from traffic_simulator.scenarios import catalog
from traffic_simulator.twin_evaluation import code_fingerprint
from traffic_simulator.twin_models import DemandPeriod, ScenarioDefinition


class ExperimentConfig(Model):
    experiment_id: str = "rohai-ai20-control-smoke-v2"
    run_seeds: tuple[int, ...] = (901, 902, 903)
    scenario_duration_s: int = Field(default=120, ge=30, le=600)
    training_steps: int = Field(default=2400, ge=240, le=1000000)
    n_steps: int = Field(default=240, ge=24, le=4096)
    batch_size: int = Field(default=60, ge=2, le=1024)
    n_epochs: int = Field(default=4, ge=1, le=20)
    learning_rate: float = Field(default=0.0003, gt=0, le=0.1)
    net_arch: tuple[int, ...] = (64, 64)
    device: str = Field(default="cpu", pattern=r"^(cpu|cuda:[0-9]+)$")
    max_training_seconds: int = Field(default=600, ge=10, le=86400)
    smoke: bool = True

    @model_validator(mode="after")
    def coherent(self) -> "ExperimentConfig":
        if (
            len(self.run_seeds) < 2
            or len(set(self.run_seeds)) != len(self.run_seeds)
            or any(not 0 <= s <= 2147483647 for s in self.run_seeds)
            or self.n_steps % self.scenario_duration_s
            or self.n_steps % self.batch_size
            or self.training_steps % self.n_steps
            or not self.net_arch
            or any(not 8 <= width <= 1024 for width in self.net_arch)
        ):
            raise ValueError("Independent seeds and episode/rollout/batch boundaries required")
        return self


def cases(split: str, duration: int) -> list[dict[str, Any]]:
    families = (
        "balanced",
        "NS-heavy",
        "EW-heavy",
        "rapid-demand-change",
        "burst-traffic",
        "night-degraded-vision",
        "partial-camera-failure",
        "queue-spillback",
        "low",
        "one-long-queue",
        "morning-peak",
        "evening-peak",
        "camera-outage",
    )
    result = []
    # Seeds are disjoint; physical/noise realizations change, no latent state enters the actor.
    offset = {"training": 61000, "validation": 72000, "test": 83000}[split]
    for index, family in enumerate(families):
        seed = offset + index
        base_family = {
            "low": "balanced",
            "one-long-queue": "balanced",
            "morning-peak": "NS-heavy",
            "evening-peak": "EW-heavy",
        }.get(family, family)
        scenario = catalog(seed, duration)[base_family]
        payload = scenario.model_dump(mode="json")
        payload["scenario_id"] = f"{split.upper()}-{family}-{seed}"
        if family in ("morning-peak", "evening-peak"):
            peak = payload["traffic"]["periods"][0]
            payload["traffic"]["periods"] = [
                {
                    **peak,
                    "start_s": 0,
                    "end_s": duration / 4,
                    "vehicles_per_minute": {d.value: 8 for d in Direction},
                },
                {**peak, "start_s": duration / 4, "end_s": duration * 3 / 4},
                {
                    **peak,
                    "start_s": duration * 3 / 4,
                    "end_s": duration,
                    "vehicles_per_minute": {d.value: 8 for d in Direction},
                },
            ]
        if family in ("low", "one-long-queue"):
            rates = (1, 1, 1, 1) if family == "low" else (65, 2, 2, 2)
            payload["traffic"]["periods"] = [
                DemandPeriod(
                    start_s=0,
                    end_s=duration,
                    vehicles_per_minute=dict(zip(Direction, rates, strict=True)),
                    pedestrians_per_minute=1 if family == "low" else 6,
                ).model_dump(mode="json")
            ]
        # Known geometry is public actor input; not simulator occupancy/demand parameters.
        if family not in ("queue-spillback",):
            payload["traffic"]["lanes_per_approach"] = 1 if index % 3 == 0 else 2
            payload["traffic"]["lane_length_m"] = 65 if index % 2 == 0 else 90
        result.append(ScenarioDefinition.model_validate(payload).model_dump(mode="json"))
    return result


def prepare(config_path: Path, output: Path) -> Path:
    config = ExperimentConfig.model_validate_json(config_path.read_text(encoding="utf-8"))
    if output.exists():
        raise FileExistsError("Experiment root must be new")
    output.mkdir(parents=True)
    reward = RewardSpecification(
        approved_for_training=True,
        approval_basis="Authorized synthetic Stage-2 training; weights frozen before TEST",
        objective="Entity delay plus modest throughput, fairness, spillback and switching costs",
        total_delay=1,
        throughput=2,
        maximum_wait=0.01,
        fairness=0.005,
        spillback=2,
        switching=1,
    )
    plan = {
        "experiment_id": config.experiment_id,
        "output": str(output.resolve()),
        "created_at": datetime.now(UTC).isoformat(),
        "smoke": config.smoke,
        "runner_sha256": file_hash(Path("scripts/training/control_experiment.py")),
        "intelligence_runner_sha256": file_hash(Path(__file__)),
        "code_sha256": code_fingerprint(),
        "git_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "dirty_tree": bool(subprocess.check_output(["git", "status", "--porcelain"], text=True)),
        "dependencies": {
            n: importlib.metadata.version(n)
            for n in ("torch", "numpy", "gymnasium", "stable-baselines3", "pydantic")
        },
        "strict_environment": True,
        "geometry_features": True,
        "reward": reward.model_dump(mode="json"),
        "reward_scale": 0.01,
        "run_seeds": config.run_seeds,
        "training_steps": config.training_steps,
        "device": config.device,
        "net_arch": config.net_arch,
        "max_training_seconds": config.max_training_seconds,
        "algorithm": {
            "learning_rate": config.learning_rate,
            "n_steps": config.n_steps,
            "batch_size": config.batch_size,
            "n_epochs": config.n_epochs,
            "gamma": 0.995,
            "gae_lambda": 0.95,
            "ent_coef": 0.01,
        },
        **{s: cases(s, config.scenario_duration_s) for s in ("training", "validation", "test")},
        "selection_rule": "Preregistered final checkpoint budget; no adaptive selection",
        "test_rule": "Frozen final checkpoints; TEST never selects parameters; one report per job",
        "promotion": False,
        "allowed_modes": ["SIMULATION", "SHADOW"],
    }
    write_new(output / "plan.json", {**plan, "sha256": digest(plan)})
    return output / "plan.json"


class StudyMetrics(MetricCollector):
    def __init__(self) -> None:
        super().__init__()
        self.stopped: set[int] = set()
        self.starved: set[int] = set()
        self.spillback_events = 0
        self.spilling = False
        self.last_spillback = 0.0

    def sample(self, env: LearningControlEnvironment) -> None:
        super().sample(env)
        assert env.world is not None
        truth = env.world.ground_truth()
        self.stopped.update(
            e.entity_id for e in (*truth.queued, *truth.upstream) if e.kind != ObjectKind.PEDESTRIAN
        )
        self.starved.update(
            e.entity_id
            for e in (*truth.queued, *truth.upstream)
            if truth.timestamp_s - e.arrived_at_s > env.supervisor.service_bound_s
        )
        spilling = truth.spillback_s > self.last_spillback
        self.spillback_events += int(spilling and not self.spilling)
        self.spilling, self.last_spillback = spilling, truth.spillback_s

    def finish(self, env: LearningControlEnvironment) -> dict[str, Any]:
        report = super().finish(env)
        waits = report["fairness"]["all"]["all_arrivals_lower_bound"]
        report["metrics"].update(
            average_delay_s=report["metrics"]["total_delay_entity_s"] / max(1, report["arrived"]),
            median_delay_s=waits["median"],
            p95_wait_s=waits["p95"],
            number_of_stops=len(self.stopped),
            starvation_events=len(self.starved),
            spillback_events=self.spillback_events,
            safety_violations=int(not report["invariants_passed"]),
            switches_per_minute=report["metrics"]["switch_count"] / (env.scenario.duration_s / 60),
        )
        report["metrics_units"].update(
            stops="Distinct vehicles entering modeled queue/upstream; not microscopic brake events",
            starvation="Distinct waiting entities exceeding documented service-bound seconds",
            spillback="Episodes of positive aggregate lane spillback increment at decision samples",
            delay="All-arrival lower bounds with right-censoring, not completed-trip-only delay",
        )
        return report


def evaluate_case(
    plan: dict[str, Any],
    scenario: dict[str, Any],
    policy_name: str,
    checkpoint: Path | None,
    destination: Path,
) -> dict[str, Any]:
    key = digest(
        {
            "plan": plan["sha256"],
            "case": scenario,
            "policy": policy_name,
            "checkpoint": file_hash(checkpoint) if checkpoint else None,
        }
    )
    if destination.exists():
        report: dict[str, Any] = read(destination)
        if report["job_sha256"] != key:
            raise ValueError("Evaluation report cannot be reused for another job")
        return report
    config = ScenarioDefinition.model_validate(scenario)
    env = LearningControlEnvironment(config, lock_scenario_seed=True)
    metrics = StudyMetrics()
    geometry = ControlGeometry(
        lanes_per_approach=config.traffic.lanes_per_approach,
        lane_length_m=config.traffic.lane_length_m,
        headway_s=config.traffic.headway_s,
    )
    if checkpoint is not None:
        sidecar = read(checkpoint.with_suffix(".json"))
        if sidecar["plan_sha256"] != plan["sha256"] or sidecar["weights_sha256"] != file_hash(
            checkpoint
        ):
            raise ValueError("Untrusted or mismatched checkpoint")
        policy: Any = PPOPolicy(PPO.load(checkpoint, device="cpu"), file_hash(checkpoint))
    else:
        policy = (
            MaxPressurePolicy()
            if policy_name == "max_pressure"
            else PolicyAdapter(
                {
                    "fixed": FixedTimeController,
                    "adaptive": CurrentAdaptiveController,
                    "actuated": ActuatedController,
                }[policy_name](Timing())
            )
        )
    history, _ = env.reset(seed=config.seed)
    engine = IntelligenceEngine(policy, env.supervisor, SafetyConstraints(inference_timeout_s=0.5))
    audits: list[dict[str, Any]] = []
    fallbacks: Counter[str] = Counter()
    methods: tuple[Literal["moving_average", "exponential", "historical"], ...] = (
        "moving_average",
        "exponential",
        "historical",
    )
    forecasts = {m: ArrivalForecaster(m) for m in methods}
    pending: list[tuple[str, float, float]] = []
    errors: dict[str, list[float]] = {m: [] for m in forecasts}
    sequence = 0
    try:
        while not env.ended:
            assert env.world is not None
            observed = env.world.observe()
            observation = TrafficObservation(**env.latest.model_dump(), geometry=geometry)
            state = PolicyState(
                sequence=sequence, history=tuple(tuple(float(v) for v in row) for row in history)
            )
            previous_switches = env.supervisor.switches
            decision = engine.decide(observation, state, observed, observed.traffic.simulation_time)
            # Engine already entered the shield once; remaining physics ticks use the same request.
            history, _, _, _, _ = env.step_request(
                decision.requested, first_signal=decision.signals, prior_switches=previous_switches
            )
            metrics.sample(env)
            audits.append(decision.model_dump(mode="json"))
            fallbacks[decision.fallback_reason or "none"] += 1
            sequence += 1
            rate = observation.physical("north.arrival_rate")
            if rate is not None:
                keep = []
                for method, due, predicted in pending:
                    if due <= observation.timestamp_s:
                        errors[method].append(abs(predicted - rate))
                    else:
                        keep.append((method, due, predicted))
                pending = keep
                for method, forecaster in forecasts.items():
                    forecaster.add(
                        ArrivalSample(timestamp_s=observation.timestamp_s, rate_per_minute=rate)
                    )
                    forecast = forecaster.predict(observation.timestamp_s, 10)
                    if forecast is not None:
                        pending.append(
                            (method, observation.timestamp_s + 10, forecast.rate_per_minute)
                        )
        report = metrics.finish(env)
        report.update(
            job_sha256=key,
            policy=policy_name,
            plan_sha256=plan["sha256"],
            checkpoint_sha256=file_hash(checkpoint) if checkpoint else None,
            fallbacks=dict(fallbacks),
            downstream_sensor_available=False,
            forecast_quality={
                m: {
                    "MAE_vehicle_per_min": float(np.mean(v)) if v else None,
                    "samples": len(v),
                    "horizon_s": 10,
                    "target": "future observed rolling arrival-rate estimate",
                }
                for m, v in errors.items()
            },
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        write_new(destination, report)
        write_new(destination.with_suffix(".decisions.json"), audits)
        return report
    finally:
        engine.close()
        env.close()


def benchmark(plan_path: Path) -> None:
    configure_cpu()
    plan = read(plan_path)
    verify_plan(plan)
    root = Path(plan["output"])
    checkpoints = {
        str(seed): root / "runs" / f"full-{seed}" / f"checkpoint-{plan['training_steps']:07}.zip"
        for seed in plan["run_seeds"]
    }
    selection_path = root / "selection.json"
    selection = {
        "plan_sha256": plan["sha256"],
        "checkpoints": {s: file_hash(p) for s, p in checkpoints.items()},
        "steps": plan["training_steps"],
        "rule": "Preregistered final budget; no TEST selection",
    }
    if selection_path.exists():
        if read(selection_path) != selection:
            raise ValueError("Frozen selection differs")
    else:
        write_new(selection_path, selection)
    rows = []
    for split in ("validation", "test"):
        for case in plan[split]:
            for name in ("fixed", "adaptive", "actuated", "max_pressure", "rohai_ppo"):
                for seed in plan["run_seeds"] if name == "rohai_ppo" else [None]:
                    path = root / "evaluation" / split / f"{case['scenario_id']}-{name}-{seed}.json"
                    rows.append(
                        evaluate_case(
                            plan,
                            case,
                            name,
                            checkpoints[str(seed)] if seed is not None else None,
                            path,
                        )
                    )
    tests = [r for r in rows if r["scenario"]["scenario_id"].startswith("TEST-")]
    comparisons = {}
    for baseline in ("fixed", "adaptive", "actuated", "max_pressure"):
        matrix = []
        for seed in plan["run_seeds"]:
            delta = []
            for case in plan["test"]:
                matching = [r for r in tests if r["scenario"]["scenario_id"] == case["scenario_id"]]
                b = next(r for r in matching if r["policy"] == baseline)
                a = next(
                    r
                    for r in matching
                    if r["checkpoint_sha256"] == file_hash(checkpoints[str(seed)])
                )
                assert a["arrival_tape_sha256"] == b["arrival_tape_sha256"]
                delta.append(
                    a["metrics"]["total_delay_entity_s"] - b["metrics"]["total_delay_entity_s"]
                )
            matrix.append(delta)
        comparisons[baseline] = paired(np.asarray(matrix, dtype=np.float64), familywise_tests=4)
    summary = {
        "status": "SMOKE_STUDY" if plan["smoke"] else "BOUNDED_SYNTHETIC_STUDY",
        "plan_sha256": plan["sha256"],
        "promotion": False,
        "physical_activation": False,
        "comparisons_total_delay": comparisons,
        "test_metrics": {
            name: {
                k: float(
                    np.mean(
                        [
                            r["metrics"][k]
                            for r in tests
                            if r["policy"] == name and r["metrics"][k] is not None
                        ]
                    )
                )
                for k in tests[0]["metrics"]
            }
            for name in ("fixed", "adaptive", "actuated", "max_pressure", "rohai_ppo")
        },
        "all_invariants_passed": all(r["invariants_passed"] for r in rows),
        "limitations": "Small seed sample; fixed synthetic suite CI; no field/HIL claim",
    }
    write_new(root / "benchmark.json", summary)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "comparisons": {k: v["verdict"] for k, v in comparisons.items()},
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "train", "benchmark"))
    parser.add_argument("--config", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--plan", type=Path)
    args = parser.parse_args()
    if args.action == "prepare":
        if args.config is None or args.output is None:
            parser.error("prepare requires --config and --output")
        print(prepare(args.config, args.output))
    else:
        if args.plan is None:
            parser.error("--plan required")
        if args.action == "benchmark":
            benchmark(args.plan)
        else:
            plan = read(args.plan)
            for seed in plan["run_seeds"]:
                directory = train_run(str(args.plan), seed, "full")
                checkpoint = Path(directory) / f"checkpoint-{plan['training_steps']:07}.zip"
                print(
                    json.dumps(
                        {
                            "seed": seed,
                            "run": directory,
                            "status": "COMPLETED"
                            if checkpoint.exists()
                            else "RESOURCE_LIMIT_PARTIAL",
                        }
                    ),
                    flush=True,
                )


if __name__ == "__main__":
    main()

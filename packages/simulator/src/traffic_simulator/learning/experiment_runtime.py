"""Bounded PPO experiment, episode-boundary checkpoints and paired scoring.

Only local, hash-verified SB3 checkpoints created by this runner may be loaded.
Ground truth in this module is used exclusively by the metric collector.
"""

import hashlib
import json
import os
import pickle
import random
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import gymnasium as gym
import numpy as np
import torch
from numpy.typing import NDArray
from stable_baselines3 import PPO
from stable_baselines3.common.logger import configure
from traffic_core.models import ObjectKind
from traffic_core.settings import Timing

from traffic_simulator.learning.contracts import FEATURES, RewardSpecification
from traffic_simulator.learning.environment import LearningControlEnvironment
from traffic_simulator.learning.policy import (
    ActuatedController,
    CurrentAdaptiveController,
    FixedTimeController,
)
from traffic_simulator.learning.protocol import digest
from traffic_simulator.twin_evaluation import code_fingerprint
from traffic_simulator.twin_models import PerceptionProfile, ScenarioDefinition

VARIANTS = ("full", "no_confidence", "no_arrivals", "no_history", "clean_training")
Array = NDArray[np.float32]


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_new(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, allow_nan=False)


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def transform(history: Array, variant: str) -> Array:
    if variant not in VARIANTS:
        raise ValueError("Unknown ablation")
    output = history.copy()
    if variant == "no_history":
        output[:-1] = 0
    suffix = {"no_confidence": ".observation_confidence", "no_arrivals": ".arrival_rate"}.get(
        variant
    )
    if suffix:
        for i, feature in enumerate(FEATURES):
            if feature.name.endswith(suffix):
                output[:, [i, i + len(FEATURES), i + 2 * len(FEATURES)]] = 0
    return output


def clean_perception() -> PerceptionProfile:
    fields = (
        "missed_detection_probability",
        "false_positives_per_frame",
        "id_switch_probability",
        "occlusion_probability",
        "position_noise_std_m",
        "wait_noise_std_s",
        "queue_undercount_probability",
        "camera_delay_s",
        "inference_latency_s",
        "frame_drop_probability",
        "calibration_scale_error",
    )
    return PerceptionProfile.model_validate(dict.fromkeys(fields, 0))


class MetricCollector:
    def __init__(self) -> None:
        self.counts: list[int] = []
        self.lengths: list[float] = []
        self.inference_ms: list[float] = []

    def sample(self, env: LearningControlEnvironment) -> None:
        assert env.world is not None
        truth = env.world.ground_truth()
        vehicles = [e for e in truth.queued if e.kind != ObjectKind.PEDESTRIAN]
        self.counts.append(len(vehicles) + len(truth.upstream))
        self.lengths.append(max((e.distance_to_stop_m + e.length_m for e in vehicles), default=0))

    def finish(self, env: LearningControlEnvironment) -> dict[str, Any]:
        report = env.episode_report()
        raw = report["reward"]["components"]
        counts = report["supervisor_interventions"]
        rejected = sum(v for k, v in counts.items() if k not in ("accepted", "duration_clamped"))
        fair = report["fairness"]
        direction_max = [
            v["all_arrivals_lower_bound"]["maximum"] or 0 for v in fair["by_direction"].values()
        ]
        report["metrics"] = {
            "total_delay_entity_s": raw["total_delay"]["raw"],
            "vehicle_delay_vehicle_s": raw["total_delay"]["raw"] - raw["pedestrian_delay"]["raw"],
            "pedestrian_delay_person_s": raw["pedestrian_delay"]["raw"],
            "throughput_entities": report["departed"],
            "max_wait_s": fair["all"]["all_arrivals_lower_bound"]["maximum"] or 0,
            "pedestrian_max_wait_s": fair["pedestrians"]["all_arrivals_lower_bound"]["maximum"]
            or 0,
            "queue_count_mean": float(np.mean(self.counts)),
            "queue_count_max": max(self.counts),
            "queue_length_mean_m": float(np.mean(self.lengths)),
            "queue_length_max_m": max(self.lengths),
            "fairness_direction_max_gap_s": max(direction_max) - min(direction_max),
            "safety_intervention_rate": rejected / max(1, sum(counts.values())),
            "switch_count": raw["switching"]["raw"],
            "episode_reward": report["reward"]["total_reward"],
        }
        report["metrics_units"] = {
            "queue_length": "m; maximum ground-truth lane queue extent, upstream excluded",
            "queue_count": "vehicle; all queued plus upstream",
            "safety_intervention_rate": "fraction of physics ticks, including clearance locks",
            "wait": "s; unfinished-trip lower bounds, censoring in fairness",
        }
        return report


class TrainingEnvironment(gym.Env[Array, int]):
    """Rotates TRAIN realizations; only actor observations leave reset/step."""

    def __init__(
        self, plan: dict[str, Any], seed: int, variant: str, directory: Path, cursor: int = 0
    ) -> None:
        self.plan, self.run_seed, self.variant, self.directory = plan, seed, variant, directory
        self.cursor = cursor
        self.env = LearningControlEnvironment(
            ScenarioDefinition.model_validate(plan["training"][0])
        )
        self.observation_space, self.action_space = (
            self.env.observation_space,
            self.env.action_space,
        )
        self.collector = MetricCollector()

    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[Array, dict[str, Any]]:
        super().reset(seed=seed)
        self.env.close()
        scenario = ScenarioDefinition.model_validate(
            self.plan["training"][self.cursor % len(self.plan["training"])]
        )
        scenario = scenario.model_copy(
            update={
                "seed": 1_000_000 + self.run_seed * 100 + self.cursor,
                "scenario_id": f"TRAIN-{self.run_seed}-{self.cursor}",
            }
        )
        if self.variant == "clean_training":
            scenario = scenario.model_copy(update={"perception": clean_perception()})
        self.env = LearningControlEnvironment(
            scenario,
            reward=RewardSpecification.model_validate(self.plan["reward"]),
            lock_scenario_seed=True,
        )
        self.collector = MetricCollector()
        obs, info = self.env.reset(seed=seed)
        return transform(obs, self.variant), info

    def step(self, action: int) -> tuple[Array, float, bool, bool, dict[str, Any]]:
        obs, reward, terminated, truncated, info = self.env.step(action)
        self.collector.sample(self.env)
        if terminated or truncated:
            report = self.collector.finish(self.env)
            report.update(
                training_started=True,
                run_seed=self.run_seed,
                variant=self.variant,
                episode=self.cursor,
            )
            # Separate immutable logs per completed episode; incomplete segments may be replayed.
            path = self.directory / f"episode-{self.cursor:04}.json"
            if path.exists():
                if digest(read(path)) != digest(report):
                    raise ValueError("Resume episode differs from previously recorded episode")
            else:
                write_new(path, report)
            self.cursor += 1
            info = {
                **info,
                "episode": {
                    "r": report["metrics"]["episode_reward"],
                    "l": round(self.env.scenario.duration_s),
                },
            }
        return (
            transform(obs, self.variant),
            reward * self.plan["reward_scale"],
            terminated,
            truncated,
            info,
        )

    def close(self) -> None:
        self.env.close()


def configure_cpu() -> None:
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)


def verify_plan(plan: dict[str, Any]) -> None:
    body = {k: v for k, v in plan.items() if k != "sha256"}
    if digest(body) != plan["sha256"]:
        raise ValueError("Experiment plan hash mismatch")
    if code_fingerprint() != plan["code_sha256"]:
        raise ValueError("Code differs from preregistered experiment")
    if file_hash(Path("scripts/training/control_experiment.py")) != plan["runner_sha256"]:
        raise ValueError("Runner differs from preregistration")
    reward = RewardSpecification.model_validate(plan["reward"])
    if not reward.approved_for_training:
        raise ValueError("Reward review missing")


def train_run(plan_path: str, seed: int, variant: str, target_steps: int | None = None) -> str:
    configure_cpu()
    plan = read(Path(plan_path))
    verify_plan(plan)
    if variant not in VARIANTS or seed not in plan["run_seeds"]:
        raise ValueError("Unregistered run")
    directory = Path(plan["output"]) / "runs" / f"{variant}-{seed}"
    directory.mkdir(parents=True, exist_ok=True)
    target = target_steps or plan["training_steps"]
    chunk = plan["algorithm"]["n_steps"]
    if target % chunk or target > plan["training_steps"]:
        raise ValueError("Target must be a registered rollout boundary")
    # A checkpoint is committed only when its sidecar exists. Orphan payloads are replaceable.
    checkpoints = sorted(directory.glob("checkpoint-*.json"))
    previous = read(checkpoints[-1]) if checkpoints else None
    if previous and previous["plan_sha256"] != plan["sha256"]:
        raise ValueError("Resume plan mismatch")
    if previous:
        for payload, checksum in (("weights", "weights_sha256"), ("rng", "rng_sha256")):
            if file_hash(directory / previous[payload]) != previous[checksum]:
                raise ValueError("Corrupt committed checkpoint")
        if previous["steps"] >= target:
            return str(directory)
    start_path = directory / "run-start.json"
    if not start_path.exists():
        write_new(
            start_path,
            {
                "plan_sha256": plan["sha256"],
                "seed": seed,
                "variant": variant,
                "started_at": datetime.now(UTC).isoformat(),
            },
        )
    env = TrainingEnvironment(
        plan, seed, variant, directory, cursor=previous["episodes_completed"] if previous else 0
    )
    if previous:
        zip_path = directory / previous["weights"]
        rng_path = directory / previous["rng"]
        if (
            file_hash(zip_path) != previous["weights_sha256"]
            or file_hash(rng_path) != previous["rng_sha256"]
        ):
            raise ValueError("Corrupt checkpoint or RNG state")
        model = PPO.load(zip_path, env=env, device="cpu")
        with rng_path.open("rb") as stream:
            state = pickle.load(stream)  # local hash-verified runner output only
        random.setstate(state["python"])
        np.random.set_state(state["numpy"])
        torch.set_rng_state(state["torch"])
    else:
        model = PPO(
            "MlpPolicy",
            env,
            seed=seed,
            device="cpu",
            verbose=0,
            policy_kwargs={"net_arch": [32, 32]},
            **plan["algorithm"],
        )
    model.set_logger(configure(str(directory / f"optimizer-{model.num_timesteps}"), ["csv"]))
    try:
        while model.num_timesteps < target:
            model.learn(total_timesteps=chunk, reset_num_timesteps=False)
            steps = model.num_timesteps
            if steps % round(env.env.scenario.duration_s):
                raise ValueError("Checkpoint must end at episode boundary")
            stem = f"checkpoint-{steps:07}"
            weights, rng = directory / f"{stem}.zip", directory / f"{stem}.rng"
            model.save(weights)
            with rng.open("wb") as stream:
                pickle.dump(
                    {
                        "python": random.getstate(),
                        "numpy": np.random.get_state(),
                        "torch": torch.get_rng_state(),
                    },
                    stream,
                )
            write_new(
                directory / f"{stem}.json",
                {
                    "steps": steps,
                    "episodes_completed": env.cursor,
                    "seed": seed,
                    "variant": variant,
                    "plan_sha256": plan["sha256"],
                    "weights": weights.name,
                    "weights_sha256": file_hash(weights),
                    "rng": rng.name,
                    "rng_sha256": file_hash(rng),
                    "timestamp": datetime.now(UTC).isoformat(),
                    "git_commit": plan["git_commit"],
                    "dirty_tree": plan["dirty_tree"],
                    "code_sha256": plan["code_sha256"],
                    "environment_version": "rohai-control-env-v1",
                    "observation_schema": "rohai-observation-v1",
                    "reward": plan["reward"],
                    "algorithm": plan["algorithm"],
                    "dependencies": plan["dependencies"],
                    "resume_mode": "episode-and-rollout-boundary",
                },
            )
    finally:
        env.close()
    return str(directory)


def evaluate_run(job: dict[str, Any]) -> str:
    import time

    configure_cpu()
    plan = read(Path(job["plan"]))
    verify_plan(plan)
    scenario = ScenarioDefinition.model_validate(job["scenario"])
    destination = Path(job["destination"])
    if destination.exists():
        existing = read(destination)
        if existing["job_sha256"] != digest(job):
            raise ValueError("Evaluation cache identity mismatch")
        return str(destination)
    if job["split"] in ("TEST", "CHALLENGE"):
        selection = read(Path(plan["output"]) / "selection.json")
        if selection["plan_sha256"] != plan["sha256"]:
            raise ValueError("Selection must be frozen before final evaluation")
        marker = read(Path(plan["output"]) / "final-evaluation-started.json")
        if marker["selection_sha256"] != file_hash(Path(plan["output"]) / "selection.json"):
            raise ValueError("Final selection was modified")
        if job["policy"] == "learned" and (
            job["variant"] != "full"
            or job["steps"] != selection["steps"]
            or job["seed"] not in selection["run_seeds"]
            or file_hash(Path(job["checkpoint"])) != selection["checkpoints"][str(job["seed"])]
        ):
            raise ValueError("Unselected policy cannot enter TEST/CHALLENGE")
    allowed_cases = (
        [plan["regression"]] if job["split"] == "REGRESSION" else plan[job["split"].lower()]
    )
    if scenario.model_dump(mode="json") not in allowed_cases:
        raise ValueError("Unregistered evaluation scenario")
    env = LearningControlEnvironment(scenario, lock_scenario_seed=True)
    metric = MetricCollector()
    model: PPO | None = None
    baseline = None
    if job["policy"] == "learned":
        checkpoint = Path(job["checkpoint"])
        sidecar = read(checkpoint.with_suffix(".json"))
        if file_hash(checkpoint) != sidecar["weights_sha256"]:
            raise ValueError("Checkpoint checksum mismatch")
        model = PPO.load(checkpoint, device="cpu")
    else:
        baseline = {
            "fixed": FixedTimeController,
            "adaptive": CurrentAdaptiveController,
            "actuated": ActuatedController,
        }[job["policy"]](Timing())
        baseline.reset(scenario.seed)
    actions = dict.fromkeys(range(9), 0)
    sensitivity = dict.fromkeys(("no_confidence", "no_arrivals", "no_history"), 0)
    feature_ranges: dict[str, list[float]] = {}
    try:
        history, _ = env.reset(seed=scenario.seed)
        while not env.ended:
            start = time.perf_counter()
            if model:
                index = int(
                    model.predict(transform(history, job["variant"]), deterministic=True)[0]
                )
                metric.inference_ms.append((time.perf_counter() - start) * 1000)
                actions[index] += 1
                if job["split"] == "VALIDATION" and job["variant"] == "full":
                    for variant in sensitivity:
                        alternative = int(
                            model.predict(transform(history, variant), deterministic=True)[0]
                        )
                        sensitivity[variant] += int(alternative != index)
                    for i, feature in enumerate(FEATURES):
                        if feature.name.endswith((".observation_confidence", ".arrival_rate")):
                            value = float(history[-1, i])
                            bounds = feature_ranges.setdefault(feature.name, [value, value])
                            bounds[0], bounds[1] = min(bounds[0], value), max(bounds[1], value)
                history, _, _, _, _ = env.step(index)
            else:
                assert baseline is not None
                request = baseline.propose(env.latest, history)
                metric.inference_ms.append((time.perf_counter() - start) * 1000)
                history, _, _, _, _ = env.step_request(request)
            metric.sample(env)
        report = metric.finish(env)
        report.update(
            job=job,
            job_sha256=digest(job),
            training_started=bool(model),
            action_histogram=actions,
            validation_action_sensitivity=sensitivity if feature_ranges else None,
            validation_feature_ranges=feature_ranges,
            inference_latency_ms={
                "median": float(np.median(metric.inference_ms)),
                "p95": float(np.percentile(metric.inference_ms, 95)),
                "maximum": max(metric.inference_ms),
            },
        )
        write_new(destination, report)
    finally:
        env.close()
    return str(destination)


def process_train(job: tuple[str, int, str]) -> str:
    return train_run(*job)


def run_parallel(function: Any, jobs: list[Any], workers: int) -> list[str]:
    from concurrent.futures import ProcessPoolExecutor, as_completed

    # Computational workers, not autonomous agents. Limit BLAS oversubscription.
    for name in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ[name] = "1"
    results = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(function, job) for job in jobs]
        for future in as_completed(futures):
            results.append(cast(str, future.result()))
            print(f"completed {len(results)}/{len(jobs)}", flush=True)
    return results

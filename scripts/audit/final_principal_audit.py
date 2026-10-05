"""Fresh paired synthetic audit. No training or physical actuation."""

import argparse
import hashlib
import json
import time
from pathlib import Path

import numpy as np
import torch
from stable_baselines3 import PPO
from traffic_core.settings import Timing
from traffic_simulator.learning.environment import LearningControlEnvironment
from traffic_simulator.learning.policy import (
    ActuatedController,
    CurrentAdaptiveController,
    FixedTimeController,
)
from traffic_simulator.scenarios import catalog
from traffic_simulator.twin_models import ScenarioDefinition


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", default=None)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    models, hashes = {}, {}
    for seed in (701, 702, 703):
        path = Path(f"artifacts/control/20260929-v1/runs/full-{seed}/checkpoint-0002160.zip")
        sidecar = json.loads(path.with_suffix(".json").read_text())
        if sha(path) != sidecar["weights_sha256"]:
            raise ValueError("Checkpoint hash mismatch")
        hashes[str(seed)] = {"hash": sha(path), "sidecar": sidecar}
        models[f"learned-{seed}"] = PPO.load(path, device="cpu")
    results = []
    for seed in (913001, 913002):
        cases = catalog(seed=seed, duration_s=120)
        for name, changes in (
            ("heavy-occlusion", {"occlusion_probability": 0.6, "occlusion_duration_s": 5}),
            ("id-switch-storm", {"id_switch_probability": 0.8}),
            ("low-fps", {"fps": 1}),
            ("detector-regression", {"missed_detection_probability": 0.6}),
        ):
            payload = cases["balanced"].model_dump(mode="json")
            payload["perception"].update(changes)
            payload["scenario_id"] = f"AUDIT-{name}-{seed}"
            cases[name] = ScenarioDefinition.model_validate(payload)
        payload = cases["balanced"].model_dump(mode="json")
        for period in payload["traffic"]["periods"]:
            period["vehicles_per_minute"] = dict.fromkeys(period["vehicles_per_minute"], 0)
            period["pedestrians_per_minute"] = 0
        payload["scenario_id"] = f"AUDIT-no-traffic-{seed}"
        cases["no-traffic"] = ScenarioDefinition.model_validate(payload)
        payload = cases["balanced"].model_dump(mode="json")
        base = payload["traffic"]["periods"][0]
        payload["traffic"]["periods"] = [
            {**base, "start_s": 0, "end_s": 40, "pedestrians_per_minute": 6},
            {**base, "start_s": 40, "end_s": 80, "pedestrians_per_minute": 150},
            {**base, "start_s": 80, "end_s": 120, "pedestrians_per_minute": 6},
        ]
        payload["scenario_id"] = f"AUDIT-pedestrian-surge-{seed}"
        cases["pedestrian-surge"] = ScenarioDefinition.model_validate(payload)
        for family, case in cases.items():
            if args.only is not None and family != args.only:
                continue
            tapes = set()
            policies = {
                "fixed": FixedTimeController(Timing()),
                "adaptive": CurrentAdaptiveController(Timing()),
                "actuated": ActuatedController(Timing()),
                **models,
            }
            for name, policy in policies.items():
                env = LearningControlEnvironment(case, lock_scenario_seed=True)
                try:
                    history, _ = env.reset(seed=case.seed)
                    if name not in models:
                        policy.reset(case.seed)
                    latency, queues = [], []
                    while not env.ended:
                        start = time.perf_counter()
                        if name in models:
                            action = int(policy.predict(history.copy(), deterministic=True)[0])
                            latency.append((time.perf_counter() - start) * 1000)
                            history, _, _, _, _ = env.step(action)
                        else:
                            request = policy.propose(env.latest, history.copy())
                            latency.append((time.perf_counter() - start) * 1000)
                            history, _, _, _, _ = env.step_request(request)
                        assert env.world is not None
                        truth = env.world.ground_truth()
                        queues.append(len(truth.queued) + len(truth.upstream))
                    report = env.episode_report()
                    truth = env.world.ground_truth()
                    tapes.add(report["arrival_tape_sha256"])
                    metrics = {
                        "total_delay_entity_s": truth.total_wait_s + truth.downstream_delay_s,
                        "pedestrian_delay_person_s": truth.pedestrian_wait_s,
                        "throughput_entities": truth.departed,
                        "remaining_entities": truth.arrived - truth.departed,
                        "queue_p95_entities": float(np.percentile(queues, 95)),
                        "queue_max_entities": max(queues),
                        "inference_p95_ms": float(np.percentile(latency, 95)),
                        "inference_max_ms": max(latency),
                    }
                    results.append(
                        {
                            "family": family,
                            "seed": seed,
                            "policy": name,
                            "metrics": metrics,
                            "report": report,
                        }
                    )
                finally:
                    env.close()
            if len(tapes) != 1:
                raise AssertionError("Policies received different arrival tapes")
            print(f"completed {family} seed={seed}", flush=True)
    source = {
        str(p).replace("\\", "/"): sha(p)
        for base in ("apps/backend/src", "packages")
        for p in sorted(Path(base).rglob("*.py"))
    }
    output = {
        "scope": "synthetic only; fresh flow seeds, known scenario families",
        "flow_seeds": [913001, 913002],
        "duration_s": 120,
        "checkpoints": hashes,
        "source_hashes": source,
        "runner_hash": sha(Path(__file__)),
        "results": results,
        "all_invariants_passed": all(r["report"]["invariants_passed"] for r in results),
    }
    (args.output / "policy_comparison.json").write_text(
        json.dumps(output, indent=2, allow_nan=False), encoding="utf-8"
    )
    print(json.dumps({"episodes": len(results), "invariants": output["all_invariants_passed"]}))


if __name__ == "__main__":
    main()

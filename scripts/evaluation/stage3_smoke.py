"""Fresh, explicitly synthetic train/load smoke with measured process resources."""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import psutil  # type: ignore[import-untyped]

ROOT = Path("artifacts/rohai-ai-2.0/stage3/smoke")


def child(kind: str) -> None:
    if kind == "vision":
        from traffic_vision.evaluation.vision import run_pretrained
        from traffic_vision.settings import VisionSettings
        from traffic_vision.training.config import TrainingConfig
        from traffic_vision.training.runner import train

        old = Path("artifacts/rohai-ai-2.0")
        baseline, _ = run_pretrained(old / "fixture-release", settings=VisionSettings(device="cpu"))
        baseline_path = ROOT / "vision-baseline.json"
        baseline_path.write_text(json.dumps(baseline, indent=2), encoding="utf-8")
        config = TrainingConfig.model_validate_json(
            (old / "fixture-training-config.json").read_text(encoding="utf-8")
        )
        config = config.model_copy(
            update={"experiment": "stage3-synthetic-train-smoke", "baseline": baseline_path}
        )
        result = train(config, ROOT / "vision-runs")
        (ROOT / "vision-result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    else:
        from traffic_core.model_registry import sha256
        from traffic_simulator.learning.experiment_runtime import train_run
        from traffic_simulator.learning.intelligence_experiment import prepare
        from traffic_simulator.learning.safe_actor import export_checkpoint

        control_config = {
            "experiment_id": "stage3-training-smoke-NOT-FINAL-MODEL",
            "run_seeds": [3001, 3002],
            "scenario_duration_s": 60,
            "training_steps": 240,
            "n_steps": 120,
            "batch_size": 60,
            "n_epochs": 2,
            "learning_rate": 0.0003,
            "net_arch": [64, 64],
            "device": "cpu",
            "max_training_seconds": 120,
            "smoke": True,
        }
        config_path = ROOT / "control-config-v2.json"
        config_path.write_text(json.dumps(control_config, indent=2), encoding="utf-8")
        plan = prepare(config_path, ROOT / "control-run")
        results = []
        for seed in (3001, 3002):
            run = Path(train_run(str(plan), seed, "full"))
            checkpoint = run / "checkpoint-0000240.zip"
            actor = ROOT / f"control-smoke-{seed}.npz"
            export_checkpoint(checkpoint, sha256(checkpoint), actor)
            results.append(
                {
                    "seed": seed,
                    "checkpoint": str(checkpoint),
                    "sha256": sha256(checkpoint),
                    "actor": str(actor),
                    "actor_sha256": sha256(actor),
                }
            )
        (ROOT / "control-result.json").write_text(
            json.dumps(
                {
                    "status": "COMPLETED",
                    "models": results,
                    "steps_per_seed": 240,
                    "heldout_evaluation": "NOT VERIFIED",
                    "physical_activation": False,
                },
                indent=2,
            ),
            encoding="utf-8",
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", choices=("vision", "control"))
    parser.add_argument("--kind", choices=("vision", "control"))
    args = parser.parse_args()
    if args.child:
        child(args.child)
        return
    ROOT.mkdir(parents=True, exist_ok=args.kind is not None)
    resources: dict[str, Any] = {}
    for kind in (args.kind,) if args.kind else ("vision", "control"):
        started = time.monotonic()
        rss, cpu = [], []
        with (ROOT / (kind + ("-v2.log" if args.kind else ".log"))).open("xb") as log:
            process = subprocess.Popen(
                [sys.executable, __file__, "--child", kind], stdout=log, stderr=subprocess.STDOUT
            )
            monitor = psutil.Process(process.pid)
            while process.poll() is None:
                try:
                    tree = [monitor, *monitor.children(recursive=True)]
                    rss.append(sum(p.memory_info().rss for p in tree))
                    cpu.append(sum(p.cpu_times().user + p.cpu_times().system for p in tree))
                except psutil.Error:
                    pass
                if time.monotonic() - started > 300:
                    for owned in monitor.children(recursive=True):
                        owned.kill()
                    process.kill()
                    process.wait()
                    raise TimeoutError("Training smoke resource deadline")
                time.sleep(0.2)
        resources[kind] = {
            "exit_code": process.returncode,
            "wall_s": time.monotonic() - started,
            "peak_tree_rss_bytes": max(rss, default=0),
            "cpu_seconds": max(cpu, default=0),
            "device": "cpu",
            "gpu_usage": "NOT MEASURED",
            "purpose": "SYNTHETIC_SMOKE_ONLY",
        }
        print(json.dumps({kind: resources[kind]}), flush=True)
        (ROOT / (kind + "-resources-v2.json")).write_text(
            json.dumps(resources[kind], indent=2), encoding="utf-8"
        )
        if process.returncode != 0:
            raise RuntimeError(f"{kind} training failed; inspect saved log")
    (ROOT / "resources.json").write_text(json.dumps(resources, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()

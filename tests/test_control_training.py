import json
import runpy
from pathlib import Path
from typing import Any

import numpy as np
import pytest
import torch
from stable_baselines3 import PPO
from traffic_core.models import Direction
from traffic_simulator.learning.contracts import FEATURES, INDEX
from traffic_simulator.learning.experiment_runtime import (
    evaluate_run,
    read,
    train_run,
    transform,
    verify_plan,
    write_new,
)
from traffic_simulator.learning.experiment_statistics import paired, required_runs
from traffic_simulator.learning.protocol import digest
from traffic_simulator.twin_models import CameraFault, GroundTruthState, PerceptionProfile
from traffic_simulator.twin_observation import ObservationModel


def small_plan(tmp_path: Path, name: str) -> Path:
    path = tmp_path / f"{name}.json"
    runner = runpy.run_path("scripts/training/control_experiment.py")
    runner["preregister"](path, tmp_path / name)
    plan = read(path)
    case = plan["training"][0]
    case["duration_s"] = 4
    case["traffic"]["periods"][0]["end_s"] = 4
    plan["training"] = [case]
    plan["training_steps"] = 16
    plan["algorithm"].update(n_steps=8, batch_size=4, n_epochs=1)
    plan["sha256"] = digest({k: v for k, v in plan.items() if k != "sha256"})
    path.write_text(json.dumps(plan), encoding="utf-8")
    return path


def test_ppo_resume_matches_uninterrupted_parameters(tmp_path: Path) -> None:
    continuous = small_plan(tmp_path, "continuous")
    interrupted = small_plan(tmp_path, "interrupted")
    direct = Path(train_run(str(continuous), 701, "full"))
    train_run(str(interrupted), 701, "full", target_steps=8)
    resumed = Path(train_run(str(interrupted), 701, "full"))
    first = PPO.load(direct / "checkpoint-0000016.zip", device="cpu")
    second = PPO.load(resumed / "checkpoint-0000016.zip", device="cpu")
    assert first.num_timesteps == second.num_timesteps == 16
    for key, value in first.policy.state_dict().items():
        assert torch.equal(value, second.policy.state_dict()[key]), key
    assert len(list(resumed.glob("episode-*.json"))) == 4
    with (resumed / "checkpoint-0000016.zip").open("ab") as stream:
        stream.write(b"corruption")
    with pytest.raises(ValueError, match="Corrupt"):
        train_run(str(interrupted), 701, "full")


def test_plan_and_unreviewed_reward_rejected(tmp_path: Path) -> None:
    path = small_plan(tmp_path, "plan")
    plan = read(path)
    plan["training_steps"] += 1
    with pytest.raises(ValueError, match="hash"):
        verify_plan(plan)
    plan["reward"]["approved_for_training"] = False
    plan["sha256"] = digest({k: v for k, v in plan.items() if k != "sha256"})
    with pytest.raises(ValueError, match="review"):
        verify_plan(plan)


def test_ablation_masks_values_validity_clipping_without_mutating_input() -> None:
    original = np.ones((4, 3 * len(FEATURES) + 1), dtype=np.float32)
    for variant, name in (
        ("no_confidence", "north.observation_confidence"),
        ("no_arrivals", "north.arrival_rate"),
    ):
        actual = transform(original, variant)
        i = INDEX[name]
        assert not actual[:, [i, i + len(FEATURES), i + 2 * len(FEATURES)]].any()
        assert original.all()
        assert actual[:, INDEX["north.queue_count"]].all()
    history = transform(original, "no_history")
    assert not history[:-1].any() and history[-1].all()
    with pytest.raises(ValueError):
        transform(original, "unknown")


def test_latency_spike_delays_capture_and_cannot_rewind_delivered_frames() -> None:
    sensor = ObservationModel(
        PerceptionProfile(
            camera_delay_s=0,
            inference_latency_s=0,
            frame_drop_probability=0,
            faults=(CameraFault(start_s=1, end_s=2, mode="latency_spike"),),
        ),
        71,
    )
    timestamps = []
    for tick in range(33):
        now = tick / 4
        sensor.ingest(GroundTruthState(timestamp_s=now, queued=()))
        observed = sensor.observe(now)
        north = next(a for a in observed.traffic.approaches if a.direction == Direction.NORTH)
        timestamps.append(north.observation.measurement_timestamp)
    assert timestamps[7] == 0.75  # Frames at 1..1.75 are delayed by 5 seconds.
    assert timestamps[8] == 2
    assert all(
        a <= b
        for a, b in zip(timestamps, timestamps[1:], strict=False)
        if a is not None and b is not None
    )


def test_seed_cluster_ci_and_variance_run_count() -> None:
    matrix = np.array([[-5, -5], [-6, -6], [-4, -4]], dtype=float)
    report = paired(matrix)
    assert report["n"] == 3  # Not six independent observations.
    assert report["verdict"] == "BETTER"
    assert required_runs([1, 1, 1, 1], 100)["selected_n"] == 12
    large = required_runs([-1000, 1000, -1000, 1000], 100)
    assert large["selected_n"] == 16 and large["budget_limited"]


def test_test_requires_frozen_selection(tmp_path: Path) -> None:
    path = small_plan(tmp_path, "selection")
    plan = read(path)
    job: dict[str, Any] = {
        "plan": str(path),
        "scenario": plan["test"][0],
        "split": "TEST",
        "policy": "fixed",
        "destination": str(tmp_path / "result.json"),
    }
    with pytest.raises(FileNotFoundError):
        evaluate_run(job)
    output = Path(plan["output"])
    write_new(output / "selection.json", {"plan_sha256": "wrong"})
    with pytest.raises(ValueError, match="Selection"):
        evaluate_run(job)


def test_completed_experiment_cannot_reconsume_test(tmp_path: Path) -> None:
    path = small_plan(tmp_path, "completed")
    plan = read(path)
    write_new(Path(plan["output"]) / "summary.json", {"completed": True})
    runner = runpy.run_path("scripts/training/control_experiment.py")
    with pytest.raises(FileExistsError, match="completed"):
        runner["execute"](path, 1)

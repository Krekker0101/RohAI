"""Freeze reference contracts and new scenario seeds before any policy fitting."""

from pathlib import Path

from traffic_simulator.learning.contracts import (
    FEATURES,
    ControlRequest,
    ObservationV1,
    RewardSpecification,
)
from traffic_simulator.learning.protocol import PolicyRunManifest, ScenarioSplits, digest
from traffic_simulator.scenarios import catalog
from traffic_simulator.twin_evaluation import write_report


def main() -> None:
    root = Path("configs/control")
    assignments = []
    for split, seed, name in (
        ("TRAIN", 11001, "balanced"),
        ("TRAIN", 11002, "NS-heavy"),
        ("TRAIN", 11003, "burst-traffic"),
        ("VALIDATION", 22001, "EW-heavy"),
        ("VALIDATION", 22002, "pedestrian-heavy"),
        ("TEST", 33001, "rapid-demand-change"),
        ("TEST", 33002, "balanced"),
        ("CHALLENGE", 44001, "saturated"),
        ("CHALLENGE", 44002, "camera-outage"),
        ("CHALLENGE", 44003, "night-degraded-vision"),
    ):
        case = catalog(seed=seed)[name].model_copy(update={"scenario_id": f"{split}-{name}-{seed}"})
        assignments.append({"split": split, "scenario": case.model_dump(mode="json")})
    payload = {"version": "rohai-control-splits-v1", "assignments": assignments}
    splits = ScenarioSplits.model_validate({**payload, "manifest_sha256": digest(payload)})
    write_report(root / "scenario-splits.json", splits.model_dump(mode="json"))
    for name, model in (
        ("observation-v1", ObservationV1),
        ("action-v1", ControlRequest),
        ("reward-v1", RewardSpecification),
        ("scenario-splits", ScenarioSplits),
        ("policy-run", PolicyRunManifest),
    ):
        write_report(root / f"{name}.schema.json", model.model_json_schema())
    reward = RewardSpecification()
    write_report(root / "reward-reference.json", reward.model_dump(mode="json"))
    write_report(
        root / "observation-layout.json",
        {
            "schema": "rohai-observation-v1",
            "features": [f.model_dump(mode="json") for f in FEATURES],
            "depth": 4,
            "decision_interval_s": 1,
            "layout": "[normalized values, validity, clipping, history_present]",
            "normalization": "clip(value/scale,0,1); missing=0 with validity=0; no TEST fitting",
        },
    )
    write_report(
        root / "experiment-plan.json",
        {
            "status": "ENVIRONMENT_FOUNDATION_ONLY",
            "training_started": False,
            "environment_version": "rohai-control-env-v1",
            "scenario_split_sha256": splits.manifest_sha256,
            "reward_sha256": digest(reward.model_dump(mode="json")),
            "reward_reviewed": False,
            "future_candidates": ["DQN with Discrete(9)", "PPO with categorical Discrete(9)"],
            "algorithm_hyperparameters": None,
            "held_out_test_evaluated": False,
            "prerequisites": [
                "Review reward on TRAIN/VALIDATION only",
                "Freeze algorithm configuration",
                "Validate sim-to-real assumptions",
                "Evaluate fairness and shield interventions",
            ],
        },
    )


if __name__ == "__main__":
    main()

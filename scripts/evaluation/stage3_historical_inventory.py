"""Inventory all historical final-budget PPO runs as inert archives, without loading them."""

import json
from datetime import datetime
from pathlib import Path

from traffic_core.model_registry import CentralModelRegistry, RegisteredModel, sha256


def main() -> None:
    root = Path("artifacts/rohai-ai-2.0/stage3")
    registry = CentralModelRegistry(root / "registry")
    historical = Path("artifacts/control/20260929-v1")
    plan_path = Path("configs/control/ppo-experiment-v1.json")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    inventory = []
    for directory in sorted((historical / "runs").iterdir()):
        if not directory.is_dir():
            continue
        checkpoints = sorted(directory.glob("checkpoint-*.zip"))
        if not checkpoints:
            continue
        checkpoint = checkpoints[-1]
        metadata_path = checkpoint.with_suffix(".json")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if sha256(checkpoint) != metadata["weights_sha256"]:
            raise ValueError("Historical artifact hash mismatch")
        record = RegisteredModel(
            model_id="historical-v1-" + directory.name,
            version=f"steps-{metadata['steps']}",
            kind="CONTROL",
            architecture="SB3-PPO-MlpPolicy-v1-archived",
            parent_model="Historical seed initialization",
            dataset_version=metadata["plan_sha256"],
            source_code_version=metadata["code_sha256"],
            training_config={
                "algorithm": metadata["algorithm"],
                "reward": metadata["reward"],
                "variant": metadata["variant"],
                "seed": metadata["seed"],
            },
            training_results={
                "status": "COMPLETED_HISTORICAL",
                "steps": metadata["steps"],
                "episodes": metadata["episodes_completed"],
                "checkpoints": len(checkpoints),
            },
            evaluation_results={
                "status": "HISTORICAL_ONLY",
                "current_independent_release": "NOT VERIFIED",
                "prior_promotion": False,
                "source": "SIMULATION",
            },
            artifact_sha256=metadata["weights_sha256"],
            artifact_format="archived-trusted-checkpoint",
            compatibility={
                "registry": "rohai-registry-2",
                "observation": metadata["observation_schema"],
                "executable": "NO: old SB3 serialization and input contract",
            },
            provenance={
                "source": str(checkpoint),
                "metadata_sha256": sha256(metadata_path),
                "historical_plan_sha256": sha256(plan_path),
                "plan_declared_sha256": plan["sha256"],
                "purpose": "historical_simulator_inventory",
            },
            license_information="Owned simulator training; SB3 MIT; Torch BSD-3-Clause",
            created_at=datetime.fromisoformat(metadata["timestamp"]),
        )
        registry.register(record, checkpoint)
        inventory.append(
            {
                "model": record.key,
                "checkpoint": str(checkpoint),
                "sha256": metadata["weights_sha256"],
                "training": record.training_results,
                "status": "EXPERIMENTAL_ARCHIVE_NOT_EXECUTABLE",
            }
        )
    with (root / "historical-models.json").open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "source": "SIMULATION_HISTORICAL",
                "models": inventory,
                "count": len(inventory),
                "physical_activation": False,
            },
            stream,
            indent=2,
        )
    print(json.dumps({"historical_final_models": len(inventory), "pickle_loaded": False}))


if __name__ == "__main__":
    main()

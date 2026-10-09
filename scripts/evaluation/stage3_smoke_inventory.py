"""Register actual new smoke outputs as unevaluated research inventory, never activate them."""

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from traffic_core.model_registry import CentralModelRegistry, RegisteredModel, sha256


def read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def losses(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as stream:
        return [{k.strip(): v.strip() for k, v in row.items()} for row in csv.DictReader(stream)]


def main() -> None:
    root = Path("artifacts/rohai-ai-2.0/stage3")
    registry = CentralModelRegistry(root / "registry")
    catalog = read(root / "training.json")
    smoke = read(root / "smoke/control-result.json")
    for result in smoke["models"]:
        checkpoint = Path(result["checkpoint"])
        metadata = read(checkpoint.with_suffix(".json"))
        plan = read(root / "smoke/control-run/plan.json")
        model_id = f"rohai-ppo-stage3-smoke-{result['seed']}"
        training = {
            "status": "COMPLETED_SMOKE_NOT_EVALUATED",
            "timesteps": 240,
            "seed": result["seed"],
            "checkpoints": 2,
            "progress": 1,
            "loss": losses(checkpoint.parent / "optimizer-0/progress.csv"),
            "validation": "NOT EVALUATED",
            "error": None,
        }
        record = RegisteredModel(
            model_id=model_id,
            version="smoke-240",
            kind="CONTROL",
            architecture="PPO-MLP-64x64-Tanh-9",
            parent_model="Seed initialization",
            dataset_version=plan["sha256"],
            source_code_version=metadata["code_sha256"],
            training_config=plan["algorithm"],
            training_results=training,
            evaluation_results={"status": "NOT EVALUATED"},
            artifact_sha256=result["actor_sha256"],
            artifact_format="numpy-actor-v1",
            compatibility={
                "registry": "rohai-registry-2",
                "observation": "rohai-observation-1+geometry-3",
            },
            provenance={
                "checkpoint_sha256": result["sha256"],
                "checkpoint_metadata_sha256": sha256(checkpoint.with_suffix(".json")),
            },
            license_information="Owned synthetic control training; SB3 MIT; Torch BSD-3-Clause",
            created_at=datetime.fromisoformat(metadata["timestamp"]),
        )
        registry.register(record, Path(result["actor"]))
        catalog["runs"].append({"model": record.key, **training})
    training = read(root / "smoke/vision-result.json")
    run = root / "smoke/vision-runs" / training["run_id"]
    old = registry.get("rohai-yolo26n-fixture-NOT-FIELD@smoke-1epoch")
    path = run / "ultralytics/weights/best.pt"
    record = RegisteredModel.model_validate(
        {
            **old.model_dump(),
            "version": "stage3-smoke-1epoch",
            "source_code_version": training["code_sha256"],
            "training_config": training["training_config"],
            "training_results": {"status": training["status"], "field_model": False, "epochs": 1},
            "artifact_sha256": sha256(path),
            "evaluation_results": {
                "independent_field_test": False,
                "training_validation": "6 synthetic validation images; mAP=0; no field evaluation",
            },
            "provenance": {
                "training_manifest_sha256": sha256(run / "manifest.json"),
                "dataset_hash": training["dataset_hash"],
                "purpose": "synthetic_fixture",
            },
            "created_at": datetime.fromisoformat(training["ended_at"]),
            "status": "EXPERIMENTAL",
            "release_scope": "NONE",
        }
    )
    registry.register(record, path)
    registry.transition(record.key, "REJECTED", "Synthetic train smoke; no independent field gate")
    catalog["runs"].append(
        {
            "model": record.key,
            "status": "COMPLETED_SYNTHETIC_FIXTURE",
            "timesteps": 1,
            "unit": "epochs",
            "seed": 2026,
            "progress": 1,
            "checkpoints": 2,
            "loss": losses(run / "ultralytics/results.csv"),
            "validation": "Field evaluation BLOCKED",
            "error": None,
        }
    )
    (root / "training.json").write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    print("Registered two unevaluated control smoke actors and one rejected vision fixture")


if __name__ == "__main__":
    main()

"""Archive known local vision artifacts without deserializing their executable checkpoints."""

import csv
import json
from datetime import datetime
from pathlib import Path

from traffic_core.model_registry import CentralModelRegistry, RegisteredModel, sha256


def main() -> None:
    root = Path("artifacts/rohai-ai-2.0")
    stage = root / "stage3"
    registry = CentralModelRegistry(stage / "registry")
    base = json.loads(Path("configs/models/yolo26n.json").read_text(encoding="utf-8"))
    record = RegisteredModel(
        model_id=base["model_id"],
        version=base["model_version"],
        kind="VISION",
        architecture=base["architecture"],
        parent_model="Upstream pretrained COCO",
        dataset_version=base["dataset_version"],
        source_code_version="Upstream code provenance unavailable",
        training_config={"status": "EXTERNAL_PRETRAINED; no local training configuration"},
        training_results={
            "status": "EXTERNAL_PRETRAINED; upstream results not independently reproduced"
        },
        evaluation_results={
            "field_evaluation": "BLOCKED: independent licensed ground truth missing"
        },
        artifact_sha256=base["weights_hash"],
        artifact_format="archived-trusted-checkpoint",
        compatibility={
            "registry": "rohai-registry-2",
            "architecture": "YOLO26n",
            "classes": "80 COCO",
        },
        provenance={
            "manifest_sha256": sha256(Path("configs/models/yolo26n.json")),
            "acquisition_evidence": "NOT AVAILABLE; pinned existing local baseline",
        },
        license_information="Ultralytics AGPL-3.0 / Enterprise; verify intended deployment rights",
        created_at=datetime.fromisoformat(base["created_at"]),
    )
    registry.register(record, Path("datasets/models/yolo26n.pt"))
    summary = json.loads((root / "fixture-training-summary.json").read_text(encoding="utf-8"))
    run = Path(summary["run"])
    training = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
    comparison = json.loads((root / "fixture-comparison.json").read_text(encoding="utf-8"))
    record = RegisteredModel(
        model_id="rohai-yolo26n-fixture-NOT-FIELD",
        version="smoke-1epoch",
        kind="VISION",
        architecture="YOLO26n",
        parent_model=base["model_id"],
        dataset_version=training["dataset_version"],
        source_code_version=training["code_sha256"],
        training_config=training["training_config"],
        training_results={
            "status": training["status"],
            "field_model": False,
            "epochs": 1,
            "artifacts": training["artifacts"],
        },
        evaluation_results={
            "report_sha256": sha256(root / "fixture-comparison.json"),
            "comparison": comparison,
            "gates": {
                "independent_field_validation": False,
                "queue_ground_truth": False,
                "field_dataset": False,
            },
        },
        artifact_sha256=summary["checkpoint_sha256"],
        artifact_format="archived-trusted-checkpoint",
        compatibility={"registry": "rohai-registry-2", "architecture": "YOLO26n", "classes": "car"},
        provenance={
            "purpose": "synthetic_fixture",
            "training_manifest_sha256": sha256(run / "manifest.json"),
            "dataset_sha256": training["dataset_hash"],
            "base_weights_sha256": training["base_weights_sha256"],
        },
        license_information="Owned synthetic fixture; Ultralytics AGPL-3.0 / Enterprise",
        created_at=datetime.fromisoformat(training["ended_at"]),
    )
    registry.register(record, run / "ultralytics/weights/best.pt")
    registry.transition(
        record.key,
        "REJECTED",
        "Synthetic fixture is not a field model; independent Vision gates blocked",
    )
    catalog_path = stage / "training.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    with (run / "ultralytics/results.csv").open(encoding="utf-8") as stream:
        losses = [
            {key.strip(): value.strip() for key, value in row.items()}
            for row in csv.DictReader(stream)
        ]
    catalog["runs"].append(
        {
            "model": record.key,
            "status": "COMPLETED_SYNTHETIC_FIXTURE",
            "timesteps": 1,
            "progress": 1,
            "seed": 2026,
            "checkpoints": 2,
            "loss": losses,
            "validation": "Independent field evaluation BLOCKED; fixture heldout mAP=0",
            "error": None,
            "unit": "epochs",
        }
    )
    # Catalog is a mutable derived UI view; model/artifact/protocol evidence stays immutable.
    catalog_path.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    print("Two vision artifacts archived; no pickle loaded; field release blocked")


if __name__ == "__main__":
    main()

"""Same held-out smoke protocol for baseline and actual fine-tuned fixture weights."""

import json
from datetime import UTC, datetime
from pathlib import Path

from traffic_core.models import ObjectKind
from traffic_vision.dataset.files import digest_file, write_new_json
from traffic_vision.dataset.schema import Split
from traffic_vision.evaluation.vision import finish_report, run_model, run_pretrained
from traffic_vision.manifest import ModelManifest
from traffic_vision.settings import VisionSettings


def main() -> None:
    root = Path("artifacts/rohai-ai-2.0")
    summary = json.loads((root / "fixture-training-summary.json").read_text(encoding="utf-8"))
    weights = Path(summary["run"]) / "ultralytics/weights/best.pt"
    manifest = ModelManifest.load(Path("configs/models/yolo26n.json")).model_copy(
        update={
            "model_id": "rohai-yolo26n-fixture-NOT-FIELD",
            "model_version": "smoke-1epoch",
            "weights_hash": digest_file(weights),
            "dataset_version": "fixture-v1",
            "class_names": (ObjectKind.CAR.value,),
            "class_id_mapping": {0: ObjectKind.CAR.value},
            "domain_mapping": {ObjectKind.CAR.value: ObjectKind.CAR},
            "created_at": datetime.now(UTC),
            "training_commit": "See hashed training manifest; dirty tree recorded",
        }
    )
    manifest_path = root / "fixture-model-manifest-v2.json"
    if manifest_path.exists():
        existing = ModelManifest.load(manifest_path)
        if existing.weights_hash != manifest.weights_hash or existing.class_names != (
            ObjectKind.CAR.value,
        ):
            raise ValueError("Fixture manifest differs from actual checkpoint")
    else:
        write_new_json(manifest_path, manifest.model_dump(mode="json"))
    settings = VisionSettings(device="cpu", image_size=640)
    baseline, _ = run_pretrained(root / "fixture-release", settings=settings)
    candidate, _ = run_model(
        root / "fixture-release",
        split=Split.TEST,
        settings=settings.model_copy(
            update={"model_path": weights, "manifest_path": manifest_path}
        ),
        run_kind="fine_tuned_synthetic_fixture",
    )
    write_new_json(root / "fixture-heldout-baseline.json", baseline)
    write_new_json(root / "fixture-heldout-candidate.json", candidate)
    result = finish_report(
        {
            "status": "SYNTHETIC_SMOKE_ONLY",
            "field_accuracy": None,
            "independent_field_test": False,
            "heldout_frames": 6,
            "inference_image_size": 640,
            "baseline": baseline["detection"],
            "candidate": candidate["detection"],
            "same_code": baseline["code_sha256"] == candidate["code_sha256"],
            "same_dataset": baseline["dataset_manifest_sha256"]
            == candidate["dataset_manifest_sha256"],
        }
    )
    write_new_json(root / "fixture-comparison.json", result)
    print(
        json.dumps(
            {
                "status": result["status"],
                "baseline": baseline["status"],
                "candidate": candidate["status"],
            }
        )
    )


if __name__ == "__main__":
    main()

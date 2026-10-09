"""Immutable, inventory-verified model bundles. Only trusted local checkpoints may be sealed."""

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator
from traffic_core.models import Model, ObjectKind

from traffic_vision.dataset.files import canonical_hash, digest_file, inside, write_new_json
from traffic_vision.dataset.release import open_release
from traffic_vision.dataset.schema import Digest
from traffic_vision.manifest import ModelManifest
from traffic_vision.training.config import TrainingConfig
from traffic_vision.training.io import read_verified


class SupportedInput(Model):
    format: Literal["BGR_uint8_HWC"] = "BGR_uint8_HWC"
    channels: Literal[3] = 3
    image_size: int = Field(ge=256, le=1536, multiple_of=32)


class ModelPackage(Model):
    compatibility_schema: Literal["rohai-model-package-1"] = "rohai-model-package-1"
    annotation_schema_version: Literal["1.0"] = "1.0"
    architecture: Literal["YOLO26n"] = "YOLO26n"
    task: Literal["detect"] = "detect"
    created_at: datetime
    dataset_version: str
    dataset_hash: Digest
    training_run: str
    training_manifest_sha256: Digest
    class_mapping: dict[int, ObjectKind]
    supported_input: SupportedInput
    expected_calibration: Literal[
        "validated_metric_road_plane_and_fresh_alignment", "validated_maket_geometry_and_alignment"
    ] = "validated_metric_road_plane_and_fresh_alignment"
    files: dict[str, Digest]
    package_sha256: Digest

    @model_validator(mode="after")
    def mapping(self) -> "ModelPackage":
        values = list(self.class_mapping.values())
        if (
            not values
            or ObjectKind.UNKNOWN in values
            or len(values) != len(set(values))
            or sorted(self.class_mapping) != list(range(len(values)))
        ):
            raise ValueError("Incompatible class mapping")
        if self.created_at.tzinfo is None:
            raise ValueError("Package timestamp requires timezone")
        return self


FILES = {"weights.pt", "model.json", "training.json", "metrics.json", "classes.json"}


def verify_package(root: Path, *, expected_hash: str | None = None) -> ModelPackage:
    package = ModelPackage.model_validate_json((root / "manifest.json").read_text(encoding="utf-8"))
    if canonical_hash(
        package.model_dump(mode="json", exclude={"package_sha256"})
    ) != package.package_sha256 or (expected_hash and expected_hash != package.package_sha256):
        raise ValueError("ModelPackage hash mismatch")
    if set(package.files) != FILES:
        raise ValueError("Incomplete package inventory")
    actual = {p.name for p in root.iterdir()}
    if actual != FILES | {"manifest.json"} or any(p.is_symlink() for p in root.iterdir()):
        raise ValueError("Package contains unexpected entries or symlinks")
    for name, expected in package.files.items():
        if digest_file(inside(root, name)) != expected:
            raise ValueError(f"Corrupt package file: {name}")
    adapter = ModelManifest.load(root / "model.json")
    names = {key: value.value for key, value in package.class_mapping.items()}
    if (
        adapter.architecture != package.architecture
        or adapter.class_id_mapping != names
        or adapter.domain_mapping != {v: ObjectKind(v) for v in names.values()}
        or adapter.weights_hash != package.files["weights.pt"]
        or adapter.input_size != package.supported_input.image_size
        or adapter.dataset_version != package.dataset_version
        or "detect" not in adapter.supported_features
    ):
        raise ValueError("Model adapter is incompatible with package")
    import json

    if json.loads((root / "classes.json").read_text(encoding="utf-8")) != {
        str(k): v for k, v in names.items()
    }:
        raise ValueError("Incompatible classes file")
    training = read_verified(root / "training.json")
    config = TrainingConfig.model_validate(training["training_config"])
    if config.purpose != "field":
        raise PermissionError("Synthetic fixture checkpoints cannot become deployment packages")
    if (
        training["report_sha256"] != package.training_manifest_sha256
        or training["run_id"] != package.training_run
        or training["dataset_hash"] != package.dataset_hash
        or training["status"] != "COMPLETED"
        or config.sha256 != training["config_sha256"]
        or config.dataset_hash != package.dataset_hash
        or config.dataset_version != package.dataset_version
        or config.image_size != package.supported_input.image_size
        or adapter.deployment_domain != config.deployment_domain
        or training["artifacts"].get("ultralytics/weights/best.pt") != adapter.weights_hash
    ):
        raise ValueError("Package training provenance mismatch")
    return package


def seal_package(run: Path, destination: Path) -> ModelPackage:
    training = read_verified(run / "manifest.json")
    if training["status"] != "COMPLETED":
        raise ValueError("Only a completed training run can be packaged")
    config = TrainingConfig.model_validate(training["training_config"])
    if config.purpose != "field":
        raise PermissionError("Synthetic fixture checkpoints cannot become deployment packages")
    if config.sha256 != training["config_sha256"]:
        raise ValueError("Training configuration hash mismatch")
    release = open_release(config.dataset_release)
    if release.manifest_sha256 != config.dataset_hash:
        raise ValueError("Wrong dataset release")
    release_root = (
        config.dataset_release if config.dataset_release.is_dir() else config.dataset_release.parent
    )
    if destination.resolve().is_relative_to(release_root.resolve()):
        raise PermissionError("Package cannot be written inside a sealed release")
    weights = run / "ultralytics" / "weights" / "best.pt"
    if digest_file(weights) != training["artifacts"]["ultralytics/weights/best.pt"]:
        raise ValueError("Checkpoint changed after training")
    # Validate trusted local checkpoint structure; never accept arbitrary uploaded pickle files.
    from traffic_vision.training.runner import load_yolo

    model = load_yolo(weights)
    names = {k: v.value for k, v in sorted(release.dataset.class_mapping.items())}
    if (
        dict(model.names) != names
        or model.task != "detect"
        or model.model.yaml.get("scale") != "n"
        or "26" not in str(model.model.yaml.get("yaml_file", ""))
    ):
        raise ValueError("Unsupported trained model architecture/classes")
    if destination.exists():
        raise FileExistsError("ModelPackage is immutable; use a new destination")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".rohai-package-", dir=destination.parent) as temp:
        staging = Path(temp) / "package"
        staging.mkdir()
        shutil.copyfile(weights, staging / "weights.pt")
        write_new_json(staging / "training.json", training)
        write_new_json(staging / "classes.json", {str(k): v for k, v in names.items()})
        csv = run / "ultralytics" / "results.csv"
        if digest_file(csv) != training["artifacts"]["ultralytics/results.csv"]:
            raise ValueError("Training metrics changed")
        write_new_json(
            staging / "metrics.json",
            {
                "scope": "TRAIN_VALIDATION_ONLY",
                "results_csv": csv.read_text(encoding="utf-8"),
                "test_evaluation": "external immutable report bound to package hash",
            },
        )
        adapter = ModelManifest(
            deployment_domain=config.deployment_domain,
            model_id="rohai-" + training["run_id"],
            model_version="1",
            architecture="YOLO26n",
            weights_hash=digest_file(staging / "weights.pt"),
            dataset_version=config.dataset_version,
            class_names=tuple(names.values()),
            class_id_mapping=names,
            domain_mapping={v: ObjectKind(v) for v in names.values()},
            input_size=config.image_size,
            training_commit=training["git_commit"],
            created_at=datetime.now(UTC),
            supported_features=("detect",),
            calibration_requirements=(
                "validated_maket_geometry_and_alignment"
                if config.deployment_domain == "PHYSICAL_MAKET"
                else "validated_metric_road_plane_and_fresh_alignment",
            ),
        )
        write_new_json(staging / "model.json", adapter.model_dump(mode="json"))
        values: dict[str, Any] = {
            "expected_calibration": adapter.calibration_requirements[0],
            "created_at": datetime.now(UTC),
            "dataset_version": config.dataset_version,
            "dataset_hash": config.dataset_hash,
            "training_run": training["run_id"],
            "training_manifest_sha256": training["report_sha256"],
            "class_mapping": names,
            "supported_input": {"image_size": config.image_size},
            "files": {name: digest_file(staging / name) for name in FILES},
            "package_sha256": "0" * 64,
        }
        package = ModelPackage.model_validate(values)
        package = package.model_copy(
            update={
                "package_sha256": canonical_hash(
                    package.model_dump(mode="json", exclude={"package_sha256"})
                )
            }
        )
        write_new_json(staging / "manifest.json", package.model_dump(mode="json"))
        verify_package(staging)
        staging.rename(destination)
    return package

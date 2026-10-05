"""Validated model metadata; model IDs are not domain class IDs."""

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from traffic_core.models import Model, ObjectKind


class ModelManifest(Model):
    deployment_domain: Literal["UNSPECIFIED", "PHYSICAL_MAKET", "REAL_ROAD"] = "UNSPECIFIED"
    model_id: str = Field(min_length=1)
    model_version: str = Field(min_length=1)
    architecture: str = Field(min_length=1)
    weights_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    dataset_version: str = Field(min_length=1)
    class_names: tuple[str, ...]
    class_id_mapping: dict[int, str]
    domain_mapping: dict[str, ObjectKind]
    input_size: int = Field(gt=0)
    training_commit: str
    created_at: datetime
    supported_features: tuple[str, ...]
    calibration_requirements: tuple[str, ...]

    @model_validator(mode="after")
    def complete(self) -> "ModelManifest":
        if not self.class_names or len(set(self.class_names)) != len(self.class_names):
            raise ValueError("Class names must be nonempty and unique")
        if set(self.class_id_mapping) != set(range(len(self.class_names))):
            raise ValueError("Class IDs must be complete and contiguous")
        if (
            tuple(self.class_id_mapping[i] for i in range(len(self.class_names)))
            != self.class_names
        ):
            raise ValueError("Class order differs from class_id_mapping")
        if set(self.domain_mapping) != set(self.class_names):
            raise ValueError("Every model class requires an explicit domain mapping")
        return self

    def validate_model(self, names: dict[int, str], weights: Path, input_size: int) -> None:
        if names != self.class_id_mapping:
            raise ValueError("Model class names/IDs disagree with manifest")
        if input_size != self.input_size:
            raise ValueError("Input size disagrees with manifest")
        with weights.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != self.weights_hash:
            raise ValueError("Model weights hash disagrees with manifest")
        if "detect" not in self.supported_features:
            raise ValueError("Model does not declare detection support")

    @classmethod
    def load(cls, path: Path) -> "ModelManifest":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

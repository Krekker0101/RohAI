"""Closed configuration: arbitrary Ultralytics overrides cannot inject held-out data."""

from pathlib import Path
from typing import Literal

from pydantic import Field
from traffic_core.models import Model

from traffic_vision.dataset.files import canonical_hash
from traffic_vision.dataset.schema import Digest, Identifier


class Augmentation(Model):
    hsv_h: float = Field(default=0.015, ge=0, le=1)
    hsv_s: float = Field(default=0.3, ge=0, le=1)
    hsv_v: float = Field(default=0.2, ge=0, le=1)
    degrees: float = Field(default=0, ge=0, le=180)
    translate: float = Field(default=0.05, ge=0, le=1)
    scale: float = Field(default=0.2, ge=0, le=1)
    shear: float = Field(default=0, ge=0, le=180)
    perspective: float = Field(default=0, ge=0, le=0.001)
    flipud: float = Field(default=0, ge=0, le=1)
    fliplr: float = Field(default=0.5, ge=0, le=1)
    mosaic: float = Field(default=0, ge=0, le=1)
    mixup: float = Field(default=0, ge=0, le=1)
    cutmix: float = Field(default=0, ge=0, le=1)
    copy_paste: float = Field(default=0, ge=0, le=1)
    close_mosaic: int = Field(default=0, ge=0)


class TrainingConfig(Model):
    schema_version: Literal["rohai-training-1"] = "rohai-training-1"
    experiment: Identifier
    base_model: Path = Field(default_factory=lambda: Path("datasets/models/yolo26n.pt"))
    base_manifest: Path = Field(default_factory=lambda: Path("configs/models/yolo26n.json"))
    dataset_release: Path
    dataset_version: str = Field(min_length=1)
    dataset_hash: Digest
    baseline: Path
    seed: int = Field(default=2026, ge=0, le=2147483647)
    image_size: int = Field(default=640, ge=256, le=1536, multiple_of=32)
    epochs: int = Field(default=30, ge=1, le=1000)
    batch: int = Field(default=8, ge=1, le=1024)
    optimizer: Literal["SGD", "Adam", "AdamW"] = "AdamW"
    lr0: float = Field(default=0.0001, gt=0, le=1)
    lrf: float = Field(default=0.1, gt=0, le=1)
    momentum: float = Field(default=0.937, ge=0, lt=1)
    weight_decay: float = Field(default=0.0005, ge=0, le=1)
    warmup_epochs: float = Field(default=3, ge=0)
    cos_lr: bool = False
    augmentation: Augmentation = Field(default_factory=Augmentation)
    patience: int = Field(default=10, ge=1)
    device: str = Field(default="cpu", pattern=r"^(cpu|mps|[0-9]+)$")
    workers: int = Field(default=0, ge=0, le=32)
    save_period: int = Field(default=1, ge=1)
    freeze: int = Field(default=0, ge=0, le=23)
    amp: bool = False
    deterministic: Literal[True] = True

    @property
    def sha256(self) -> str:
        return canonical_hash(self.model_dump(mode="json"))

    @classmethod
    def load(cls, path: Path) -> "TrainingConfig":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

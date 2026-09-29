"""Ultralytics adapter; optimization, optimizer state and resume belong to Ultralytics."""

import importlib.metadata
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import uuid
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from traffic_vision.dataset.files import digest_file, inside, write_new_json
from traffic_vision.dataset.release import open_release, training_view
from traffic_vision.dataset.schema import Layer, Split
from traffic_vision.dataset.validation import read_annotations, validate_dataset
from traffic_vision.evaluation.vision import code_fingerprint, finish_report
from traffic_vision.manifest import ModelManifest
from traffic_vision.training.config import TrainingConfig
from traffic_vision.training.io import atomic_json, exclusive, read_verified


def environment() -> dict[str, Any]:
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "dependencies": dict(
            sorted(
                (d.metadata["Name"], d.version)
                for d in importlib.metadata.distributions()
                if d.metadata["Name"]
            )
        ),
    }


def git_state() -> dict[str, Any]:
    def git(*args: str) -> str:
        return subprocess.check_output(["git", *args], text=True).strip()

    return {
        "git_commit": git("rev-parse", "HEAD"),
        "dirty_tree": bool(git("status", "--porcelain", "--untracked-files=normal")),
    }


def preflight(config: TrainingConfig) -> dict[str, Any]:
    release = open_release(config.dataset_release)
    root = (
        config.dataset_release if config.dataset_release.is_dir() else config.dataset_release.parent
    )
    if (
        release.manifest_sha256 != config.dataset_hash
        or release.dataset.version != config.dataset_version
    ):
        raise ValueError("Dataset version/hash differs from frozen training configuration")
    validation = validate_dataset(release.dataset, root)
    if not validation.valid or validation.near_duplicate_candidates:
        raise ValueError("Invalid dataset or unresolved leakage review")
    for split in (Split.TRAIN, Split.VALIDATION):
        if not training_view(config.dataset_release, split, config.baseline):
            raise ValueError(f"Empty {split}")
    base = ModelManifest.load(config.base_manifest)
    if base.architecture != "YOLO26n" or digest_file(config.base_model) != base.weights_hash:
        raise ValueError("Unsupported base model or corrupt weights")
    pinned = ModelManifest.load(Path("configs/models/yolo26n.json"))
    if base != pinned:
        raise ValueError("Initial experiment matrix must use the pinned pretrained baseline")
    if not set(release.dataset.class_mapping.values()).issubset(set(base.domain_mapping.values())):
        raise ValueError("Dataset contains classes unsupported by baseline")
    return {
        "status": "READY",
        "dataset_hash": release.manifest_sha256,
        "baseline_sha256": read_verified(config.baseline)["report_sha256"],
        "base_weights_sha256": base.weights_hash,
        "config_sha256": config.sha256,
    }


def export_yolo(config: TrainingConfig, destination: Path) -> dict[str, Any]:
    """Only fully exhaustive detection frames; never convert ignored boxes into negatives."""
    release = open_release(config.dataset_release)
    root = (
        config.dataset_release if config.dataset_release.is_dir() else config.dataset_release.parent
    )
    if destination.resolve().is_relative_to(root.resolve()):
        raise PermissionError("Training exports cannot modify an immutable release")
    if destination.exists():
        raise FileExistsError("Training export destination must be new")
    annotations = read_annotations(release.dataset, root)
    sources = {s.source_id: s for s in release.dataset.sources}
    inventory: dict[str, Any] = {}
    for split, folder in ((Split.TRAIN, "train"), (Split.VALIDATION, "val")):
        frames = training_view(config.dataset_release, split, config.baseline)
        accepted: list[str] = []
        excluded: list[str] = []
        for frame in frames:
            ann = annotations[frame.frame_id]
            if (
                Layer.DETECTION not in ann.layers
                or set(ann.exhaustive_classes) != set(release.dataset.class_mapping)
                or any(obj.ignore for obj in ann.objects)
            ):
                excluded.append(frame.frame_id)
                continue
            image = destination / "images" / folder / (frame.frame_id + ".png")
            label = destination / "labels" / folder / (frame.frame_id + ".txt")
            image.parent.mkdir(parents=True, exist_ok=True)
            label.parent.mkdir(parents=True, exist_ok=True)
            # Decode/re-encode: source media extension is not assumed to be PNG.
            import cv2
            import numpy as np

            decoded = cv2.imdecode(
                np.frombuffer(inside(root, frame.image.path).read_bytes(), dtype=np.uint8),
                cv2.IMREAD_COLOR,
            )
            if decoded is None:
                raise ValueError("Invalid training image")
            ok, encoded = cv2.imencode(".png", decoded)
            if not ok:
                raise ValueError("Failed to encode training image")
            image.write_bytes(encoded.tobytes())
            source = sources[frame.source_id]
            rows = []
            for obj in ann.objects:
                box = obj.bbox
                values = (
                    (box.x1 + box.x2) / (2 * source.width),
                    (box.y1 + box.y2) / (2 * source.height),
                    (box.x2 - box.x1) / source.width,
                    (box.y2 - box.y1) / source.height,
                )
                rows.append(f"{obj.class_id} " + " ".join(f"{v:.12g}" for v in values))
            label.write_text("\n".join(rows) + ("\n" if rows else ""), encoding="utf-8")
            accepted.append(frame.frame_id)
        if not accepted:
            raise ValueError(f"No fully exhaustive detection frames in {split}")
        inventory[split.value] = {"included": accepted, "excluded": excluded}
    # JSON is valid YAML. No test/challenge/download key is ever emitted.
    write_new_json(
        destination / "data.yaml",
        {
            "path": str(destination.resolve()),
            "train": "images/train",
            "val": "images/val",
            "names": [v.value for _, v in sorted(release.dataset.class_mapping.items())],
        },
    )
    write_new_json(destination / "selection.json", inventory)
    return inventory


def load_yolo(path: Path) -> Any:
    os.environ["YOLO_AUTOINSTALL"] = "false"
    os.environ["YOLO_OFFLINE"] = "true"
    runtime = Path(".runtime/ultralytics-training").resolve()
    runtime.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YOLO_CONFIG_DIR", str(runtime))
    from ultralytics import YOLO  # type: ignore[attr-defined]
    from ultralytics.utils import SETTINGS

    SETTINGS.update(  # type: ignore[no-untyped-call]
        {
            key: False
            for key in (
                "sync",
                "clearml",
                "comet",
                "dvc",
                "mlflow",
                "raytune",
                "tensorboard",
                "wandb",
            )
        }
    )

    return YOLO(str(path), task="detect")


def train(config: TrainingConfig, runs: Path, *, resume: Path | None = None) -> dict[str, Any]:
    proof = preflight(config)  # Before any directory creation or model deserialization.
    release_root = (
        config.dataset_release if config.dataset_release.is_dir() else config.dataset_release.parent
    ).resolve()
    run = (
        resume.resolve()
        if resume
        else runs.resolve()
        / (datetime.now(UTC).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:12])
    )
    if run.is_relative_to(release_root):
        raise PermissionError("Training output cannot be inside a sealed dataset")
    if not resume:
        run.mkdir(parents=True, exist_ok=False)
    elif not run.is_dir():
        raise FileNotFoundError("Resume run does not exist")
    with exclusive(run):
        env = environment()
        code = code_fingerprint()
        if resume:
            manifest = read_verified(run / "manifest.json")
            if (
                manifest["config_sha256"] != config.sha256
                or manifest["environment"] != env
                or manifest["code_sha256"] != code
                or manifest["status"] == "COMPLETED"
            ):
                raise ValueError(
                    "Resume requires identical config/code/environment and incomplete run"
                )
            for name, expected in manifest["input_files"].items():
                if digest_file(inside(run, name)) != expected:
                    raise ValueError("Training input changed before resume")
            weights = run / "ultralytics" / "weights" / "last.pt"
            expected = manifest.get("artifacts", {}).get("ultralytics/weights/last.pt")
            if not expected or digest_file(weights) != expected:
                raise ValueError("No integrity-verified resumable last checkpoint")
        else:
            export_yolo(config, run / "data")
            shutil.copyfile(config.base_model, run / "base.pt")
            if digest_file(run / "base.pt") != proof["base_weights_sha256"]:
                raise ValueError("Base weights changed during copy")
            write_new_json(run / "config.json", config.model_dump(mode="json"))
            manifest = {
                **proof,
                **git_state(),
                "run_id": run.name,
                "started_at": datetime.now(UTC).isoformat(),
                "ended_at": None,
                "training_config": config.model_dump(mode="json"),
                "dataset_version": config.dataset_version,
                "environment": env,
                "code_sha256": code,
                "attempts": [],
                "input_files": {
                    str(p.relative_to(run)).replace("\\", "/"): digest_file(p)
                    for p in (run / "data").rglob("*")
                    if p.is_file()
                },
            }
            weights = run / "base.pt"
        options: dict[str, Any] = {
            "data": str(run / "data" / "data.yaml"),
            "project": str(run),
            "name": "ultralytics",
            "exist_ok": True,
            "imgsz": config.image_size,
            "seed": config.seed,
            "epochs": config.epochs,
            "batch": config.batch,
            "optimizer": config.optimizer,
            "lr0": config.lr0,
            "lrf": config.lrf,
            "momentum": config.momentum,
            "weight_decay": config.weight_decay,
            "warmup_epochs": config.warmup_epochs,
            "cos_lr": config.cos_lr,
            "patience": config.patience,
            "device": config.device,
            "workers": config.workers,
            "save": True,
            "save_period": config.save_period,
            "plots": True,
            "val": True,
            "split": "val",
            "deterministic": True,
            "amp": config.amp,
            "freeze": config.freeze,
            "cache": False,
            **config.augmentation.model_dump(),
        }
        manifest["ultralytics_arguments"] = options
        manifest["status"] = "RUNNING"
        manifest["ended_at"] = None
        manifest.pop("error", None)
        manifest["attempts"].append(
            {
                "started_at": datetime.now(UTC).isoformat(),
                "resume": bool(resume),
                "checkpoint_sha256": digest_file(weights),
            }
        )
        atomic_json(run / "manifest.json", finish_report(manifest))
        logger = logging.getLogger("ultralytics")
        handler = logging.FileHandler(run / "training.log", encoding="utf-8")
        logger.addHandler(handler)
        try:
            with (run / "console.log").open("a", encoding="utf-8") as log:
                with redirect_stdout(log), redirect_stderr(log):
                    model = load_yolo(weights)
                    import torch

                    accelerator = {
                        "torch": torch.__version__,
                        "cuda_runtime": torch.version.cuda,
                        "cudnn": torch.backends.cudnn.version(),  # type: ignore[no-untyped-call]
                        "cuda_devices": [
                            torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())
                        ],
                    }
                    if resume and manifest.get("accelerator") != accelerator:
                        raise ValueError("Accelerator environment changed before resume")
                    manifest["accelerator"] = accelerator
                    if resume and (
                        not model.ckpt
                        or model.ckpt.get("epoch", -1) < 0
                        or model.ckpt.get("optimizer") is None
                    ):
                        raise ValueError("Checkpoint has no optimizer/epoch; refuse silent restart")

                    def capture(trainer: Any) -> None:
                        manifest["resolved_ultralytics_config"] = json.loads(
                            json.dumps(vars(trainer.args), default=str, allow_nan=False)
                        )
                        manifest["artifacts"] = {
                            str(p.relative_to(run)).replace("\\", "/"): digest_file(p)
                            for p in (run / "ultralytics" / "weights").glob("*.pt")
                        }
                        atomic_json(run / "manifest.json", finish_report(manifest))

                    model.add_callback("on_train_start", capture)
                    model.add_callback("on_model_save", capture)
                    model.train(**options, resume=str(weights) if resume else False)
                    capture(model.trainer)
                    for required in ("weights/best.pt", "weights/last.pt", "results.csv"):
                        if not (run / "ultralytics" / required).is_file():
                            raise ValueError(f"Training did not produce {required}")
                    manifest["status"] = "COMPLETED"
        except BaseException as exc:
            manifest["status"] = "INTERRUPTED" if isinstance(exc, KeyboardInterrupt) else "FAILED"
            manifest["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            logger.removeHandler(handler)
            handler.close()
            manifest["ended_at"] = datetime.now(UTC).isoformat()
            manifest["artifacts"] = {
                str(p.relative_to(run)).replace("\\", "/"): digest_file(p)
                for p in run.rglob("*")
                if p.is_file() and p.name not in ("manifest.json", ".writer.lock")
            }
            atomic_json(run / "manifest.json", finish_report(manifest))
        return manifest

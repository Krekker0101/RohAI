"""Actual one-epoch fine-tuning smoke on owned synthetic rectangles, never a field model."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
from traffic_core.models import ObjectKind
from traffic_vision.contracts import BoundingBox
from traffic_vision.dataset.files import digest_file, write_new_json
from traffic_vision.dataset.release import seal_release
from traffic_vision.dataset.schema import (
    Asset,
    Conditions,
    DatasetSpec,
    FrameAnnotation,
    FrameRecord,
    Layer,
    ObjectAnnotation,
    SourceVideo,
    Split,
    SplitDefinition,
    UsageRights,
)
from traffic_vision.evaluation.vision import run_pretrained
from traffic_vision.settings import VisionSettings
from traffic_vision.training.config import TrainingConfig
from traffic_vision.training.package import seal_package
from traffic_vision.training.runner import train


def create_fixture(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=False)
    sources, frames = [], []
    assignments = {}

    def asset(name: str) -> Asset:
        return Asset(path=name, sha256=digest_file(root / name))

    for cohort, split in enumerate((Split.TRAIN, Split.VALIDATION, Split.TEST)):
        source = f"fixture-{cohort}"
        video = f"{source}.avi"
        writer = cv2.VideoWriter(str(root / video), cv2.VideoWriter.fourcc(*"FFV1"), 4, (256, 256))
        if not writer.isOpened():
            raise RuntimeError("Lossless fixture writer unavailable")
        try:
            for index in range(6):
                image = np.random.default_rng(cohort * 100 + index).integers(
                    0, 40, (256, 256, 3), dtype=np.uint8
                )
                x, y = 40 + index * 10, 70 + cohort * 35
                cv2.rectangle(image, (x, y), (x + 40, y + 24), (40, 90, 230), -1)
                writer.write(image)
                frame_id = f"{source}-{index}"
                image_name, label_name = f"{frame_id}.png", f"{frame_id}.json"
                if not cv2.imwrite(str(root / image_name), image):
                    raise RuntimeError("Fixture image write failed")
                annotation = FrameAnnotation(
                    frame_id=frame_id,
                    layers=(Layer.DETECTION,),
                    exhaustive_classes=(0,),
                    provenance="synthetic_fixture",
                    objects=(
                        ObjectAnnotation(
                            annotation_id="rectangle",
                            class_id=0,
                            domain_class=ObjectKind.CAR,
                            bbox=BoundingBox(x1=x, y1=y, x2=x + 41, y2=y + 25),
                        ),
                    ),
                )
                write_new_json(root / label_name, annotation.model_dump(mode="json"))
                frames.append(
                    FrameRecord(
                        frame_id=frame_id,
                        source_id=source,
                        frame_index=index,
                        timestamp=index / 4,
                        image=asset(image_name),
                        annotation=asset(label_name),
                    )
                )
        finally:
            writer.release()
        sources.append(
            SourceVideo(
                source_id=source,
                recording_id=source,
                camera_id=source,
                intersection_id=source,
                video=asset(video),
                captured_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=cohort),
                duration_seconds=1.5,
                fps=4,
                width=256,
                height=256,
                scene_domain="SYNTHETIC",
                conditions=Conditions(
                    day_night="unknown",
                    weather="unknown",
                    traffic_density="unknown",
                    occlusion="none",
                    camera_angle="unknown",
                    lighting="unknown",
                ),
                provenance="Owned generated rectangles; no road or toy-object accuracy evidence",
                usage="synthetic_fixture",
                pretrained_exposure="unknown",
                rights=UsageRights(
                    license_id="CC0-1.0",
                    owner="RohAI generated fixture",
                    source_reference="this script",
                    training_allowed=True,
                    privacy_reviewed=True,
                    reviewed_by="GENERATED_FIXTURE_ONLY",
                    evidence="Procedural pixels with no captured persons or external media",
                ),
            )
        )
        assignments[source] = split
    spec = DatasetSpec(
        dataset_id="ai20-smoke-rectangles",
        version="fixture-v1",
        created_at=datetime.now(UTC),
        purpose="synthetic_fixture",
        class_mapping={0: ObjectKind.CAR},
        sources=tuple(sources),
        frames=tuple(frames),
        split=SplitDefinition(
            assignments=assignments,
            group_by=("recording", "camera", "day", "intersection"),
            independence_exceptions="Synthetic smoke cohorts; no field generalization evidence",
        ),
    )
    path = root / "dataset.json"
    write_new_json(path, spec.model_dump(mode="json"))
    return path


def main() -> None:
    output = Path("artifacts/rohai-ai-2.0")
    output.mkdir(parents=True, exist_ok=True)
    spec = create_fixture(output / "fixture-draft")
    release = seal_release(spec, output / "fixture-release")
    baseline, predictions = run_pretrained(
        output / "fixture-release", settings=VisionSettings(device="cpu")
    )
    if predictions is None:
        raise RuntimeError("Fixture baseline unavailable")
    write_new_json(output / "fixture-baseline.json", baseline)
    config = TrainingConfig(
        purpose="synthetic_fixture",
        experiment="ai20-smoke-NOT-FIELD",
        dataset_release=output / "fixture-release",
        dataset_version=release.dataset.version,
        dataset_hash=release.manifest_sha256,
        baseline=output / "fixture-baseline.json",
        image_size=256,
        epochs=1,
        batch=2,
        device="cpu",
        freeze=20,
        warmup_epochs=0,
        patience=1,
    )
    write_new_json(output / "fixture-training-config.json", config.model_dump(mode="json"))
    result = train(config, output / "fixture-runs")
    run = output / "fixture-runs" / result["run_id"]
    try:
        seal_package(run, output / "FORBIDDEN-fixture-deployment")
    except PermissionError:
        deployment_blocked = True
    else:
        raise RuntimeError("Fixture escaped deployment gate")
    write_new_json(
        output / "fixture-training-summary.json",
        {
            "status": result["status"],
            "purpose": "SYNTHETIC_SMOKE_ONLY",
            "original_architecture": False,
            "base": "Ultralytics YOLO26n pretrained COCO",
            "field_accuracy": None,
            "deployment_blocked": deployment_blocked,
            "run": str(run),
            "dataset_hash": release.manifest_sha256,
            "checkpoint_sha256": digest_file(run / "ultralytics/weights/best.pt"),
        },
    )
    print(json.dumps({"run": str(run), "status": result["status"], "field_model": False}))


if __name__ == "__main__":
    main()

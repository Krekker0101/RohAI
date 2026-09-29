"""Copy-on-release, content-addressed verification, and training-only views."""

import json
import shutil
import tempfile
from pathlib import Path

from traffic_vision.dataset.files import canonical_hash, digest_file, inside, write_new_json
from traffic_vision.dataset.schema import Asset, DatasetSpec, FrameRecord, ReleaseManifest, Split
from traffic_vision.dataset.statistics import statistics
from traffic_vision.dataset.validation import validate_dataset


def assets(dataset: DatasetSpec) -> dict[str, str]:
    values: list[Asset] = [s.video for s in dataset.sources]
    values.extend(s.geometry for s in dataset.sources if s.geometry is not None)
    values.extend(s.alignment for s in dataset.sources if s.alignment is not None)
    values.extend(a for f in dataset.frames for a in (f.image, f.annotation))
    result: dict[str, str] = {}
    for asset in values:
        if asset.path in ("release.json", "release.sha256"):
            raise ValueError("Reserved release path")
        if asset.path in result and result[asset.path] != asset.sha256:
            raise ValueError("Conflicting asset hashes")
        result[asset.path] = asset.sha256
    return result


def seal_release(spec_path: Path, destination: Path) -> ReleaseManifest:
    dataset = DatasetSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    report = validate_dataset(dataset, spec_path.parent)
    if not report.valid:
        raise ValueError(report.model_dump_json())
    if destination.exists():
        raise FileExistsError("Dataset releases cannot be overwritten; publish a new version")
    files = assets(dataset)
    payload = {
        "format": "rohai-dataset-release-1",
        "dataset": dataset.model_dump(mode="json"),
        "files": files,
        "statistics": statistics(dataset, spec_path.parent),
    }
    manifest = ReleaseManifest.model_validate(
        {**payload, "manifest_sha256": canonical_hash(payload)}
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Temp is owned by this operation and cleanup remains under the chosen parent directory.
    with tempfile.TemporaryDirectory(prefix=".rohai-release-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "release"
        staging.mkdir()
        for relative, expected in files.items():
            target = inside(staging, relative)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(inside(spec_path.parent, relative), target)
            if digest_file(target) != expected:
                raise ValueError("Source changed while sealing release")
        write_new_json(staging / "release.json", manifest.model_dump(mode="json"))
        (staging / "release.sha256").write_text(
            digest_file(staging / "release.json") + "\n", encoding="ascii"
        )
        staging.rename(destination)
    return manifest


def open_release(path: Path) -> ReleaseManifest:
    path = path / "release.json" if path.is_dir() else path
    manifest = ReleaseManifest.model_validate_json(path.read_text(encoding="utf-8"))
    expected = path.with_name("release.sha256").read_text(encoding="ascii").strip()
    if (
        digest_file(path) != expected
        or canonical_hash(manifest.model_dump(mode="json", exclude={"manifest_sha256"}))
        != manifest.manifest_sha256
    ):
        raise ValueError("Release manifest changed after sealing")
    if manifest.files != assets(manifest.dataset):
        raise ValueError("Release file inventory is incomplete")
    for relative, digest in manifest.files.items():
        if digest_file(inside(path.parent, relative)) != digest:
            raise ValueError(f"Release asset changed: {relative}")
    return manifest


def select_frames(manifest: ReleaseManifest, split: Split) -> tuple[FrameRecord, ...]:
    return tuple(
        f
        for f in manifest.dataset.frames
        if manifest.dataset.split.assignments[f.source_id] == split
    )


def training_view(release_path: Path, split: Split, baseline_path: Path) -> tuple[FrameRecord, ...]:
    """Gate training access on an independently measured pinned pretrained baseline."""
    if split not in (Split.TRAIN, Split.VALIDATION):
        raise PermissionError("TEST and CHALLENGE are evaluation-only")
    manifest = open_release(release_path)
    if manifest.dataset.purpose != "field":
        raise PermissionError("Training requires a field baseline, not synthetic fixtures")
    report = json.loads(baseline_path.read_text(encoding="utf-8"))
    if (
        report.get("status") != "MEASURED"
        or report.get("split") != "TEST"
        or report.get("dataset_manifest_sha256") != manifest.manifest_sha256
        or report.get("run_kind") != "pretrained_yolo26n"
        or not report.get("independent_test")
    ):
        raise PermissionError(
            "Independent pretrained TEST baseline for this exact release is required"
        )
    from traffic_vision.manifest import ModelManifest

    current_model = ModelManifest.load(Path("configs/models/yolo26n.json"))
    detection = report.get("detection")
    if (
        not isinstance(detection, dict)
        or detection.get("ground_truth", 0) <= 0
        or detection.get("mAP50") is None
    ):
        raise PermissionError("Baseline has no measured detection result/model provenance")
    if (
        report.get("model_weights_sha256") != current_model.weights_hash
        or report.get("model_id") != current_model.model_id
    ):
        raise PermissionError("Baseline must measure the current pinned pretrained model")
    if report.get("model_manifest_sha256") != digest_file(Path("configs/models/yolo26n.json")):
        raise PermissionError("Baseline model mapping/metadata differs from current manifest")
    expected = report.get("report_sha256")
    if not expected or expected != canonical_hash(
        {k: v for k, v in report.items() if k != "report_sha256"}
    ):
        raise PermissionError("Baseline report integrity failure")
    return select_frames(manifest, split)


def export_training(
    release_path: Path, split: Split, baseline_path: Path, destination: Path
) -> None:
    frames = training_view(release_path, split, baseline_path)
    path = release_path / "release.json" if release_path.is_dir() else release_path
    if destination.resolve().is_relative_to(path.parent.resolve()):
        raise PermissionError("Never write training output inside a dataset release")
    if destination.exists():
        raise FileExistsError("Training export destination must be new")
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = open_release(path)
    with tempfile.TemporaryDirectory(
        prefix=".rohai-training-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary) / "training"
        staging.mkdir()
        for frame in frames:
            for asset in (frame.image, frame.annotation):
                target = inside(staging, asset.path)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(inside(path.parent, asset.path), target)
                if digest_file(target) != asset.sha256:
                    raise ValueError("Release changed while exporting training data")
        write_new_json(
            staging / "training-index.json",
            {
                "dataset_id": manifest.dataset.dataset_id,
                "version": manifest.dataset.version,
                "manifest_sha256": manifest.manifest_sha256,
                "class_mapping": {
                    str(k): v.value for k, v in manifest.dataset.class_mapping.items()
                },
                "annotation_schema_version": manifest.dataset.annotation_schema_version,
                "split": split.value,
                "frames": [f.model_dump(mode="json") for f in frames],
            },
        )
        staging.rename(destination)

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cv2
import numpy as np
import pytest
from pydantic import ValidationError
from traffic_core.models import Direction, ObjectKind
from traffic_vision.cli import main
from traffic_vision.contracts import BoundingBox
from traffic_vision.dataset.files import canonical_hash, digest_file
from traffic_vision.dataset.release import open_release, seal_release, training_view
from traffic_vision.dataset.schema import (
    Asset,
    Conditions,
    DatasetSpec,
    FeatureTruth,
    FrameAnnotation,
    FrameRecord,
    Layer,
    ObjectAnnotation,
    SequenceSpec,
    SourceVideo,
    Split,
    SplitDefinition,
)
from traffic_vision.dataset.splits import plan_split
from traffic_vision.dataset.statistics import CoverageRequirement, statistics
from traffic_vision.dataset.validation import validate_dataset, validate_path
from traffic_vision.evaluation.analytics import analytics_benchmark, propose_acceptance
from traffic_vision.evaluation.metrics import PredictionFrame
from traffic_vision.evaluation.vision import PredictionRun, finish_report, run_pretrained


def asset(root: Path, relative: str) -> Asset:
    return Asset(path=relative, sha256=digest_file(root / relative))


def make_dataset(root: Path) -> tuple[DatasetSpec, Path]:
    root.mkdir(parents=True, exist_ok=True)
    sources: list[SourceVideo] = []
    frames: list[FrameRecord] = []
    sequences: list[SequenceSpec] = []
    for index, _split in enumerate(Split):
        source_id = f"source{index}"
        video_name = f"video{index}.avi"
        writer = cv2.VideoWriter(
            str(root / video_name), cv2.VideoWriter.fourcc(*"MJPG"), 10, (64, 48)
        )
        assert writer.isOpened()
        for tick in range(4):
            pixels = np.random.default_rng(index * 100 + tick).integers(
                0, 256, (48, 64, 3), dtype=np.uint8
            )
            writer.write(pixels)
            if tick == 3:
                continue
            frame_id = f"{source_id}-{tick}"
            image_name, annotation_name = f"{frame_id}.png", f"{frame_id}.json"
            assert cv2.imwrite(str(root / image_name), pixels)
            ann = FrameAnnotation(
                frame_id=frame_id,
                layers=(Layer.DETECTION, Layer.TRACKING, Layer.ANALYTICS),
                exhaustive_classes=(0, 1),
                provenance="synthetic_fixture",
                objects=(
                    ObjectAnnotation(
                        annotation_id=f"a{tick}",
                        bbox=BoundingBox(x1=5, y1=5, x2=20, y2=20),
                        class_id=0,
                        domain_class=ObjectKind.CAR,
                        track_id="car1",
                        approach=Direction.NORTH,
                        lane_id="n1",
                        stopped=True,
                        queue_member=True,
                    ),
                ),
                traffic=(
                    FeatureTruth(
                        feature="queue_count",
                        approach=Direction.NORTH,
                        value=1,
                        unit="vehicle",
                        method="manual_count",
                    ),
                ),
            )
            (root / annotation_name).write_text(ann.model_dump_json(), encoding="utf-8")
            frames.append(
                FrameRecord(
                    frame_id=frame_id,
                    source_id=source_id,
                    sequence_id=source_id,
                    frame_index=tick,
                    timestamp=tick / 10,
                    image=asset(root, image_name),
                    annotation=asset(root, annotation_name),
                )
            )
        writer.release()
        sources.append(
            SourceVideo(
                source_id=source_id,
                recording_id=f"record{index}",
                camera_id=f"camera{index}",
                intersection_id=f"intersection{index}",
                video=asset(root, video_name),
                captured_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=index),
                duration_seconds=0.4,
                fps=10,
                width=64,
                height=48,
                conditions=Conditions(
                    day_night="day",
                    weather="dry",
                    traffic_density="low",
                    occlusion="none",
                    camera_angle="oblique",
                    lighting="good",
                ),
                provenance="Generated test fixture, not road footage",
                usage="synthetic_fixture",
                pretrained_exposure="unknown",
            )
        )
        sequences.append(
            SequenceSpec(
                sequence_id=source_id,
                source_id=source_id,
                start_frame=0,
                end_frame=2,
                layers=(Layer.DETECTION, Layer.TRACKING),
            )
        )
    spec = DatasetSpec(
        dataset_id="fixture",
        version="v1",
        created_at=datetime.now(UTC),
        purpose="synthetic_fixture",
        class_mapping={0: ObjectKind.CAR, 1: ObjectKind.PEDESTRIAN},
        sources=tuple(sources),
        frames=tuple(frames),
        sequences=tuple(sequences),
        split=SplitDefinition(
            assignments={s.source_id: split for s, split in zip(sources, Split, strict=True)}
        ),
    )
    path = root / "dataset.json"
    path.write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    return spec, path


@pytest.fixture
def dataset(tmp_path: Path) -> tuple[DatasetSpec, Path]:
    return make_dataset(tmp_path / "draft")


def errors(spec: DatasetSpec, root: Path) -> set[str]:
    return {
        issue.code for issue in validate_dataset(spec, root).issues if issue.severity == "error"
    }


def replace_annotation(
    spec: DatasetSpec, root: Path, index: int, value: dict[str, object]
) -> DatasetSpec:
    frame = spec.frames[index]
    (root / frame.annotation.path).write_text(json.dumps(value), encoding="utf-8")
    frames = list(spec.frames)
    frames[index] = frame.model_copy(update={"annotation": asset(root, frame.annotation.path)})
    return spec.model_copy(update={"frames": tuple(frames)})


def test_valid_release_and_statistics(dataset: tuple[DatasetSpec, Path], tmp_path: Path) -> None:
    spec, path = dataset
    assert validate_path(path).valid
    sealed = seal_release(path, tmp_path / "releases" / "v1")
    assert open_release(tmp_path / "releases" / "v1").manifest_sha256 == sealed.manifest_sha256
    result = statistics(spec, path.parent, (CoverageRequirement(filters={"day_night": "night"}),))
    assert result["frames"] == 12
    assert len(result["coverage_gaps"]) == 1  # type: ignore[arg-type]
    assert result["empty_splits"] == []
    with pytest.raises(FileExistsError):
        seal_release(path, tmp_path / "releases" / "v1")


@pytest.mark.parametrize("kind", ["image", "annotation", "video"])
def test_missing_assets(dataset: tuple[DatasetSpec, Path], kind: str) -> None:
    spec, path = dataset
    target = spec.sources[0].video if kind == "video" else getattr(spec.frames[0], kind)
    (path.parent / target.path).unlink()
    assert f"missing_{kind}" in errors(spec, path.parent)


def test_corrupt_image_even_with_correct_hash(dataset: tuple[DatasetSpec, Path]) -> None:
    spec, path = dataset
    frame = spec.frames[0]
    (path.parent / frame.image.path).write_bytes(b"not an image")
    frame = frame.model_copy(update={"image": asset(path.parent, frame.image.path)})
    spec = spec.model_copy(update={"frames": (frame, *spec.frames[1:])})
    assert "corrupt_image" in errors(spec, path.parent)


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("bbox", "invalid_bbox"),
        ("class", "unknown_or_out_of_range_class"),
        ("duplicate", "duplicate_label"),
        ("track", "impossible_track_id"),
        ("prediction", "prediction_as_ground_truth"),
        ("missing_analytics", "missing_analytics"),
    ],
)
def test_annotation_validation(
    dataset: tuple[DatasetSpec, Path], mutation: str, expected: str
) -> None:
    spec, path = dataset
    value = json.loads((path.parent / spec.frames[0].annotation.path).read_text())
    if mutation == "bbox":
        value["objects"][0]["bbox"]["x2"] = 1000
    elif mutation == "class":
        value["objects"][0]["class_id"] = 99
    elif mutation == "duplicate":
        value["objects"].append(value["objects"][0])
    elif mutation == "track":
        value["objects"][0]["track_id"] = None
    elif mutation == "prediction":
        value["provenance"] = "model_predictions"
    else:
        value["traffic"] = []
    changed = replace_annotation(spec, path.parent, 0, value)
    assert expected in errors(changed, path.parent)


def test_unknown_class_and_negative_box_are_schema_errors(
    dataset: tuple[DatasetSpec, Path],
) -> None:
    spec, path = dataset
    value = json.loads((path.parent / spec.frames[0].annotation.path).read_text())
    value["objects"][0]["domain_class"] = "spaceship"
    value["objects"][0]["bbox"]["x1"] = -1
    assert "invalid_annotation" in errors(
        replace_annotation(spec, path.parent, 0, value), path.parent
    )


def test_missing_sequence_frame(dataset: tuple[DatasetSpec, Path]) -> None:
    spec, path = dataset
    changed = spec.model_copy(update={"frames": spec.frames[1:]})
    assert "broken_sequence" in errors(changed, path.parent)


@pytest.mark.parametrize("dimension", ["recording_id", "camera_id", "captured_at", "video"])
def test_source_day_camera_and_content_leakage(
    dataset: tuple[DatasetSpec, Path], dimension: str
) -> None:
    spec, path = dataset
    sources = list(spec.sources)
    sources[1] = sources[1].model_copy(update={dimension: getattr(sources[0], dimension)})
    changed = spec.model_copy(update={"sources": tuple(sources)})
    assert "split_overlap" in errors(changed, path.parent)


def test_duplicate_pixels_detected_across_split(dataset: tuple[DatasetSpec, Path]) -> None:
    spec, path = dataset
    frames = list(spec.frames)
    frames[3] = frames[3].model_copy(update={"image": frames[0].image})
    assert "image_leakage" in errors(spec.model_copy(update={"frames": tuple(frames)}), path.parent)


def test_split_planner_whole_cohorts_and_challenge(dataset: tuple[DatasetSpec, Path]) -> None:
    spec, _ = dataset
    first = plan_split(spec, challenge_sources=("source3",))
    assert first == plan_split(spec, challenge_sources=("source3",))
    assert set(first.assignments.values()) == set(Split)
    sources = tuple(s.model_copy(update={"camera_id": "same"}) for s in spec.sources)
    with pytest.raises(ValueError, match="cohorts"):
        plan_split(spec.model_copy(update={"sources": sources}))
    with pytest.raises(ValueError, match="exception"):
        SplitDefinition(group_by=("recording",))


@pytest.mark.parametrize("mutation", ["annotation", "manifest", "image"])
def test_sealed_release_tampering_detected(
    dataset: tuple[DatasetSpec, Path], tmp_path: Path, mutation: str
) -> None:
    spec, path = dataset
    root = tmp_path / "sealed"
    seal_release(path, root)
    relative = "release.json" if mutation == "manifest" else getattr(spec.frames[6], mutation).path
    with (root / relative).open("ab") as stream:
        stream.write(b" ")
    with pytest.raises(ValueError, match="changed"):
        open_release(root)


def test_training_gate_protects_holdouts_and_requires_baseline(
    dataset: tuple[DatasetSpec, Path], tmp_path: Path
) -> None:
    _, path = dataset
    release = tmp_path / "sealed"
    seal_release(path, release)
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"status": "BLOCKED"}))
    for split in (Split.TEST, Split.CHALLENGE):
        with pytest.raises(PermissionError, match="evaluation-only"):
            training_view(release, split, baseline)
    with pytest.raises(PermissionError, match="baseline"):
        training_view(release, Split.TRAIN, baseline)


def test_fixture_cannot_masquerade_as_field(dataset: tuple[DatasetSpec, Path]) -> None:
    spec, path = dataset
    assert "synthetic_as_field" in errors(spec.model_copy(update={"purpose": "field"}), path.parent)


def test_relative_path_and_units() -> None:
    for name in ("../outside.png", "C:/secret.png", "/abs.png", "images\\frame.png"):
        with pytest.raises(ValidationError):
            Asset(path=name, sha256="a" * 64)
    with pytest.raises(ValidationError, match="unit"):
        FeatureTruth(
            feature="queue_length_m", approach=Direction.NORTH, value=10, unit="px", method="survey"
        )


def test_missing_baseline_is_explicit_and_no_thresholds(tmp_path: Path) -> None:
    report, run = run_pretrained(tmp_path / "absent-release")
    assert report["status"] == "BLOCKED"
    assert report["detection"] is None and run is None
    assert not report["fine_tuning_allowed"]
    assert propose_acceptance(report, {})["thresholds"] is None


def test_cli_fails_bad_data_and_writes_blocked_report(tmp_path: Path) -> None:
    assert main(["dataset", "validate", str(tmp_path / "missing.json")]) == 2
    output = tmp_path / "reports" / "blocked.json"
    assert (
        main(
            [
                "benchmark",
                "vision",
                "--release",
                str(tmp_path / "data" / "release.json"),
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert json.loads(output.read_text())["status"] == "BLOCKED"


def test_downstream_unknown_is_coverage_zero_not_zero_error(
    dataset: tuple[DatasetSpec, Path], tmp_path: Path
) -> None:
    _, path = dataset
    release = seal_release(path, tmp_path / "sealed")
    frames = [
        f
        for f in release.dataset.frames
        if release.dataset.split.assignments[f.source_id] == Split.TEST
    ]
    payload = {
        "schema_version": "1.0",
        "dataset_manifest_sha256": release.manifest_sha256,
        "model_weights_sha256": "a" * 64,
        "split": "TEST",
        "run_kind": "synthetic_fixture",
        "frames": [
            PredictionFrame(frame_id=f.frame_id, detections=()).model_dump(mode="json")
            for f in frames
        ],
        "settings": {},
    }
    run = PredictionRun.model_validate({**payload, "run_sha256": canonical_hash(payload)})
    predictions = tmp_path / "predictions.json"
    predictions.write_text(run.model_dump_json(), encoding="utf-8")
    report = analytics_benchmark(tmp_path / "sealed", predictions)
    queue = report["control_features"]["queue_count"]
    assert queue["coverage"] == 0
    assert queue["mae"] is None
    assert report["control_features"]["traffic_score"]["status"] == "NOT_ANNOTATED"
    assert not report["independent_test"]


def test_acceptance_uses_measured_baseline_and_explicit_safety_budget() -> None:
    base = {"status": "MEASURED", "independent_test": True, "dataset_manifest_sha256": "a"}
    from traffic_core.settings import Timing

    analytics = {
        **base,
        "timing": Timing().model_dump(),
        "control_sensitivity": [{"absolute_green_error_s": 2}],
    }
    assert propose_acceptance(base, analytics)["thresholds"] is None
    result = propose_acceptance(base, analytics, max_green_error_seconds=1.8)
    assert result["thresholds"]["absolute_phase_score_error"] == 1
    assert not result["baseline_within_proposed_green_budget"]
    assert finish_report(base)["report_sha256"]


def test_unlabeled_context_is_explicit_not_an_empty_negative() -> None:
    ann = FrameAnnotation(frame_id="context", provenance="context_only")
    assert not ann.layers and not ann.exhaustive_classes
    with pytest.raises(ValidationError, match="Context"):
        FrameAnnotation(frame_id="context", provenance="context_only", layers=(Layer.DETECTION,))


def test_generated_schemas_match_executable_contract() -> None:
    from traffic_vision.dataset.schema import ReleaseManifest

    for name, model in (
        ("dataset", DatasetSpec),
        ("annotation", FrameAnnotation),
        ("release", ReleaseManifest),
    ):
        assert (
            json.loads(Path(f"datasets/schema/{name}-1.0.json").read_text())
            == model.model_json_schema()
        )


def test_corrupt_video_with_matching_hash(dataset: tuple[DatasetSpec, Path]) -> None:
    spec, path = dataset
    source = spec.sources[0]
    (path.parent / source.video.path).write_bytes(b"not a video")
    changed = source.model_copy(update={"video": asset(path.parent, source.video.path)})
    assert "corrupt_or_truncated_video" in errors(
        spec.model_copy(update={"sources": (changed, *spec.sources[1:])}), path.parent
    )


def test_training_export_contains_only_allowed_assets(
    dataset: tuple[DatasetSpec, Path], tmp_path: Path
) -> None:
    # Metadata fixture exercises the gate; this constructed report is NOT a measured baseline.
    from traffic_vision.dataset.release import export_training
    from traffic_vision.manifest import ModelManifest

    spec, path = dataset
    for index, frame in enumerate(spec.frames):
        annotation = json.loads((path.parent / frame.annotation.path).read_text())
        annotation.update(provenance="human", reviewed_by="unit-test-fixture")
        spec = replace_annotation(spec, path.parent, index, annotation)
    spec = spec.model_copy(
        update={
            "purpose": "field",
            "sources": tuple(
                s.model_copy(
                    update={"usage": "field", "pretrained_exposure": "audited_no_known_overlap"}
                )
                for s in spec.sources
            ),
        }
    )
    path.write_text(spec.model_dump_json(), encoding="utf-8")
    release = seal_release(path, tmp_path / "sealed")
    model = ModelManifest.load(Path("configs/models/yolo26n.json"))
    report = finish_report(
        {
            "status": "MEASURED",
            "split": "TEST",
            "dataset_manifest_sha256": release.manifest_sha256,
            "run_kind": "pretrained_yolo26n",
            "independent_test": True,
            "detection": {"ground_truth": 3, "mAP50": 0},
            "model_id": model.model_id,
            "model_weights_sha256": model.weights_hash,
            "model_manifest_sha256": digest_file(Path("configs/models/yolo26n.json")),
        }
    )
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(report), encoding="utf-8")
    export_training(tmp_path / "sealed", Split.TRAIN, baseline, tmp_path / "training")
    for frame in spec.frames:
        exists = (tmp_path / "training" / frame.image.path).exists()
        assert exists == (spec.split.assignments[frame.source_id] == Split.TRAIN)
    report["model_weights_sha256"] = "b" * 64
    baseline.write_text(json.dumps(finish_report(report)), encoding="utf-8")
    with pytest.raises(PermissionError, match="current"):
        training_view(tmp_path / "sealed", Split.TRAIN, baseline)


def test_analytics_replay_uses_historical_calibration(
    dataset: tuple[DatasetSpec, Path], tmp_path: Path
) -> None:
    from test_traffic_state_estimation import road
    from traffic_vision.calibration import (
        CalibrationMetadata,
        CalibrationReference,
        CalibrationValidator,
    )
    from traffic_vision.geometry import load_geometry
    from traffic_vision.models import Point

    spec, path = dataset
    source = spec.sources[2]
    coords = ((0, 0), (64, 0), (64, 48), (0, 48), (4, 4), (60, 4), (60, 44), (4, 44))
    points = tuple(
        CalibrationReference(point_id=str(i), image=Point(x=x, y=y), world=Point(x=x, y=y))
        for i, (x, y) in enumerate(coords)
    )
    calibration = CalibrationValidator.fit(
        width=64,
        height=48,
        references=points[:4],
        validation=points[4:],
        metadata=CalibrationMetadata(
            camera_id=source.camera_id,
            version="c1",
            timestamp=source.captured_at,
            image_model="rectified",
            world_frame="survey",
            validation_error_m=0,
            validation_max_error_m=0,
        ),
    )
    geometry = load_geometry(Path("configs/cameras/demo.json")).model_copy(
        update={"source_id": source.camera_id, "road": road(), "camera_calibration": calibration}
    )
    (path.parent / "geometry.json").write_text(geometry.model_dump_json(), encoding="utf-8")
    source = source.model_copy(update={"geometry": asset(path.parent, "geometry.json")})
    spec = spec.model_copy(update={"sources": (*spec.sources[:2], source, *spec.sources[3:])})
    path.write_text(spec.model_dump_json(), encoding="utf-8")
    release = seal_release(path, tmp_path / "sealed")
    selected = [f for f in spec.frames if f.source_id == source.source_id]
    payload = {
        "schema_version": "1.0",
        "dataset_manifest_sha256": release.manifest_sha256,
        "model_weights_sha256": "a" * 64,
        "split": "TEST",
        "run_kind": "synthetic_fixture",
        "frames": [
            PredictionFrame(frame_id=f.frame_id, detections=()).model_dump(mode="json")
            for f in selected
        ],
        "settings": {},
    }
    run = PredictionRun.model_validate({**payload, "run_sha256": canonical_hash(payload)})
    prediction_path = tmp_path / "predictions.json"
    prediction_path.write_text(run.model_dump_json(), encoding="utf-8")
    report = analytics_benchmark(tmp_path / "sealed", prediction_path)
    assert report["control_features"]["queue_count"]["coverage"] == 0
    assert source.source_id in report["missing_alignment_sources"]
    assert source.source_id not in report["missing_geometry_sources"]

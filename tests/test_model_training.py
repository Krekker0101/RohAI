"""Lifecycle contract fixtures. Mock checkpoints/results are never field evidence."""

import copy
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError
from test_dataset_foundation import asset, make_dataset
from traffic_core.models import ObjectKind
from traffic_vision.cli import main
from traffic_vision.dataset.files import canonical_hash, digest_file, write_new_json
from traffic_vision.dataset.release import seal_release
from traffic_vision.dataset.schema import FrameAnnotation, Split, UsageRights
from traffic_vision.dataset.validation import read_annotations
from traffic_vision.detector import YOLODetector
from traffic_vision.evaluation.vision import code_fingerprint, finish_report
from traffic_vision.manifest import ModelManifest
from traffic_vision.settings import VisionSettings
from traffic_vision.training import runner
from traffic_vision.training.config import TrainingConfig
from traffic_vision.training.evaluate import verify_evaluation
from traffic_vision.training.gates import compare, freeze_policy, required_gates
from traffic_vision.training.io import atomic_json, exclusive, read_verified
from traffic_vision.training.package import seal_package, verify_package
from traffic_vision.training.registry import ModelRegistry


def training_fixture(root: Path) -> TrainingConfig:
    """Synthetic files with mocked field metadata ONLY to test the orchestration contract."""
    spec, path = make_dataset(root / "draft")
    frames = []
    for frame in spec.frames:
        label_path = path.parent / frame.annotation.path
        ann = FrameAnnotation.model_validate_json(label_path.read_text(encoding="utf-8"))
        ann = ann.model_copy(
            update={
                "provenance": "human",
                "reviewed_by": "UNIT_TEST_ONLY",
                "exhaustive_classes": (0, 1, 2, 3),
            }
        )
        label_path.write_text(ann.model_dump_json(), encoding="utf-8")
        frames.append(
            frame.model_copy(update={"annotation": asset(path.parent, frame.annotation.path)})
        )
    spec = spec.model_copy(
        update={
            "purpose": "field",
            "class_mapping": {
                0: ObjectKind.CAR,
                1: ObjectKind.PEDESTRIAN,
                2: ObjectKind.BUS,
                3: ObjectKind.TRUCK,
            },
            "frames": tuple(frames),
            "sources": tuple(
                s.model_copy(
                    update={
                        "usage": "field",
                        "pretrained_exposure": "audited_no_known_overlap",
                        "rights": UsageRights(
                            license_id="TEST_ONLY",
                            owner="fixture",
                            source_reference="synthetic fixture",
                            training_allowed=True,
                            privacy_reviewed=True,
                            reviewed_by="UNIT_TEST_ONLY",
                            evidence="Mock metadata for unit contract, never field evidence",
                        ),
                    }
                )
                for s in spec.sources
            ),
        }
    )
    path.write_text(spec.model_dump_json(), encoding="utf-8")
    release = seal_release(path, root / "release")
    pinned_path = Path("configs/models/yolo26n.json")
    pinned = ModelManifest.load(pinned_path)
    baseline = finish_report(
        {
            "status": "MEASURED",
            "split": "TEST",
            "independent_test": True,
            "dataset_manifest_sha256": release.manifest_sha256,
            "run_kind": "pretrained_yolo26n",
            "model_id": pinned.model_id,
            "model_weights_sha256": pinned.weights_hash,
            "model_manifest_sha256": digest_file(pinned_path),
            "detection": {"ground_truth": 3, "mAP50": 0.5},
        }
    )
    write_new_json(root / "baseline.json", baseline)
    return TrainingConfig(
        experiment="unit-test",
        dataset_release=root / "release",
        dataset_version=spec.version,
        dataset_hash=release.manifest_sha256,
        baseline=root / "baseline.json",
        epochs=2,
    )


class FakeYOLO:
    def __init__(self, *, fail: bool = False, resumable: bool = True) -> None:
        self.callbacks: dict[str, Callable[[Any], None]] = {}
        self.fail = fail
        self.ckpt = {"epoch": 0, "optimizer": {"test": True}} if resumable else {}
        self.names = {0: "car", 1: "pedestrian", 2: "bus", 3: "truck"}
        self.task = "detect"
        self.model = SimpleNamespace(yaml={"scale": "n", "yaml_file": "yolo26.yaml"})
        self.trainer = SimpleNamespace(args=SimpleNamespace())
        self.options: dict[str, Any] = {}

    def add_callback(self, name: str, callback: Callable[[Any], None]) -> None:
        self.callbacks[name] = callback

    def train(self, **kwargs: Any) -> None:
        self.options = kwargs
        self.trainer.args = SimpleNamespace(**kwargs)
        root = Path(kwargs["project"]) / "ultralytics"
        (root / "weights").mkdir(parents=True, exist_ok=True)
        (root / "weights" / "last.pt").write_bytes(b"MOCK checkpoint, not PyTorch")
        self.callbacks["on_train_start"](self.trainer)
        self.callbacks["on_model_save"](self.trainer)
        if self.fail:
            raise KeyboardInterrupt()
        (root / "weights" / "best.pt").write_bytes(b"MOCK best, not production")
        (root / "results.csv").write_text("epoch,loss\n1,0.5\n", encoding="utf-8")


def test_config_deterministic_closed_schema(tmp_path: Path) -> None:
    config = TrainingConfig(
        experiment="test",
        dataset_release=Path("release"),
        dataset_version="v1",
        dataset_hash="a" * 64,
        baseline=Path("baseline.json"),
    )
    payload = config.model_dump(mode="json")
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    first.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    second.write_text(json.dumps(dict(reversed(list(payload.items()))), indent=3), encoding="utf-8")
    assert TrainingConfig.load(first).sha256 == TrainingConfig.load(second).sha256
    for override in ({"test": "heldout"}, {"optimizer": "auto"}, {"device": "0,1"}, {"batch": -1}):
        with pytest.raises(ValidationError):
            TrainingConfig.model_validate({**payload, **override})


def test_baseline_gate_stops_before_model_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = training_fixture(tmp_path)
    atomic_json(config.baseline, finish_report({"status": "BLOCKED"}))
    monkeypatch.setattr(runner, "load_yolo", lambda _: pytest.fail("Model must not load"))
    with pytest.raises(PermissionError, match="baseline"):
        runner.train(config, tmp_path / "runs")
    assert not (tmp_path / "runs").exists()


def test_training_resume_and_manifest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = training_fixture(tmp_path)
    fake = FakeYOLO(fail=True)
    monkeypatch.setattr(runner, "load_yolo", lambda _: fake)
    with pytest.raises(KeyboardInterrupt):
        runner.train(config, tmp_path / "runs")
    run = next((tmp_path / "runs").iterdir())
    manifest = read_verified(run / "manifest.json")
    assert manifest["status"] == "INTERRUPTED"
    assert "ultralytics/weights/last.pt" in manifest["artifacts"]
    assert manifest["resolved_ultralytics_config"]["seed"] == config.seed
    data = json.loads((run / "data" / "data.yaml").read_text(encoding="utf-8"))
    assert set(data) == {"path", "train", "val", "names"}
    selection = json.loads((run / "data" / "selection.json").read_text(encoding="utf-8"))
    assert set(selection) == {Split.TRAIN.value, Split.VALIDATION.value}
    assert not any("source2" in p.name or "source3" in p.name for p in (run / "data").rglob("*"))
    with pytest.raises(ValueError, match="identical"):
        runner.train(config.model_copy(update={"seed": 42}), tmp_path / "runs", resume=run)
    fake.fail = False
    completed = runner.train(config, tmp_path / "runs", resume=run)
    assert completed["status"] == "COMPLETED"
    assert len(completed["attempts"]) == 2
    assert fake.options["resume"] == str(run / "ultralytics" / "weights" / "last.pt")
    assert fake.options["save"] and fake.options["val"] and fake.options["deterministic"]
    with pytest.raises(ValueError, match="incomplete"):
        runner.train(config, tmp_path / "runs", resume=run)


def test_resume_refuses_optimizerless_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = training_fixture(tmp_path)
    monkeypatch.setattr(runner, "load_yolo", lambda _: FakeYOLO(fail=True))
    with pytest.raises(KeyboardInterrupt):
        runner.train(config, tmp_path / "runs")
    run = next((tmp_path / "runs").iterdir())
    monkeypatch.setattr(runner, "load_yolo", lambda _: FakeYOLO(resumable=False))
    with pytest.raises(ValueError, match="silent restart"):
        runner.train(config, tmp_path / "runs", resume=run)


def make_package(root: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config = training_fixture(root)
    monkeypatch.setattr(runner, "load_yolo", lambda _: FakeYOLO())
    result = runner.train(config, root / "runs")
    destination = root / "package"
    seal_package(root / "runs" / result["run_id"], destination)
    return destination


@pytest.mark.parametrize("fault", ["weights", "hash", "missing", "classes", "schema", "model"])
def test_package_rejects_corruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    root = make_package(tmp_path, monkeypatch)
    assert verify_package(root).architecture == "YOLO26n"
    manifest = root / "manifest.json"
    if fault == "weights":
        (root / "weights.pt").write_bytes(b"corrupt")
    elif fault == "missing":
        manifest.unlink()
    else:
        value = json.loads(manifest.read_text(encoding="utf-8"))
        if fault == "hash":
            value["package_sha256"] = "0" * 64
        else:
            if fault == "classes":
                value["class_mapping"]["0"] = "bus"
            elif fault == "schema":
                value["annotation_schema_version"] = "2.0"
            else:
                value["architecture"] = "YOLO-unsupported"
            value["package_sha256"] = canonical_hash(
                {k: v for k, v in value.items() if k != "package_sha256"}
            )
        manifest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        verify_package(root)
    # Startup rejects invalid package before attempting Ultralytics deserialization.
    detector = YOLODetector(VisionSettings(model_package=root))
    with pytest.raises((ValueError, OSError)):
        detector.open()
    assert detector._model is None


def report_fixture() -> tuple[dict[str, Any], dict[str, Any]]:
    shared: dict[str, Any] = {
        "status": "MEASURED",
        "independent_test": True,
        "split": "TEST",
        "dataset_manifest_sha256": "a" * 64,
        "prediction_run_sha256": "b" * 64,
    }
    vision = {
        **shared,
        "created_at": datetime.now(UTC).isoformat(),
        "run_kind": "pretrained_yolo26n",
        "code_sha256": code_fingerprint(),
        "class_mapping": {"0": "car", "1": "pedestrian", "2": "bus", "3": "truck"},
        "settings": {"vision_settings": VisionSettings().model_dump(mode="json")},
    }
    analytics = {
        **shared,
        "observability": {"labeled_frames": 10, "false_reliable": 0},
        "control_sensitivity": [{"absolute_green_error_s": 0.1}],
        "comparisons": [
            {
                "frame_id": "frame1",
                "approach": "north",
                "feature": "queue_count",
                "ground_truth": 3,
                "estimated": 4,
                "unit": "vehicle",
            }
        ],
    }
    for gate in required_gates(vision):
        target = vision if gate["section"] == "vision" else analytics
        for part in gate["path"][:-1]:
            target = target.setdefault(part, {})
        target[gate["path"][-1]] = 0 if gate["name"] == "false_reliable" else 0.5
    return finish_report(vision), finish_report(analytics)


def policy_fixture(root: Path, config_hash: str = "c" * 64) -> dict[str, Any]:
    root.mkdir(parents=True, exist_ok=True)
    vision, analytics = report_fixture()
    write_new_json(root / "baseline-vision.json", vision)
    write_new_json(root / "baseline-analytics.json", analytics)
    return freeze_policy(
        root / "baseline-vision.json",
        root / "baseline-analytics.json",
        root / "policy.json",
        safety_rationale="Unit-test budget, not road safety approval",
        max_green_error_seconds=1,
        config_hashes=[config_hash],
    )


@pytest.mark.parametrize(
    "failure",
    [
        "pedestrian",
        "bus",
        "truck",
        "night",
        "occlusion",
        "density",
        "queue",
        "wait",
        "coverage",
        "map_only",
        "missing",
        "unsafe",
    ],
)
def test_regression_gates_fail_closed(tmp_path: Path, failure: str) -> None:
    policy = policy_fixture(tmp_path)
    vision, analytics = (
        copy.deepcopy(policy["baseline"]["vision"]),
        copy.deepcopy(policy["baseline"]["analytics"]),
    )
    analytics["strata"]["all"]["queue_count"]["mae"] = 0.4
    analytics["comparisons"][0]["estimated"] = 3.5
    if failure in ("pedestrian", "bus", "truck"):
        index = str(("car", "pedestrian", "bus", "truck").index(failure))
        vision["detection"]["per_class"][index]["recall"] = 0.4
    elif failure in ("night", "occlusion", "density"):
        key = {
            "night": "day_night=night",
            "occlusion": "occlusion=heavy",
            "density": "traffic_density=high",
        }[failure]
        vision["strata"][key]["detection"]["recall"] = 0.4
    elif failure in ("queue", "wait"):
        feature = "queue_length_m" if failure == "queue" else "mean_wait_seconds"
        analytics["strata"]["all"][feature]["mae"] = 0.6
    elif failure == "coverage":
        analytics["comparisons"][0]["estimated"] = None
    elif failure == "map_only":
        analytics["strata"]["all"]["queue_count"]["mae"] = 0.5
        vision["detection"]["mAP50_95"] = 0.9
    elif failure == "unsafe":
        analytics["observability"]["false_reliable"] = 1
    else:
        analytics["strata"]["all"]["queue_length_m"]["mae"] = None
    result = compare(policy, finish_report(vision), finish_report(analytics), package_hash="d" * 64)
    assert not result["promotion_allowed"]
    assert any(not row["pass"] for row in result["gates"])


def test_policy_cannot_be_relaxed_or_test_changed(tmp_path: Path) -> None:
    policy = policy_fixture(tmp_path)
    vision, analytics = policy["baseline"]["vision"], policy["baseline"]["analytics"]
    modified = copy.deepcopy(policy)
    modified["gates"].pop()
    with pytest.raises(ValueError, match="changed or removed"):
        compare(finish_report(modified), vision, analytics, package_hash="d" * 64)
    vision = copy.deepcopy(vision)
    vision["dataset_manifest_sha256"] = "e" * 64
    with pytest.raises(ValueError):
        compare(policy, vision, analytics, package_hash="d" * 64)


def test_writer_lock_and_cli(tmp_path: Path) -> None:
    with exclusive(tmp_path):
        with pytest.raises(FileExistsError):
            with exclusive(tmp_path):
                pytest.fail("Second writer acquired lock")
    assert main(["model", "verify", "--package", str(tmp_path)]) == 2
    assert main(["registry", "show", "--root", str(tmp_path / "registry")]) == 0


def test_improvement_must_be_on_paired_samples(tmp_path: Path) -> None:
    policy = policy_fixture(tmp_path)
    vision = copy.deepcopy(policy["baseline"]["vision"])
    analytics = copy.deepcopy(policy["baseline"]["analytics"])
    analytics["strata"]["all"]["queue_count"]["mae"] = 0.1
    result = compare(policy, finish_report(vision), finish_report(analytics), package_hash="d" * 64)
    assert not result["promotion_allowed"]
    assert not next(row for row in result["gates"] if row["name"] == "downstream_improvement")[
        "pass"
    ]


def test_export_excludes_partial_annotations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = training_fixture(tmp_path)
    original = read_annotations

    def partial(*args: Any) -> dict[str, FrameAnnotation]:
        annotations = original(*args)
        key = "source0-0"
        annotations[key] = annotations[key].model_copy(update={"exhaustive_classes": (0,)})
        key = "source1-0"
        annotations[key] = annotations[key].model_copy(
            update={
                "objects": tuple(
                    obj.model_copy(update={"ignore": True}) for obj in annotations[key].objects
                )
            }
        )
        return annotations

    monkeypatch.setattr(runner, "read_annotations", partial)
    result = runner.export_yolo(config, tmp_path / "export")
    assert result["TRAIN"]["excluded"] == ["source0-0"]
    assert result["VALIDATION"]["excluded"] == ["source1-0"]
    assert not (tmp_path / "export/images/train/source0-0.png").exists()


def test_resume_detects_corrupt_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = training_fixture(tmp_path)
    monkeypatch.setattr(runner, "load_yolo", lambda _: FakeYOLO(fail=True))
    with pytest.raises(KeyboardInterrupt):
        runner.train(config, tmp_path / "runs")
    run = next((tmp_path / "runs").iterdir())
    (run / "ultralytics/weights/last.pt").write_bytes(b"damaged")
    monkeypatch.setattr(runner, "load_yolo", lambda _: pytest.fail("No load on hash failure"))
    with pytest.raises(ValueError, match="integrity-verified"):
        runner.train(config, tmp_path / "runs", resume=run)


def test_installed_ultralytics_accepts_training_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Parse with the installed API; do not run real optimization on synthetic fixtures.
    config = training_fixture(tmp_path)
    fake = FakeYOLO()
    monkeypatch.setattr(runner, "load_yolo", lambda _: fake)
    runner.train(config, tmp_path / "runs")
    (tmp_path / "ultralytics-settings").mkdir()
    monkeypatch.setenv("YOLO_CONFIG_DIR", str(tmp_path / "ultralytics-settings"))
    from ultralytics.cfg import get_cfg

    resolved = get_cfg(overrides=fake.options)
    assert resolved.optimizer == "AdamW"
    assert resolved.save and resolved.val and resolved.split == "val"


def evaluation_fixture(root: Path, package: Path) -> Path:
    training = read_verified(package / "training.json")
    policy = policy_fixture(root, training["config_sha256"])
    package_info = verify_package(package)
    # Bind the fixture baseline to this fixture release and re-freeze before candidate evidence.
    for report in policy["baseline"].values():
        report["dataset_manifest_sha256"] = package_info.dataset_hash
        finish_report(report)
    policy["dataset_hash"] = package_info.dataset_hash
    finish_report(policy)
    atomic_json(root / "policy.json", policy)
    ticket = finish_report(
        {
            "started_at": datetime.now(UTC).isoformat(),
            "package_sha256": package_info.package_sha256,
            "policy_sha256": policy["report_sha256"],
            "dataset_hash": package_info.dataset_hash,
        }
    )
    write_new_json(root / "ticket.json", ticket)
    vision = copy.deepcopy(policy["baseline"]["vision"])
    vision.update(
        {
            "run_kind": "candidate",
            "created_at": datetime.now(UTC).isoformat(),
            "model_weights_sha256": package_info.files["weights.pt"],
        }
    )
    analytics = copy.deepcopy(policy["baseline"]["analytics"])
    analytics["strata"]["all"]["queue_count"]["mae"] = 0.4
    analytics["comparisons"][0]["estimated"] = 3.5
    finish_report(vision)
    finish_report(analytics)
    write_new_json(root / "vision.json", vision)
    write_new_json(root / "analytics.json", analytics)
    comparison = compare(policy, vision, analytics, package_hash=package_info.package_sha256)
    assert comparison["promotion_allowed"]
    write_new_json(root / "comparison.json", comparison)
    return root


def test_registry_approval_rollback_and_tampered_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = ModelRegistry(tmp_path / "registry")
    with pytest.raises(ValueError, match="No approved"):
        registry.production()
    keys = []
    for index in range(2):
        package = make_package(tmp_path / f"case{index}", monkeypatch)
        evaluation = evaluation_fixture(tmp_path / f"eval{index}", package)
        key = registry.register(package)
        keys.append(key)
        with pytest.raises(ValueError, match="Invalid registry transition"):
            registry.transition(key, "APPROVED", reason="cannot skip validation")
        registry.transition(key, "VALIDATED", reason="unit-test pass", evaluation=evaluation)
        registry.transition(key, "APPROVED", reason="unit-test approval")
        assert registry.production().name == key
    assert registry.read()["previous_approved"] == keys[0]
    state = registry.rollback(reason="unit-test incident")
    assert state["models"][keys[1]]["status"] == "ROLLED_BACK"
    assert registry.production().name == keys[0]
    with pytest.raises(ValueError, match="No previous"):
        registry.rollback(reason="no second target")
    evidence_path = tmp_path / "registry" / "evaluations" / keys[0] / "comparison.json"
    evidence_path.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="Integrity"):
        registry.production()


def test_post_test_policy_change_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package = make_package(tmp_path / "case", monkeypatch)
    evaluation = evaluation_fixture(tmp_path / "evaluation", package)
    policy = read_verified(evaluation / "policy.json")
    policy["frozen_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    atomic_json(evaluation / "policy.json", finish_report(policy))
    with pytest.raises(ValueError, match="provenance"):
        verify_evaluation(evaluation, package)


def test_candidate_entrypoint_requires_detector_and_downstream(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from traffic_vision.evaluation.vision import PredictionRun
    from traffic_vision.training import evaluate

    package = make_package(tmp_path / "case", monkeypatch)
    fixture = evaluation_fixture(tmp_path / "evidence-fixture", package)
    vision = read_verified(fixture / "vision.json")
    analytics = read_verified(fixture / "analytics.json")
    info = verify_package(package)
    payload = {
        "dataset_manifest_sha256": info.dataset_hash,
        "model_weights_sha256": info.files["weights.pt"],
        "split": "TEST",
        "run_kind": "candidate",
        "frames": [],
        "settings": {},
        "schema_version": "1.0",
    }
    predictions = PredictionRun.model_validate({**payload, "run_sha256": canonical_hash(payload)})
    stages: list[str] = []

    def detector_tracker(release: Path, **kwargs: Any) -> tuple[dict[str, Any], PredictionRun]:
        assert release == tmp_path / "case/release"
        assert kwargs["split"] == Split.TEST and kwargs["run_kind"] == "candidate"
        assert kwargs["settings"].model_package == package
        stages.append("detector_tracker")
        vision["created_at"] = datetime.now(UTC).isoformat()
        vision["prediction_run_sha256"] = predictions.run_sha256
        return finish_report(vision), predictions

    def downstream(release: Path, path: Path) -> dict[str, Any]:
        stored = PredictionRun.model_validate_json(path.read_text(encoding="utf-8"))
        stored.verify()
        assert stages == ["detector_tracker"]
        stages.append("geometry_analytics_controller_observations")
        analytics["prediction_run_sha256"] = stored.run_sha256
        return finish_report(analytics)

    monkeypatch.setattr(evaluate, "run_model", detector_tracker)
    monkeypatch.setattr(evaluate, "analytics_benchmark", downstream)
    destination = tmp_path / "actual-orchestration"
    result = evaluate.evaluate_candidate(
        package, fixture / "policy.json", tmp_path / "case/release", destination
    )
    assert result["promotion_allowed"] and len(stages) == 2
    assert verify_evaluation(destination, package) == result
    with pytest.raises(FileExistsError):
        evaluate.evaluate_candidate(
            package, fixture / "policy.json", tmp_path / "case/release", destination
        )

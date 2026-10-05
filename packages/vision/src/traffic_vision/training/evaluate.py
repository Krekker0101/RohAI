"""One evaluation entrypoint for candidate detection, tracking and controller observations."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from traffic_vision.dataset.files import write_new_json
from traffic_vision.dataset.schema import Split
from traffic_vision.evaluation.analytics import analytics_benchmark
from traffic_vision.evaluation.vision import code_fingerprint, finish_report, run_model
from traffic_vision.manifest import ModelManifest
from traffic_vision.settings import VisionSettings
from traffic_vision.training.gates import compare, evaluation_protocol, validate_policy
from traffic_vision.training.io import read_verified
from traffic_vision.training.package import verify_package


def evaluate_candidate(
    package_path: Path, policy_path: Path, release: Path, destination: Path
) -> dict[str, Any]:
    package = verify_package(package_path)
    policy = read_verified(policy_path)
    validate_policy(policy)
    if policy["baseline"]["vision"]["code_sha256"] != code_fingerprint():
        raise ValueError("Evaluation code changed; re-measure baseline before candidate TEST")
    training = read_verified(package_path / "training.json")
    if (
        training["config_sha256"] not in policy["allowed_training_configs"]
        or package.dataset_hash != policy["dataset_hash"]
    ):
        raise ValueError("Candidate is outside the pre-registered experiment matrix")
    if datetime.fromisoformat(policy["frozen_at"]) > datetime.now(UTC):
        raise ValueError("Policy cannot be frozen in the future")
    release_root = release if release.is_dir() else release.parent
    if destination.resolve().is_relative_to(
        release_root.resolve()
    ) or destination.resolve().is_relative_to(package_path.resolve()):
        raise PermissionError("Evaluation cannot modify immutable inputs")
    destination.mkdir(parents=True, exist_ok=False)
    # Created before any candidate TEST inference. Never reuse an evaluation directory.
    write_new_json(
        destination / "ticket.json",
        finish_report(
            {
                "started_at": datetime.now(UTC).isoformat(),
                "package_sha256": package.package_sha256,
                "policy_sha256": policy["report_sha256"],
                "dataset_hash": package.dataset_hash,
            }
        ),
    )
    write_new_json(destination / "policy.json", policy)
    settings = VisionSettings.model_validate(
        policy["baseline"]["vision"]["settings"]["vision_settings"]
    )
    settings = settings.model_copy(
        update={
            "deployment_domain": ModelManifest.load(package_path / "model.json").deployment_domain,
            "model_package": package_path,
            "model_registry": None,
            "model_path": package_path / "weights.pt",
            "manifest_path": package_path / "model.json",
        }
    )
    if settings.image_size != package.supported_input.image_size:
        raise ValueError("Candidate input size differs from fixed evaluation protocol")
    vision, predictions = run_model(
        release, split=Split.TEST, settings=settings, run_kind="candidate"
    )
    if predictions is None:
        write_new_json(destination / "vision.json", vision)
        raise ValueError("Candidate evaluation blocked; see vision.json")
    write_new_json(destination / "predictions.json", predictions.model_dump(mode="json"))
    write_new_json(destination / "vision.json", vision)
    analytics = analytics_benchmark(release, destination / "predictions.json")
    write_new_json(destination / "analytics.json", analytics)
    if evaluation_protocol(vision) != policy["evaluation_protocol"]:
        raise ValueError("Evaluation implementation/environment changed since baseline")
    result = compare(policy, vision, analytics, package_hash=package.package_sha256)
    write_new_json(destination / "comparison.json", result)
    return result


def verify_evaluation(root: Path, package_path: Path) -> dict[str, Any]:
    package = verify_package(package_path)
    policy = read_verified(root / "policy.json")
    ticket = read_verified(root / "ticket.json")
    vision = read_verified(root / "vision.json")
    analytics = read_verified(root / "analytics.json")
    result = read_verified(root / "comparison.json")
    training = read_verified(package_path / "training.json")
    if (
        ticket["package_sha256"] != package.package_sha256
        or ticket["policy_sha256"] != policy["report_sha256"]
        or ticket["dataset_hash"] != package.dataset_hash
        or policy["dataset_hash"] != package.dataset_hash
        or vision["class_mapping"] != {str(k): v.value for k, v in package.class_mapping.items()}
        or vision["model_weights_sha256"] != package.files["weights.pt"]
        or vision["run_kind"] != "candidate"
        or training["config_sha256"] not in policy["allowed_training_configs"]
        or datetime.fromisoformat(policy["frozen_at"])
        > datetime.fromisoformat(ticket["started_at"])
        or datetime.fromisoformat(ticket["started_at"])
        > datetime.fromisoformat(vision["created_at"])
    ):
        raise ValueError("Evaluation/package/policy provenance mismatch")
    recomputed = compare(policy, vision, analytics, package_hash=package.package_sha256)
    if recomputed != result:
        raise ValueError("Comparison report does not match its evidence")
    return result

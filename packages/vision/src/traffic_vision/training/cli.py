"""Training lifecycle commands, integrated with the main rohai CLI."""

import argparse
from pathlib import Path
from typing import Any

from traffic_vision.training.config import TrainingConfig


def add_commands(commands: Any) -> None:
    training = commands.add_parser("train").add_subparsers(dest="action", required=True)
    for name in ("preflight", "vision"):
        command = training.add_parser(name)
        command.add_argument("--config", type=Path, required=True)
        command.add_argument("--runs", type=Path, default=Path(".runtime/training"))
        command.add_argument("--resume", type=Path)
    models = commands.add_parser("model").add_subparsers(dest="action", required=True)
    package = models.add_parser("package")
    package.add_argument("--run", type=Path, required=True)
    package.add_argument("--destination", type=Path, required=True)
    verify = models.add_parser("verify")
    verify.add_argument("--package", type=Path, required=True)
    freeze = models.add_parser("freeze-policy")
    freeze.add_argument("--vision", type=Path, required=True)
    freeze.add_argument("--analytics", type=Path, required=True)
    freeze.add_argument("--destination", type=Path, required=True)
    freeze.add_argument("--config", type=Path, nargs="+", required=True)
    freeze.add_argument("--safety-rationale", required=True)
    freeze.add_argument("--max-green-error-seconds", type=float, required=True)
    evaluate = models.add_parser("evaluate")
    evaluate.add_argument("--package", type=Path, required=True)
    evaluate.add_argument("--policy", type=Path, required=True)
    evaluate.add_argument("--release", type=Path, required=True)
    evaluate.add_argument("--destination", type=Path, required=True)
    registry = commands.add_parser("registry").add_subparsers(dest="action", required=True)
    for action in ("register", "validate", "approve", "reject", "deprecate", "rollback", "show"):
        entry = registry.add_parser(action)
        entry.add_argument("--root", type=Path, default=Path(".runtime/model-registry"))
        if action == "register":
            entry.add_argument("--package", type=Path, required=True)
        elif action not in ("show", "rollback"):
            entry.add_argument("--model", required=True)
        if action not in ("show", "register"):
            entry.add_argument("--reason", required=True)
        if action == "validate":
            entry.add_argument("--evaluation", type=Path, required=True)


def execute(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if args.command == "train":
        from traffic_vision.training.runner import preflight, train

        config = TrainingConfig.load(args.config)
        result = (
            preflight(config)
            if args.action == "preflight"
            else train(config, args.runs, resume=args.resume)
        )
        return result, 0
    if args.command == "model":
        from traffic_vision.training.evaluate import evaluate_candidate
        from traffic_vision.training.gates import freeze_policy
        from traffic_vision.training.package import seal_package, verify_package

        if args.action == "package":
            return seal_package(args.run, args.destination).model_dump(mode="json"), 0
        if args.action == "verify":
            return verify_package(args.package).model_dump(mode="json"), 0
        if args.action == "freeze-policy":
            return freeze_policy(
                args.vision,
                args.analytics,
                args.destination,
                safety_rationale=args.safety_rationale,
                max_green_error_seconds=args.max_green_error_seconds,
                config_hashes=[TrainingConfig.load(p).sha256 for p in args.config],
            ), 0
        result = evaluate_candidate(args.package, args.policy, args.release, args.destination)
        return result, 0 if result["promotion_allowed"] else 2
    from traffic_vision.training.registry import ModelRegistry, Status

    registry = ModelRegistry(args.root)
    if args.action == "show":
        return registry.read(), 0
    if args.action == "register":
        return {"status": "CANDIDATE", "model": registry.register(args.package)}, 0
    if args.action == "rollback":
        return registry.rollback(reason=args.reason), 0
    statuses: dict[str, Status] = {
        "validate": "VALIDATED",
        "approve": "APPROVED",
        "reject": "REJECTED",
        "deprecate": "DEPRECATED",
    }
    return registry.transition(
        args.model,
        statuses[args.action],
        reason=args.reason,
        evaluation=getattr(args, "evaluation", None),
    ), 0

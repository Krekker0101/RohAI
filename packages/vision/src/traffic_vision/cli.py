"""RohAI dataset, evaluation and reproducible model lifecycle CLI."""

import argparse
import json
from pathlib import Path
from typing import Any

from traffic_vision.dataset.files import write_new_json
from traffic_vision.dataset.release import export_training, open_release, seal_release
from traffic_vision.dataset.schema import DatasetSpec, Split
from traffic_vision.dataset.splits import plan_split
from traffic_vision.dataset.statistics import CoverageRequirement, statistics
from traffic_vision.dataset.validation import validate_dataset, validate_path
from traffic_vision.evaluation.vision import finish_report


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="rohai", description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    control = commands.add_parser("control", help="Offline Intelligence Engine experiments")
    control.add_argument("arguments", nargs=argparse.REMAINDER)
    maket = commands.add_parser("maket")
    maket.add_argument("arguments", nargs=argparse.REMAINDER)
    from traffic_vision.training.cli import add_commands

    add_commands(commands)
    dataset = commands.add_parser("dataset").add_subparsers(dest="action", required=True)
    ingest = dataset.add_parser("import")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--destination", type=Path, required=True)
    proposals = dataset.add_parser("propose")
    proposals.add_argument("path", type=Path)
    proposals.add_argument("--destination", type=Path, required=True)
    labels = dataset.add_parser("labels")
    labels.add_argument("path", type=Path, help="Native annotation JSON (editable draft)")
    labels.add_argument("--operation", choices=("review", "yolo", "coco"), required=True)
    labels.add_argument(
        "--classes", type=Path, required=True, help="Dataset manifest with class_mapping"
    )
    labels.add_argument("--width", type=int, required=True)
    labels.add_argument("--height", type=int, required=True)
    labels.add_argument("--reviewer")
    labels.add_argument("--exhaustive", type=int, nargs="*", default=[])
    labels.add_argument("--output", type=Path, required=True)
    validate = dataset.add_parser("validate")
    validate.add_argument("path", type=Path)
    validate.add_argument(
        "--metadata-only",
        action="store_true",
        help="Skip full source-video decode; not sufficient to seal a release",
    )
    validate.add_argument("--output", type=Path)
    stats = dataset.add_parser("stats")
    stats.add_argument("path", type=Path)
    stats.add_argument("--coverage", type=Path, default=Path("configs/datasets/coverage.json"))
    stats.add_argument("--output", type=Path)
    split = dataset.add_parser("split")
    split.add_argument("path", type=Path)
    split.add_argument("--challenge", nargs="*", default=[])
    split.add_argument("--output", type=Path, required=True)
    release = dataset.add_parser("release")
    release.add_argument("path", type=Path)
    release.add_argument("--destination", type=Path, required=True)
    export = dataset.add_parser("export-training")
    export.add_argument("path", type=Path)
    export.add_argument("--baseline", type=Path, required=True)
    export.add_argument("--split", type=Split, choices=list(Split), required=True)
    export.add_argument("--destination", type=Path, required=True)
    inventory = dataset.add_parser("inventory")
    inventory.add_argument("--root", type=Path, default=Path("."))
    inventory.add_argument("--output", type=Path, required=True)
    bench = commands.add_parser("benchmark").add_subparsers(dest="action", required=True)
    vision = bench.add_parser("vision")
    vision.add_argument("--release", type=Path, required=True)
    vision.add_argument(
        "--split",
        type=Split,
        choices=(Split.VALIDATION, Split.TEST, Split.CHALLENGE),
        default=Split.TEST,
    )
    vision.add_argument("--output", type=Path, required=True)
    vision.add_argument("--predictions", type=Path)
    analytics = bench.add_parser("analytics")
    analytics.add_argument("--release", type=Path, required=True)
    analytics.add_argument("--predictions", type=Path, required=True)
    analytics.add_argument("--output", type=Path, required=True)
    acceptance = bench.add_parser("acceptance")
    acceptance.add_argument("--vision", type=Path, required=True)
    acceptance.add_argument("--analytics", type=Path, required=True)
    acceptance.add_argument("--max-green-error-seconds", type=float)
    acceptance.add_argument("--output", type=Path, required=True)
    return result


def _read_dataset(path: Path) -> tuple[DatasetSpec, Path]:
    if path.is_dir() or path.name == "release.json":
        manifest = open_release(path)
        return manifest.dataset, path if path.is_dir() else path.parent
    return DatasetSpec.model_validate_json(path.read_text(encoding="utf-8")), path.parent


def _protect_output(output: Path | None, release_path: Path) -> None:
    if output is None:
        return
    root = release_path if release_path.is_dir() else release_path.parent
    if output.resolve().is_relative_to(root.resolve()):
        raise PermissionError("Evaluation output must be outside immutable release root")
    if output.exists():
        raise FileExistsError("Reports cannot be overwritten; choose a new run path")


def execute(args: argparse.Namespace) -> tuple[dict[str, Any], int]:
    if args.command == "control":
        import subprocess
        import sys

        child = subprocess.run(
            [
                sys.executable,
                "-m",
                "traffic_simulator.learning.intelligence_experiment",
                *args.arguments,
            ],
            check=False,
        )
        return {
            "command": "control",
            "status": "COMPLETED" if child.returncode == 0 else "ERROR",
            "exit_code": child.returncode,
        }, child.returncode
    if args.command == "maket":
        from smart_traffic_backend.maket.app import main as maket_main

        return {"mode": "PHYSICAL_MAKET"}, maket_main(args.arguments)
    if args.command in ("train", "model", "registry"):
        from traffic_vision.training.cli import execute as lifecycle_execute

        return lifecycle_execute(args)
    if args.command == "dataset":
        if args.action == "import":
            from traffic_vision.dataset.ingest import import_media

            path = import_media(args.path, args.destination)
            return {"status": "DRAFT_UNVERIFIED", "manifest": str(path)}, 0
        if args.action == "propose":
            from traffic_vision.dataset.proposals import propose_annotations

            return propose_annotations(args.path, args.destination), 0
        if args.action == "labels":
            from traffic_vision.dataset.labels import export_coco, export_yolo, review
            from traffic_vision.dataset.schema import FrameAnnotation

            annotation = FrameAnnotation.model_validate_json(args.path.read_text(encoding="utf-8"))
            classes = DatasetSpec.model_validate_json(
                args.classes.read_text(encoding="utf-8")
            ).class_mapping
            dimensions = {"width": args.width, "height": args.height, "classes": classes}
            if args.operation == "review":
                if not args.reviewer:
                    raise ValueError("Review requires attributed human --reviewer")
                return review(
                    annotation,
                    reviewer=args.reviewer,
                    exhaustive_classes=tuple(args.exhaustive),
                    **dimensions,
                ).model_dump(mode="json"), 0
            if args.operation == "coco":
                return export_coco(
                    annotation, image_id=1, file_name=annotation.frame_id + ".png", **dimensions
                ), 0
            return {
                "format": "YOLO",
                "text": export_yolo(annotation, **dimensions),
                "provenance": annotation.provenance,
                "sidecar": annotation.model_dump(mode="json"),
            }, 0
        if args.action == "inventory":
            from traffic_vision.dataset.inventory import inventory

            return inventory(args.root), 0
        if args.action == "validate":
            if args.path.is_dir() or args.path.name == "release.json":
                spec, root = _read_dataset(args.path)
                validation_result = validate_dataset(
                    spec, root, decode_videos=not args.metadata_only
                )
            else:
                validation_result = validate_path(args.path, decode_videos=not args.metadata_only)
            return validation_result.model_dump(mode="json"), 0 if validation_result.valid else 2
        if args.action == "stats":
            spec, root = _read_dataset(args.path)
            requirements = tuple(
                CoverageRequirement.model_validate(row)
                for row in json.loads(args.coverage.read_text(encoding="utf-8"))
            )
            return statistics(spec, root, requirements), 0
        if args.action == "split":
            if args.path.name == "release.json" or args.path.is_dir():
                raise PermissionError("Splits of sealed releases cannot be changed")
            spec, _ = _read_dataset(args.path)
            return plan_split(spec, challenge_sources=tuple(args.challenge)).model_dump(
                mode="json"
            ), 0
        if args.action == "release":
            sealed = seal_release(args.path, args.destination)
            return {
                "status": "SEALED",
                "dataset_id": sealed.dataset.dataset_id,
                "version": sealed.dataset.version,
                "manifest_sha256": sealed.manifest_sha256,
            }, 0
        if args.action == "export-training":
            export_training(args.path, args.split, args.baseline, args.destination)
            return {"status": "EXPORTED", "split": args.split.value, "training_started": False}, 0
    elif args.command == "benchmark":
        if args.action in ("vision", "analytics"):
            _protect_output(args.output, args.release)
        if args.action == "vision":
            from traffic_vision.evaluation.vision import run_pretrained

            _protect_output(args.predictions, args.release)
            result, run = run_pretrained(args.release, split=args.split)
            if run is not None:
                prediction_path = args.predictions or args.output.with_name(
                    args.output.stem + ".predictions.json"
                )
                _protect_output(prediction_path, args.release)
                write_new_json(prediction_path, run.model_dump(mode="json"))
                result["prediction_path"] = str(prediction_path)
                result = finish_report(result)
            return result, 0 if result["status"].startswith("MEASURED") else 2
        if args.action == "analytics":
            from traffic_vision.dataset.schema import CONTROL_FEATURES, FEATURE_UNITS
            from traffic_vision.evaluation.analytics import analytics_benchmark

            if not args.release.exists() or not args.predictions.exists():
                return finish_report(
                    {
                        "status": "BLOCKED",
                        "blocked_reasons": ["independent_release_or_prediction_run_missing"],
                        "independent_test": False,
                        "control_features": {
                            feature: {
                                "status": "NOT_MEASURED",
                                "unit": FEATURE_UNITS[feature],
                                "mae": None,
                                "coverage": None,
                            }
                            for feature in CONTROL_FEATURES
                        },
                        "traffic_metrics": None,
                        "acceptance_thresholds": None,
                        "fine_tuning_allowed": False,
                    }
                ), 2
            result = analytics_benchmark(args.release, args.predictions)
            return result, 0 if result["status"] == "MEASURED" else 2
        if args.action == "acceptance":
            from traffic_vision.evaluation.analytics import propose_acceptance

            return propose_acceptance(
                json.loads(args.vision.read_text(encoding="utf-8")),
                json.loads(args.analytics.read_text(encoding="utf-8")),
                max_green_error_seconds=args.max_green_error_seconds,
            ), 0
    raise ValueError("Unsupported command")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.command == "maket":
        from smart_traffic_backend.maket.app import main as maket_main

        return maket_main(args.arguments)
    try:
        output = getattr(args, "output", None)
        if output is not None and output.exists():
            raise FileExistsError("Output already exists; use a new run path")
        result, code = execute(args)
        if output is not None:
            write_new_json(output, result)
            print(
                json.dumps(
                    {
                        "status": result.get("status", result.get("valid", "written")),
                        "output": str(output),
                    },
                    ensure_ascii=False,
                )
            )
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return code
    except (OSError, ValueError, RuntimeError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

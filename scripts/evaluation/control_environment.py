"""Checker + fair baseline evaluation. Default split is VALIDATION, never training or TEST."""

import argparse
import importlib.metadata
from pathlib import Path
from typing import Any

from gymnasium.utils.env_checker import check_env
from traffic_core.settings import Timing
from traffic_simulator.learning.environment import LearningControlEnvironment
from traffic_simulator.learning.evaluation import evaluate_policy
from traffic_simulator.learning.policy import (
    ActuatedController,
    CurrentAdaptiveController,
    FixedTimeController,
)
from traffic_simulator.learning.protocol import ScenarioSplits
from traffic_simulator.twin_evaluation import write_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest", type=Path, default=Path("configs/control/scenario-splits.json")
    )
    parser.add_argument(
        "--split", choices=("TRAIN", "VALIDATION", "TEST", "CHALLENGE"), default="VALIDATION"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError("Choose a new evaluation report path")
    splits = ScenarioSplits.model_validate_json(args.manifest.read_text(encoding="utf-8"))
    cases = splits.select(args.split, purpose="evaluation")
    checker = LearningControlEnvironment(cases[0])
    try:
        check_env(checker, skip_render_check=True)
    finally:
        checker.close()
    reports: list[dict[str, Any]] = []
    for case in cases:
        group = [
            evaluate_policy(case, policy(Timing()))
            for policy in (FixedTimeController, CurrentAdaptiveController, ActuatedController)
        ]
        if len({r["arrival_tape_sha256"] for r in group}) != 1:
            raise ValueError("Baseline arrival tapes differ")
        reports.extend(group)
    write_report(
        args.output,
        {
            "schema": "rohai-control-evaluation-v1",
            "split": args.split,
            "scenario_split_sha256": splits.manifest_sha256,
            "gymnasium_version": importlib.metadata.version("gymnasium"),
            "environment_checker": "PASS",
            "reports": reports,
            "trained_policy_evaluated": False,
            "improvement_claim": False,
            "invariants_passed": all(r["invariants_passed"] for r in reports),
        },
    )
    return 0 if all(r["invariants_passed"] for r in reports) else 2


if __name__ == "__main__":
    raise SystemExit(main())

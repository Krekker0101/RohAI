"""Evaluate frozen digital-twin scenarios, without controller training."""

import argparse
import importlib.util
import json
import shutil
from pathlib import Path

from traffic_simulator.scenarios import catalog, load_scenario
from traffic_simulator.twin_evaluation import paired_comparison, write_report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rohai-sim", description=__doc__)
    parser.add_argument("--scenario", type=Path)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--duration", type=float, default=180)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output.exists():
            raise FileExistsError("Evaluation reports are immutable; choose a new output")
        scenarios = (
            [load_scenario(args.scenario)]
            if args.scenario
            else list(catalog(args.seed, args.duration).values())
        )
        results = [paired_comparison(scenario) for scenario in scenarios]
        passed = all(r["pass"] for r in results)
        write_report(
            args.output,
            {
                "schema": "rohai-twin-catalog-report-1",
                "results": results,
                "pass": passed,
                "rl_started": False,
                "sumo_availability": {
                    "binary": shutil.which("sumo") is not None,
                    "traci": importlib.util.find_spec("traci") is not None,
                    "libsumo": importlib.util.find_spec("libsumo") is not None,
                    "integration_executed": False,
                },
            },
        )
        print(json.dumps({"pass": passed, "output": str(args.output), "scenarios": len(results)}))
        return 0 if passed else 2
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "ERROR", "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

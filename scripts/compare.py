"""Run a reproducible paired benchmark and print a machine-readable report."""

import argparse

from traffic_core.settings import Timing
from traffic_simulator.comparison import ComparisonRequest, compare
from traffic_simulator.simulator import Scenario


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--duration", type=int, default=600)
    args = parser.parse_args()
    result = compare(
        ComparisonRequest(scenario=Scenario(seed=args.seed), duration_seconds=args.duration),
        Timing(),
    )
    print(result.model_dump_json(indent=2))


if __name__ == "__main__":
    main()

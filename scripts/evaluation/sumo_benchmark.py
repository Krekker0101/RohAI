"""Independent vehicle-only SUMO baseline experiment."""

import argparse
import json
from pathlib import Path

from traffic_simulator.sumo import benchmark


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seeds", type=int, nargs="+", default=[42, 43, 44])
    parser.add_argument("--seconds", type=int, default=600)
    parser.add_argument("--demand", type=float, default=900)
    args = parser.parse_args()
    report = benchmark(args.output, tuple(args.seeds), args.seconds, args.demand)
    print(json.dumps({"status": report["status"], "cases": len(report["cases"])}))


if __name__ == "__main__":
    main()

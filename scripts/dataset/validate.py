"""Convenience entry point; equivalent to python -m traffic_vision.cli dataset validate."""

import sys

from traffic_vision.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["dataset", "validate", *sys.argv[1:]]))

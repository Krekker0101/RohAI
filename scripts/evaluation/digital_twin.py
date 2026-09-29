"""Compatibility wrapper for the rohai-sim entrypoint."""

from traffic_simulator.twin_cli import main

if __name__ == "__main__":
    raise SystemExit(main())

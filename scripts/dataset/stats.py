import sys

from traffic_vision.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["dataset", "stats", *sys.argv[1:]]))

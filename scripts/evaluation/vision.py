import sys

from traffic_vision.cli import main

if __name__ == "__main__":
    raise SystemExit(main(["benchmark", "vision", *sys.argv[1:]]))

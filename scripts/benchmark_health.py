"""Paired microbenchmark of identical ROI checks; no road/perception accuracy claims."""

import argparse
import json
import statistics
import time
from pathlib import Path

import cv2
import numpy as np
from traffic_vision.geometry import load_geometry
from traffic_vision.health import PerceptionHealthMonitor
from traffic_vision.settings import PerceptionHealthSettings


def benchmark(iterations: int = 100) -> dict[str, object]:
    geometry = load_geometry(Path("configs/cameras/demo.json"))
    zones = [
        zone
        for approach in geometry.approaches
        for zone in (approach.incoming, approach.queue_zone, approach.pedestrian_crossing)
        if zone is not None
    ]
    gray = np.random.default_rng(42).integers(0, 256, (180, 320), dtype=np.uint8)
    reference = PerceptionHealthMonitor(PerceptionHealthSettings())
    optimized = PerceptionHealthMonitor(PerceptionHealthSettings())
    timings: dict[str, list[float]] = {"reference": [], "optimized": []}
    for iteration in range(iterations + 10):
        results: dict[str, list[dict[str, bool]]] = {}
        # Alternate order to reduce warm-cache and run-order bias.
        for mode in timings if iteration % 2 else reversed(timings):
            monitor = reference if mode == "reference" else optimized
            start = time.perf_counter()
            lap = (
                np.asarray(cv2.Laplacian(gray, cv2.CV_64F), dtype=np.float64)
                if mode == "optimized"
                else None
            )
            small = (
                np.asarray(cv2.resize(gray, (64, 64)), dtype=np.uint8)
                if mode == "optimized"
                else None
            )
            results[mode] = [
                monitor._image_checks(
                    gray, zone, str(index), iteration / 15, laplacian=lap, small=small
                )
                for index, zone in enumerate(zones)
            ]
            duration = (time.perf_counter() - start) * 1000
            if iteration >= 10:
                timings[mode].append(duration)
        if results["reference"] != results["optimized"]:
            raise AssertionError("Optimized health checks changed a diagnostic")
    before, after = (statistics.median(timings[mode]) for mode in timings)
    return {
        "scope": "ROI health microbenchmark only",
        "iterations": iterations,
        "roi_count": len(zones),
        "identical_checks": True,
        "reference_median_ms": before,
        "optimized_median_ms": after,
        "reduction_percent": (before - after) / before * 100,
        "full_pipeline_speedup": "NOT_ESTABLISHED_BY_THIS_MICROBENCHMARK",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = benchmark()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result))


if __name__ == "__main__":
    main()

"""Sequential same-video CPU budget measurements; recommendations never change defaults."""

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any


def tune(output: Path, seconds: int, repeats: int) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError("Tuning output must be new")
    if not 5 <= seconds <= 60 or not 1 <= repeats <= 5:
        raise ValueError("Use 5..60 seconds and 1..5 repeats")
    output.mkdir(parents=True)
    rows = []
    # Reversed alternate passes reduce monotonic thermal/run-order bias.
    for repeat in range(repeats):
        for threads in (1, 2, 4, 8) if repeat % 2 == 0 else (8, 4, 2, 1):
            path = output / f"threads-{threads}-repeat-{repeat}.json"
            subprocess.run(
                [
                    sys.executable,
                    "scripts/benchmark_vision_stage1.py",
                    "--mode",
                    "recorded",
                    "--seconds",
                    str(seconds),
                    "--torch-threads",
                    str(threads),
                    "--output",
                    str(path),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                timeout=180,
            )
            row = json.loads(path.read_text(encoding="utf-8"))
            if row["status"]["state"] != "running" or row["status"]["error"] is not None:
                raise ValueError("Cannot recommend a failed runtime")
            rows.append(row)
    aggregate = []
    for threads in (1, 2, 4, 8):
        runs = [r for r in rows if r["actual_torch_threads"] == threads]
        if len(runs) != repeats:
            raise ValueError("Actual native thread budget differs from requested budget")
        aggregate.append(
            {
                "threads": threads,
                "repeats": repeats,
                "median_inference_fps": statistics.median(
                    r["performance"]["rates"]["inference_fps"] for r in runs
                ),
                "median_inference_p95_ms": statistics.median(
                    r["performance"]["latency_ms"]["inference"]["p95"] for r in runs
                ),
                "median_cpu_percent": statistics.median(
                    r["process_cpu_percent"] for r in runs if r["process_cpu_percent"] is not None
                ),
                "total_capture_drops": sum(r["status"]["dropped_capture"] for r in runs),
            }
        )
    # Recorded input is paced at 12.5 FPS; additional throughput is not measurable here.
    qualified = [
        r
        for r in aggregate
        if r["median_inference_fps"] >= 12
        and r["median_inference_p95_ms"] <= 150
        and r["total_capture_drops"] <= repeats * 2
    ]
    best = (
        min(qualified, key=lambda r: (r["median_cpu_percent"], r["median_inference_p95_ms"]))
        if qualified
        else None
    )
    result = {
        "scope": "RECORDED_CPU_ONLY",
        "same_model_and_resolution": True,
        "aggregate": aggregate,
        "recommended_threads": best["threads"] if best else None,
        "selection_rule": ">=12 FPS, p95<=150ms, <=2 capture drops/run; "
        "then minimum CPU. Provisional observational thresholds, not field SLA.",
        "automatic_config_change": False,
        "field_accuracy": "NOT_MEASURED",
    }
    (output / "summary.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=8)
    parser.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    print(json.dumps(tune(args.output, args.seconds, args.repeats), indent=2))


if __name__ == "__main__":
    main()

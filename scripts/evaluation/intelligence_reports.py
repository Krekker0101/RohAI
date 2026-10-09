"""Independent reports from immutable study outputs; exact forecast target horizon."""

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Literal

import numpy as np
from traffic_simulator.learning.experiment_runtime import file_hash, read, write_new
from traffic_simulator.learning.forecast import ArrivalForecaster, ArrivalSample
from traffic_simulator.learning.intelligence_contracts import TrafficObservation


def forecast_quality(paths: list[Path], horizon_s: int = 10) -> dict[str, object]:
    errors: dict[str, list[float]] = {
        m: [] for m in ("moving_average", "exponential", "historical")
    }
    methods: tuple[Literal["moving_average", "exponential", "historical"], ...] = (
        "moving_average",
        "exponential",
        "historical",
    )
    missing = 0
    for path in paths:
        observations = [TrafficObservation.model_validate(a["observation"]) for a in read(path)]
        target = {o.timestamp_s: o.physical("north.arrival_rate") for o in observations}
        forecasts = {m: ArrivalForecaster(m) for m in methods}
        for observation in observations:
            rate = observation.physical("north.arrival_rate")
            if rate is None:
                continue
            truth = target.get(observation.timestamp_s + horizon_s)
            for method, forecaster in forecasts.items():
                forecaster.add(
                    ArrivalSample(timestamp_s=observation.timestamp_s, rate_per_minute=rate)
                )
                prediction = forecaster.predict(observation.timestamp_s, horizon_s)
                if prediction is None:
                    continue
                if truth is None:
                    missing += 1
                    continue  # Never replace a missing 10s target with a later recovered frame.
                errors[method].append(abs(prediction.rate_per_minute - truth))
    return {
        "horizon_s": horizon_s,
        "target": "future observed rolling arrival-rate estimate",
        "source_hashes": {str(p): file_hash(p) for p in paths},
        "missing_or_right_censored_targets": missing,
        "quality": {
            m: {"samples": len(v), "MAE_vehicle_per_min": float(np.mean(v)) if v else None}
            for m, v in errors.items()
        },
        "field_accuracy": None,
        "uncertainty": "Descriptive intervals only; no calibrated coverage claim",
    }


def report(roots: list[Path], destination: Path) -> None:
    if destination.exists():
        raise FileExistsError("Analysis output cannot overwrite a prior report")
    destination.mkdir(parents=True)
    summaries = [read(root / "benchmark.json") for root in roots]
    rows = []
    for root, summary in zip(roots, summaries, strict=True):
        for policy, values in summary["test_metrics"].items():
            rows.append({"experiment": root.name, "policy": policy, **values})
        paths = sorted((root / "evaluation" / "test").glob("*fixed-None.decisions.json"))
        write_new(destination / (root.name + "-forecast.json"), forecast_quality(paths))
    with (destination / "metrics.csv").open("x", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    os.environ["MPLCONFIGDIR"] = str((destination / "mpl-cache").resolve())
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    names = ("fixed", "adaptive", "actuated", "max_pressure", "rohai_ppo")
    labels: tuple[str, ...] = ("Fixed", "Adaptive", "Actuated", "Pressure\n(upstream)", "PPO 2400")
    values = [summaries[0]["test_metrics"][p]["total_delay_entity_s"] for p in names]
    if len(summaries) > 1:
        labels = (*labels, "PPO 4800")
        values.append(summaries[1]["test_metrics"]["rohai_ppo"]["total_delay_entity_s"])
    fig, ax = plt.subplots(figsize=(9, 4.8))
    fig.subplots_adjust(bottom=0.20, top=0.88, left=0.10, right=0.98)
    bars = ax.bar(labels, values, color=["#64748b"] * 4 + ["#dc2626"] * (len(values) - 4))
    ax.bar_label(bars, fmt="%.0f", padding=3)
    ax.set_ylabel("Mean total delay / 120s scenario (entity s)")
    ax.set_title("RohAI Stage 2: held-out synthetic suite, 3 training seeds")
    ax.set_ylim(0, max(values) * 1.18)
    fig.text(
        0.5,
        0.03,
        "Smoke studies; censored unfinished trips; no field/HIL or superiority claim",
        ha="center",
        fontsize=9,
    )
    fig.savefig(destination / "control-comparison.png", dpi=160)
    fig.savefig(destination / "control-comparison.svg")
    plt.close(fig)
    write_new(
        destination / "provenance.json",
        {
            "source_benchmarks": {str(root): file_hash(root / "benchmark.json") for root in roots},
            "primary_metric": "total_delay_entity_s",
            "automatic_activation": False,
            "followup_budget_only": True,
            "historical_PPO_results_included": False,
        },
    )
    print(json.dumps({"output": str(destination), "studies": len(roots)}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roots", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report(args.roots, args.output)


if __name__ == "__main__":
    main()

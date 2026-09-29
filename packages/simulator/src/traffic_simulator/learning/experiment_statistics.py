"""Paired, seed-clustered summaries; repeated baseline rows are not independent runs."""

from typing import Any

import numpy as np
from numpy.typing import NDArray


def summary(values: list[float], seed: int = 921) -> dict[str, Any]:
    data = np.asarray(values, dtype=np.float64)
    if data.size == 0 or not np.isfinite(data).all():
        raise ValueError("Finite nonempty sample required")
    rng = np.random.default_rng(seed)
    means = data[rng.integers(0, len(data), size=(4000, len(data)))].mean(axis=1)
    return {
        "n": len(data),
        "mean": float(data.mean()),
        "median": float(np.median(data)),
        "std": float(data.std(ddof=1)) if len(data) > 1 else None,
        "mean_ci95": np.quantile(means, [0.025, 0.975]).tolist(),
        "p90": float(np.percentile(data, 90)),
        "p95": float(np.percentile(data, 95)),
        "p99": float(np.percentile(data, 99)),
        "minimum": float(data.min()),
        "maximum": float(data.max()),
    }


def paired(matrix: NDArray[np.float64], *, familywise_tests: int = 1) -> dict[str, Any]:
    """Rows: independent training seeds; columns: fixed evaluation scenarios.

    CI is conditional on this scenario suite; do not pretend to estimate all road demand.
    Averaging columns BEFORE resampling preserves dependence between scenarios for a policy.
    """
    if matrix.ndim != 2 or matrix.shape[0] < 2 or not np.isfinite(matrix).all():
        raise ValueError("Need >=2 independent paired runs and finite metric matrix")
    values = matrix.mean(axis=1)
    result = summary(values.tolist())
    rng = np.random.default_rng(921)
    samples = values[rng.integers(0, len(values), size=(20000, len(values)))].mean(axis=1)
    alpha = 0.05 / familywise_tests
    interval = np.quantile(samples, [alpha / 2, 1 - alpha / 2]).tolist()
    result.update(
        simultaneous_ci=interval,
        familywise_tests=familywise_tests,
        direction="negative means learned has lower cost; throughput is negated",
        verdict="BETTER" if interval[1] < 0 else "WORSE" if interval[0] > 0 else "INCONCLUSIVE",
        uncertainty="training-seed bootstrap, conditional on fixed scenario suite",
    )
    return result


def required_runs(
    pilot_deltas: list[float], baseline_mean: float, *, minimum: int = 12, maximum: int = 16
) -> dict[str, Any]:
    if len(pilot_deltas) < 4 or baseline_mean <= 0:
        raise ValueError("Four pilot seeds and positive reference required")
    sd = float(np.std(pilot_deltas, ddof=1))
    half_width = baseline_mean * 0.10  # precision planning, not a performance acceptance margin
    required = max(minimum, int(np.ceil((1.96 * sd / half_width) ** 2)))
    return {
        "pilot_n": len(pilot_deltas),
        "pilot_std": sd,
        "target_half_width": half_width,
        "required_approximate": required,
        "selected_n": min(maximum, required),
        "budget_limited": required > maximum,
        "basis": "normal approximation on VALIDATION paired delay; final bootstrap reported",
    }

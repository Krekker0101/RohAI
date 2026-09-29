"""Bounded offline benchmark API; missing estimates remain missing, never zero-filled."""

import math
from collections import defaultdict

from pydantic import Field, model_validator
from traffic_core.models import Model


class MetricSample(Model):
    metric: str = Field(min_length=1, max_length=100)
    unit: str = Field(min_length=1, max_length=32)
    stratum: str = Field(default="all", max_length=100)
    estimated: float | None
    ground_truth: float


class SampleError(Model):
    sample: MetricSample
    absolute_error: float | None
    relative_error: float | None
    signed_error: float | None


class MetricSummary(Model):
    metric: str
    unit: str
    stratum: str
    samples: int
    measured: int
    coverage: float
    mae: float | None
    rmse: float | None
    bias: float | None
    mean_relative_error: float | None
    absolute_error_percentiles: dict[str, float | None]


class BenchmarkRequest(Model):
    samples: tuple[MetricSample, ...] = Field(min_length=1, max_length=10000)

    @model_validator(mode="after")
    def consistent_units(self) -> "BenchmarkRequest":
        units: dict[str, str] = {}
        for s in self.samples:
            if s.metric in units and units[s.metric] != s.unit:
                raise ValueError("A metric must use one explicit unit; convert before comparing")
            units[s.metric] = s.unit
        return self


class BenchmarkReport(Model):
    errors: tuple[SampleError, ...]
    summaries: tuple[MetricSummary, ...]


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lo, hi = math.floor(position), math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


def benchmark(request: BenchmarkRequest) -> BenchmarkReport:
    errors: list[SampleError] = []
    groups: dict[tuple[str, str, str], list[SampleError]] = defaultdict(list)
    for sample in request.samples:
        signed = sample.estimated - sample.ground_truth if sample.estimated is not None else None
        error = SampleError(
            sample=sample,
            signed_error=signed,
            absolute_error=abs(signed) if signed is not None else None,
            relative_error=abs(signed) / abs(sample.ground_truth)
            if signed is not None and sample.ground_truth != 0
            else None,
        )
        errors.append(error)
        groups[(sample.metric, sample.unit, sample.stratum)].append(error)
    summaries: list[MetricSummary] = []
    for (metric, unit, stratum), rows in sorted(groups.items()):
        absolute = [r.absolute_error for r in rows if r.absolute_error is not None]
        signed_errors = [r.signed_error for r in rows if r.signed_error is not None]
        relative = [r.relative_error for r in rows if r.relative_error is not None]
        n = len(absolute)
        summaries.append(
            MetricSummary(
                metric=metric,
                unit=unit,
                stratum=stratum,
                samples=len(rows),
                measured=n,
                coverage=n / len(rows),
                mae=sum(absolute) / n if n else None,
                rmse=math.sqrt(sum(x * x for x in absolute) / n) if n else None,
                bias=sum(signed_errors) / n if n else None,
                mean_relative_error=sum(relative) / len(relative) if relative else None,
                absolute_error_percentiles={
                    f"p{q}": percentile(absolute, q / 100) for q in (50, 90, 95, 99)
                },
            )
        )
    return BenchmarkReport(errors=tuple(errors), summaries=tuple(summaries))


class IdentitySample(Model):
    timestamp: float = Field(ge=0)
    ground_truth_id: str
    estimated_id: str | None
    arrival_events: int = Field(default=0, ge=0)


class IdentityReport(Model):
    id_switches: int
    track_fragmentation: int
    duplicate_arrivals: int
    lost_tracks: int
    recovered_tracks: int


def benchmark_identities(samples: tuple[IdentitySample, ...]) -> IdentityReport:
    if len(samples) > 10000:
        raise ValueError("Identity benchmark capacity exceeded")
    previous: dict[str, IdentitySample] = {}
    last_id: dict[str, str] = {}
    arrivals: dict[str, int] = defaultdict(int)
    switches = fragments = lost = recovered = 0
    for sample in samples:
        key = sample.ground_truth_id
        old = previous.get(key)
        if old is not None and sample.timestamp <= old.timestamp:
            raise ValueError("Ground truth identity timestamps must advance")
        if old is not None and old.estimated_id is not None and sample.estimated_id is None:
            lost += 1
        if sample.estimated_id is not None:
            if key in last_id and sample.estimated_id != last_id[key]:
                switches += 1
            if old is not None and old.estimated_id is None and key in last_id:
                fragments += 1
                recovered += 1
            last_id[key] = sample.estimated_id
        arrivals[key] += sample.arrival_events
        previous[key] = sample
    return IdentityReport(
        id_switches=switches,
        track_fragmentation=fragments,
        duplicate_arrivals=sum(max(0, n - 1) for n in arrivals.values()),
        lost_tracks=lost,
        recovered_tracks=recovered,
    )

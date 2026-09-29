"""Privileged simulator scoring, separate from actor observations. All units are explicit."""

from typing import Any

import numpy as np
from traffic_core.models import Direction, ObjectKind

from traffic_simulator.learning.contracts import REWARD_UNITS, RewardSpecification
from traffic_simulator.twin_models import GroundTruthState, ServiceRecord


def reward_terms(
    before: GroundTruthState,
    after: GroundTruthState,
    switched: int,
    records: list[ServiceRecord] | None = None,
) -> dict[str, float]:
    dt = after.timestamp_s - before.timestamp_s
    waits = [after.timestamp_s - e.arrived_at_s for e in (*after.queued, *after.upstream)]
    active = [r for r in (records or []) if not r.departed]
    waits.extend(r.queue_wait_s + r.downstream_delay_s for r in active)
    direction_maxima = [
        max(
            (
                after.timestamp_s - e.arrived_at_s
                for e in (*after.queued, *after.upstream)
                if e.approach == direction
            ),
            default=0,
        )
        for direction in Direction
    ]
    for index, direction in enumerate(Direction):
        direction_maxima[index] = max(
            direction_maxima[index],
            max(
                (r.queue_wait_s + r.downstream_delay_s for r in active if r.approach == direction),
                default=0,
            ),
        )
    queue_and_ped = after.total_wait_s - before.total_wait_s
    pedestrian = after.pedestrian_wait_s - before.pedestrian_wait_s
    return {
        "total_delay": queue_and_ped + after.downstream_delay_s - before.downstream_delay_s,
        "queue_delay": queue_and_ped - pedestrian,
        "pedestrian_delay": pedestrian,
        "throughput": float(after.departed - before.departed),
        "maximum_wait": max(waits, default=0) * dt,
        "fairness": (max(direction_maxima) - min(direction_maxima)) * dt,
        "spillback": after.spillback_s - before.spillback_s,
        "switching": float(switched),
    }


class RewardLedger:
    def __init__(self, specification: RewardSpecification) -> None:
        self.specification = specification
        self.raw = dict.fromkeys(REWARD_UNITS, 0.0)
        self.contributions = dict.fromkeys(REWARD_UNITS, 0.0)

    def record(self, terms: dict[str, float]) -> tuple[float, dict[str, float]]:
        if set(terms) != set(REWARD_UNITS):
            raise ValueError("Reward component coverage mismatch")
        contributions = {}
        for key, value in terms.items():
            if not np.isfinite(value) or value < -1e-8:
                raise ValueError("Reward terms must be finite nonnegative costs/counts")
            sign = 1 if key == "throughput" else -1
            contribution = sign * float(getattr(self.specification, key)) * value
            self.raw[key] += value
            self.contributions[key] += contribution
            contributions[key] = contribution
        return sum(contributions.values()), contributions

    def report(self) -> dict[str, Any]:
        return {
            "specification": self.specification.model_dump(mode="json"),
            "total_reward": sum(self.contributions.values()),
            "components": {
                key: {"raw": self.raw[key], "unit": unit, "contribution": self.contributions[key]}
                for key, unit in REWARD_UNITS.items()
            },
        }


def distribution(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "mean": float(np.mean(values)) if values else None,
        "median": float(np.median(values)) if values else None,
        **{f"p{p}": float(np.percentile(values, p)) if values else None for p in (90, 95, 99)},
        "maximum": max(values) if values else None,
    }


def fairness_report(truth: GroundTruthState, completed: list[ServiceRecord]) -> dict[str, Any]:
    pending = (*truth.queued, *truth.upstream)
    rows = [
        (r.approach, r.kind, r.queue_wait_s + r.downstream_delay_s, not r.departed)
        for r in completed
    ]
    rows += [(e.approach, e.kind, truth.timestamp_s - e.arrived_at_s, True) for e in pending]
    identities = [r.entity_id for r in completed] + [e.entity_id for e in pending]
    if len(set(identities)) != len(identities) or len(rows) != truth.arrived:
        raise ValueError("Incomplete or duplicate service ledger identity")

    def summarize(selected: list[tuple[Direction, ObjectKind, float, bool]]) -> dict[str, Any]:
        return {
            "unit": "s",
            "all_arrivals_lower_bound": distribution([r[2] for r in selected]),
            "completed_total_wait": distribution([r[2] for r in selected if not r[3]]),
            "unfinished_wait_lower_bound": distribution([r[2] for r in selected if r[3]]),
            "right_censored_count": sum(r[3] for r in selected),
        }

    return {
        "all": summarize(rows),
        "by_direction": {d.value: summarize([r for r in rows if r[0] == d]) for d in Direction},
        "by_class": {
            kind.value: summarize([r for r in rows if r[1] == kind])
            for kind in ObjectKind
            if kind not in (ObjectKind.UNKNOWN, ObjectKind.EMERGENCY_VEHICLE)
        },
        "pedestrians": summarize([r for r in rows if r[1] == ObjectKind.PEDESTRIAN]),
        "limitation": (
            "Queue wait plus excess downstream hold; unfinished waits are right-censored. "
            "Nominal travel/crossing time is excluded."
        ),
    }

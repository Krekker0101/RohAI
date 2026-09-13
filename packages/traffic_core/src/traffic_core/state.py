from collections.abc import Sequence

from traffic_core.models import ApproachState, Congestion, Counts, Direction, ObjectKind

SCORE_WEIGHTS = {
    ObjectKind.CAR: 1.0,
    ObjectKind.BUS: 2.5,
    ObjectKind.TRUCK: 2.0,
    ObjectKind.MOTORCYCLE: 0.5,
    ObjectKind.BICYCLE: 0.4,
    ObjectKind.PEDESTRIAN: 1.0,
}


def build_approach_state(
    direction: Direction,
    waiting_objects: Sequence[tuple[ObjectKind, float]],
    arrival_rate_per_minute: float,
    *,
    waiting_weight: float,
    moderate_queue: int,
    high_queue: int,
) -> ApproachState:
    """Shared state calculation for simulated queues and future tracked observations.

    Each pair contains a class and its observed stationary waiting time in seconds.
    Camera-specific stop detection and queue membership stay in the vision adapter.
    """
    size = len(waiting_objects)
    kinds = [kind for kind, _ in waiting_objects]
    waits = [wait for _, wait in waiting_objects]
    if any(wait < 0 for wait in waits):
        raise ValueError("Waiting time cannot be negative")
    oldest = max(waits, default=0.0)
    congestion = (
        Congestion.HIGH
        if size >= high_queue
        else Congestion.MODERATE
        if size >= moderate_queue
        else Congestion.LOW
    )
    return ApproachState(
        direction=direction,
        counts=Counts.from_kinds(kinds),
        queue_length=size,
        traffic_score=sum(SCORE_WEIGHTS[kind] for kind in kinds) + oldest * waiting_weight,
        mean_wait_seconds=sum(waits) / size if size else 0,
        oldest_wait_seconds=oldest,
        arrival_rate_per_minute=arrival_rate_per_minute,
        congestion=congestion,
    )

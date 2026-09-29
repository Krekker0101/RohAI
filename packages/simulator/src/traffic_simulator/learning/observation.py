"""Production-compatible semantic encoding; no access to GroundTruthState or dynamics."""

from collections import deque

import numpy as np
from numpy.typing import NDArray
from traffic_core.models import (
    Direction,
    ObjectKind,
    ObservationStatus,
    Phase,
    SignalStage,
    phase_for,
)

from traffic_simulator.learning.contracts import FEATURES, ObservationV1, ObservedSupplement
from traffic_simulator.learning.safety import SafetySupervisor
from traffic_simulator.twin_dynamics import LENGTHS
from traffic_simulator.twin_models import ObservedTrafficState


def encode(
    observed: ObservedTrafficState,
    supervisor: SafetySupervisor,
    supplements: dict[Direction, ObservedSupplement] | None = None,
) -> ObservationV1:
    if not isinstance(observed, ObservedTrafficState):
        raise TypeError("ObservationV1 can only encode observed traffic")
    now = observed.traffic.simulation_time
    values: dict[str, float | None] = {}
    for approach in observed.traffic.approaches:
        direction, evidence = approach.direction, approach.observation
        tracks = [
            t
            for t in observed.tracks
            if t.approach == direction and t.kind != ObjectKind.PEDESTRIAN
        ]
        length = (
            max(
                (
                    t.distance_to_stop_m + LENGTHS.get(t.kind, 4.5)
                    for t in tracks
                    if t.distance_to_stop_m is not None
                ),
                default=0,
            )
            if evidence.reliable
            else None
        )
        # Do not fabricate geometry from a count when this source supplies no tracks.
        if approach.queue_length and not tracks:
            length = None
        supplemental = (supplements or {}).get(direction)
        fresh = supplemental is not None and 0 <= now - supplemental.captured_at_s <= 3
        phase = phase_for(direction)
        connected = evidence.camera_health.get("camera_connected")
        fields = {
            "queue_count": approach.queue_length,
            "queue_length_m": length,
            "oldest_wait_s": approach.oldest_wait_seconds,
            "mean_wait_s": approach.mean_wait_seconds,
            "arrival_rate": approach.arrival_rate_per_minute,
            "departure_rate": supplemental.departure_rate_per_minute
            if fresh and supplemental
            else None,
            "pedestrian_waiting": approach.pedestrian_count_waiting,
            "pedestrian_oldest_wait_s": approach.pedestrian_oldest_wait_seconds,
            "time_since_service_s": now - supervisor.last_service[phase],
            "has_received_service": float(supervisor.has_served[phase]),
            "downstream_occupancy": supplemental.downstream_occupancy
            if fresh and supplemental
            else None,
            "measurement_age_s": evidence.measurement_age_ms / 1000
            if evidence.measurement_age_ms is not None
            else None,
            "observation_confidence": evidence.measurement_confidence,
            "camera_connected": float(connected) if connected is not None else None,
            "reliable": float(evidence.reliable),
            "traffic_score": approach.traffic_score,
        }
        values.update(
            {
                f"{direction.value}.{key}": float(value) if value is not None else None
                for key, value in fields.items()
            }
        )
        for status in ObservationStatus:
            values[f"{direction.value}.status.{status.value}"] = float(
                evidence.observation_status == status
            )
    signal = supervisor.machine.snapshot(now)
    values.update({f"phase.{p.value}": float(signal.phase == p) for p in Phase})
    values.update({f"pending.{p.value}": float(supervisor.machine.pending == p) for p in Phase})
    values.update(
        {
            f"last_requested.{p.value}": float(supervisor.last_request.requested_phase == p)
            if supervisor.last_request
            else None
            for p in Phase
        }
    )
    values.update({f"stage.{s.value}": float(signal.stage == s) for s in SignalStage})
    values.update(
        phase_age_s=now - supervisor.phase_started_at,
        stage_age_s=signal.elapsed_seconds,
        green_budget_s=supervisor.machine.green_seconds,
        pending_budget_s=supervisor.pending_duration,
        last_requested_duration_s=supervisor.last_request.requested_duration_s
        if supervisor.last_request
        else None,
        last_request_accepted=float(supervisor.outcome.accepted)
        if supervisor.last_request
        else None,
        pedestrian_time_since_service_s=now - supervisor.last_service[Phase.PEDESTRIAN],
        fault_latched=float(supervisor.machine.faulted),
    )
    normalized, valid, clipped = [], [], []
    for feature in FEATURES:
        value = values[feature.name]
        valid.append(int(value is not None))
        clipped.append(int(value is not None and not 0 <= value <= feature.scale))
        normalized.append(min(1.0, max(0.0, value / feature.scale)) if value is not None else 0.0)
    return ObservationV1(
        timestamp_s=now, values=tuple(normalized), valid=tuple(valid), clipped=tuple(clipped)
    )


class ObservationStack:
    def __init__(self, depth: int = 4) -> None:
        if not 1 <= depth <= 32:
            raise ValueError("History depth must be within [1,32]")
        self.depth = depth
        self.frames: deque[ObservationV1] = deque(maxlen=depth)

    @property
    def shape(self) -> tuple[int, int]:
        return self.depth, 3 * len(FEATURES) + 1

    def append(self, frame: ObservationV1) -> NDArray[np.float32]:
        if self.frames and frame.timestamp_s <= self.frames[-1].timestamp_s:
            raise ValueError("Stack timestamps must increase")
        self.frames.append(frame)
        return self.array()

    def array(self) -> NDArray[np.float32]:
        result = np.zeros(self.shape, dtype=np.float32)
        offset = self.depth - len(self.frames)
        for index, frame in enumerate(self.frames, offset):
            result[index] = np.asarray(
                (*frame.values, *frame.valid, *frame.clipped, 1), dtype=np.float32
            )
        return result

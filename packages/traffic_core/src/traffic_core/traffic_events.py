"""Pixel-free traffic events and explicitly unit-bearing estimates."""

from enum import StrEnum

from pydantic import Field

from traffic_core.models import Model


class TrafficEventType(StrEnum):
    VEHICLE_ENTERED_APPROACH = "VehicleEnteredApproach"
    VEHICLE_STOPPED = "VehicleStopped"
    VEHICLE_JOINED_QUEUE = "VehicleJoinedQueue"
    VEHICLE_STARTED_MOVING = "VehicleStartedMoving"
    VEHICLE_CROSSED_STOP_LINE = "VehicleCrossedStopLine"
    VEHICLE_EXITED_INTERSECTION = "VehicleExitedIntersection"
    PEDESTRIAN_ARRIVED = "PedestrianArrived"
    PEDESTRIAN_STARTED_CROSSING = "PedestrianStartedCrossing"
    PEDESTRIAN_CLEARED_CROSSING = "PedestrianClearedCrossing"


class TrafficEvent(Model):
    event_id: str
    kind: TrafficEventType
    timestamp: float = Field(ge=0)
    track_uid: str
    session_id: str
    source: str
    confidence: float = Field(ge=0, le=1)
    geometry_version: str
    calibration_version: str
    approach: str
    lane_id: str | None = None


class MetricEstimate(Model):
    value: float | None = None
    unit: str = Field(min_length=1)
    unavailable_reason: str | None = None


class IdentityDiagnostics(Model):
    # True ID switches/fragmentation need labeled identity ground truth; never fabricate zero.
    id_switches: int | None = None
    track_fragmentation: int | None = None
    reconciled_id_changes: int = 0
    duplicate_arrivals: int = 0
    lost_tracks: int = 0
    recovered_tracks: int = 0


class RoadTrackState(Model):
    track_uid: str
    pedestrian_state: str = "pedestrian_unknown"
    current_approach: str | None = None
    current_lane: str | None = None
    movement_direction: str | None = None
    distance_to_stop_line: MetricEstimate = Field(
        default_factory=lambda: MetricEstimate(unit="m", unavailable_reason="unobserved")
    )
    estimated_speed_mps: MetricEstimate = Field(
        default_factory=lambda: MetricEstimate(unit="m/s", unavailable_reason="unobserved")
    )
    crossed_entry_line: bool = False
    crossed_stop_line: bool = False
    crossed_exit_line: bool = False
    queued: bool = False
    wait_seconds: float = Field(default=0, ge=0)


class TrafficEstimation(Model):
    timestamp: float
    calibration_status: str
    reasons: tuple[str, ...] = ()
    tracks: tuple[RoadTrackState, ...] = ()
    events: tuple[TrafficEvent, ...] = ()
    metrics: dict[str, dict[str, MetricEstimate]] = Field(default_factory=dict)
    identity: IdentityDiagnostics = Field(default_factory=IdentityDiagnostics)

    def unavailable(
        self, reason: str, *, calibration_status: str | None = None
    ) -> "TrafficEstimation":
        return self.model_copy(
            update={
                "calibration_status": calibration_status or self.calibration_status,
                "reasons": tuple(dict.fromkeys((*self.reasons, reason))),
                "events": (),
                "metrics": {
                    a: {
                        k: MetricEstimate(unit=v.unit, unavailable_reason=reason)
                        for k, v in values.items()
                    }
                    for a, values in self.metrics.items()
                },
                "tracks": tuple(
                    t.model_copy(
                        update={
                            "distance_to_stop_line": MetricEstimate(
                                unit="m", unavailable_reason=reason
                            ),
                            "estimated_speed_mps": MetricEstimate(
                                unit="m/s", unavailable_reason=reason
                            ),
                        }
                    )
                    for t in self.tracks
                ),
            }
        )

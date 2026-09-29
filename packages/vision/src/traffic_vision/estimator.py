"""Bounded, timestamp-based track estimates. Hidden objects are never labelled visible."""

import math
from collections import deque
from dataclasses import dataclass, field

from traffic_vision.models import Detection, Lane, PedestrianState, TrackVisibility, TrajectoryPoint
from traffic_vision.settings import AnalyticsSettings


@dataclass
class Track:
    identity: int
    first_seen: float
    last_seen: float
    detection: Detection
    trajectory: deque[TrajectoryPoint] = field(default_factory=deque)
    lane: Lane = Lane.UNKNOWN
    speed: float | None = None
    visible: bool = True
    slow_frames: int = 0  # Diagnostic only; never used as a time threshold.
    candidate_since: float | None = None
    waiting_started_at: float | None = None
    current_wait: float = 0
    total_wait: float = 0
    queued: bool = False
    state: TrackVisibility = TrackVisibility.CONFIRMED_VISIBLE
    tracking_confidence: float = 1
    motion_confidence: float = 0
    pedestrian_state: PedestrianState = PedestrianState.UNKNOWN
    pedestrian_since: float | None = None

    def reset_wait(self) -> None:
        self.slow_frames = 0
        self.candidate_since = None
        self.waiting_started_at = None
        self.current_wait = 0
        self.queued = False


class TrackStateEstimator:
    def __init__(self, settings: AnalyticsSettings) -> None:
        self.settings = settings
        self.aliases: dict[int, int] = {}
        self.ambiguous = False

    def missing(self, track: Track, now: float) -> None:
        if now < track.last_seen:
            raise ValueError("Track clock must be monotonic")
        age = now - track.last_seen
        track.visible = False
        if age > self.settings.lost_track_seconds:
            track.state = TrackVisibility.EXPIRED
        elif age > self.settings.occlusion_hold_seconds:
            track.state = TrackVisibility.LOST
        elif age > self.settings.prediction_after_seconds:
            track.state = TrackVisibility.PREDICTED
        else:
            track.state = TrackVisibility.TEMPORARILY_OCCLUDED
        track.tracking_confidence = max(0, 1 - age / self.settings.occlusion_hold_seconds)
        if track.state in (TrackVisibility.LOST, TrackVisibility.EXPIRED):
            track.queued = False
        elif track.waiting_started_at is not None:
            track.current_wait = max(0, now - track.waiting_started_at)

    def resolve(
        self, observed: dict[int, Detection], tracks: dict[int, Track], now: float
    ) -> dict[int, Detection]:
        # Only mutual unique, same-class, close matches within the hold window may recover ID.
        self.ambiguous = False
        mapped: dict[int, Detection] = {}
        for identity, detection in observed.items():
            canonical = self.aliases.get(identity, identity)
            if canonical in mapped:
                self.ambiguous = True
                mapped[identity] = detection
            else:
                mapped[canonical] = detection
        proposals: dict[int, list[int]] = {}
        for identity, detection in mapped.items():
            if identity in tracks:
                continue
            proposals[identity] = [
                old
                for old, t in tracks.items()
                if old not in mapped
                and t.detection.kind == detection.kind
                and 0 < now - t.last_seen <= self.settings.occlusion_hold_seconds
                and math.hypot(
                    t.detection.footpoint.x - detection.footpoint.x,
                    t.detection.footpoint.y - detection.footpoint.y,
                )
                <= self.settings.id_recovery_distance_px
            ]
        for identity, candidates in proposals.items():
            if len(candidates) != 1:
                self.ambiguous |= len(candidates) > 1
                continue
            old = candidates[0]
            if sum(old in ids for ids in proposals.values()) != 1:
                self.ambiguous = True
                continue
            if len(self.aliases) >= self.settings.max_session_tracks:
                raise RuntimeError("ID alias budget exceeded")
            self.aliases[identity] = old
            mapped[old] = mapped.pop(identity)
        return mapped

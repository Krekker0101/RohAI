"""Causal sensor/tracker surrogate, with per-camera delayed delivery and explicit health."""

import math
import random
from collections import deque
from dataclasses import dataclass

from traffic_core.models import (
    ApproachState,
    Direction,
    ObjectKind,
    ObservationEvidence,
    ObservationStatus,
    TrafficState,
)
from traffic_core.state import build_approach_state

from traffic_simulator.twin_dynamics import stable_seed
from traffic_simulator.twin_models import (
    GroundTruthState,
    ObservedTrack,
    ObservedTrafficState,
    PerceptionProfile,
)

HEALTH_KEYS = (
    "camera_connected",
    "frame_fresh",
    "frame_rate_adequate",
    "image_not_frozen",
    "not_black",
    "not_underexposed",
    "not_overexposed",
    "not_blurred",
    "no_major_occlusion_suspected",
    "roi_visible",
    "inference_alive",
    "analytics_alive",
    "calibration_available",
    "calibration_fresh",
)


@dataclass
class TrackMemory:
    public_id: str
    first_seen: float
    last_seen: float
    position_m: float
    generation: int = 0


@dataclass(frozen=True)
class CameraFrame:
    captured_s: float
    delivered_s: float
    direction: Direction
    tracks: tuple[ObservedTrack, ...]
    arrivals_per_minute: float
    pedestrian_arrivals_per_minute: float
    reasons: tuple[str, ...]


class ObservationModel:
    """GroundTruthState enters this sensor boundary only, never the policy port."""

    def __init__(self, profile: PerceptionProfile, seed: int, waiting_weight: float = 0.05) -> None:
        self.profile, self.seed, self.waiting_weight = profile, seed, waiting_weight
        self.next_capture = 0.0
        self.capture_index = 0
        self.last_capture = -1.0
        self.last_truth_s = -1.0
        self.last_observed_s = -1.0
        self.pending: deque[CameraFrame] = deque()
        self.latest: dict[Direction, CameraFrame] = {}
        self.delivered_times: dict[Direction, deque[float]] = {d: deque() for d in Direction}
        self.memory: dict[int, TrackMemory] = {}
        self.occluded_until: dict[int, float] = {}
        self.recent: dict[tuple[Direction, bool], deque[float]] = {
            (direction, pedestrian): deque()
            for direction in Direction
            for pedestrian in (False, True)
        }
        self.diagnostics = dict.fromkeys(
            (
                "missed",
                "false_positives",
                "id_switches",
                "occlusions",
                "queue_undercount",
                "dropped_frames",
                "lost_tracks",
                "recovered_tracks",
            ),
            0,
        )

    def _faults(self, direction: Direction, at: float) -> dict[str, float]:
        return {
            f.mode: f.start_s
            for f in self.profile.faults
            if direction in f.directions and f.start_s <= at < f.end_s
        }

    def ingest(self, truth: GroundTruthState) -> None:
        now, p = truth.timestamp_s, self.profile
        if now <= self.last_truth_s or now < self.last_observed_s:
            raise ValueError("Observation input timestamps must strictly increase")
        self.last_truth_s = now
        if now + 1e-9 < self.next_capture:
            return
        self.capture_index += 1
        self.next_capture = (math.floor(now * p.fps + 1e-9) + 1) / p.fps
        capture_window_start = self.last_capture if self.last_capture >= 0 else now - 1 / p.fps
        # Bounded tracker state; never copy latent arrival times into measured wait.
        for identity in list(self.memory):
            if now - self.memory[identity].last_seen > p.track_ttl_s:
                del self.memory[identity]
                self.diagnostics["lost_tracks"] += 1
        self.occluded_until = {key: end for key, end in self.occluded_until.items() if end > now}
        for direction in Direction:
            rng = random.Random(stable_seed(self.seed, "camera", direction, self.capture_index))
            faults = self._faults(direction, now)
            if "outage" in faults or rng.random() > p.camera_availability:
                continue
            if "freeze" in faults:
                continue  # The last image persists with its original capture timestamp.
            if rng.random() < p.frame_drop_probability:
                self.diagnostics["dropped_frames"] += 1
                continue
            tracks: list[ObservedTrack] = []
            for entity in truth.queued:
                if entity.approach != direction:
                    continue
                erng = random.Random(
                    stable_seed(self.seed, "entity", entity.entity_id, self.capture_index)
                )
                if erng.random() < p.occlusion_probability:
                    self.occluded_until[entity.entity_id] = now + p.occlusion_duration_s
                if "occlusion" in faults or self.occluded_until.get(entity.entity_id, -1) > now:
                    self.diagnostics["occlusions"] += 1
                    continue
                if erng.random() < p.missed_detection_probability:
                    self.diagnostics["missed"] += 1
                    continue
                if (
                    entity.kind != ObjectKind.PEDESTRIAN
                    and erng.random() < p.queue_undercount_probability
                ):
                    self.diagnostics["queue_undercount"] += 1
                    continue
                distance = max(
                    0,
                    entity.distance_to_stop_m * (1 + p.calibration_scale_error)
                    + erng.gauss(0, p.position_noise_std_m),
                )
                old = self.memory.get(entity.entity_id)
                switched = old is not None and erng.random() < p.id_switch_probability
                if switched:
                    self.diagnostics["id_switches"] += 1
                if old is None or switched:
                    token = stable_seed(
                        self.seed, "observed-id", entity.entity_id, self.capture_index
                    )
                    memory = TrackMemory(f"track-{token:x}", now, now, distance)
                    speed = None
                else:
                    memory = old
                    assert memory is not None
                    if now - memory.last_seen > 1.5 / p.fps:
                        self.diagnostics["recovered_tracks"] += 1
                    speed = abs(distance - memory.position_m) / (now - memory.last_seen)
                # Timestamp starts at first observation, NOT entity.arrived_at_s.
                wait = max(0, now - memory.first_seen + erng.gauss(0, p.wait_noise_std_s))
                memory.last_seen, memory.position_m = now, distance
                self.memory[entity.entity_id] = memory
                tracks.append(
                    ObservedTrack(
                        track_id=memory.public_id,
                        kind=entity.kind,
                        approach=direction,
                        lane_id=entity.lane_id,
                        distance_to_stop_m=None if "calibration_invalid" in faults else distance,
                        estimated_speed_mps=None if "calibration_invalid" in faults else speed,
                        observed_wait_s=wait,
                    )
                )
                # Sensor abstraction of a visible entry crossing; ID switches do not create entries.
                if (
                    entity.admitted_at_s is not None
                    and capture_window_start < entity.admitted_at_s <= now
                ):
                    self.recent[(direction, entity.kind == ObjectKind.PEDESTRIAN)].append(now)
            if rng.random() < p.false_positives_per_frame:
                self.diagnostics["false_positives"] += 1
                tracks.append(
                    ObservedTrack(
                        track_id=f"fp-{direction}-{self.capture_index}",
                        kind=ObjectKind.CAR,
                        approach=direction,
                        lane_id=f"{direction}-0",
                        distance_to_stop_m=max(0, rng.gauss(5, 1)),
                        estimated_speed_mps=None,
                        observed_wait_s=0,
                    )
                )
            for pedestrian in (False, True):
                history = self.recent[(direction, pedestrian)]
                while history and history[0] <= now - 60:
                    history.popleft()
            denominator = max(1 / p.fps, min(now, 60))
            self.pending.append(
                CameraFrame(
                    captured_s=now,
                    delivered_s=now
                    + p.camera_delay_s
                    + p.inference_latency_s
                    + (5.0 if "latency_spike" in faults else 0.0),
                    direction=direction,
                    tracks=tuple(tracks),
                    arrivals_per_minute=len(self.recent[(direction, False)]) * 60 / denominator,
                    pedestrian_arrivals_per_minute=len(self.recent[(direction, True)])
                    * 60
                    / denominator,
                    reasons=tuple(f for f in faults if f != "latency_spike"),
                )
            )
        self.last_capture = now
        self.pending = deque(sorted(self.pending, key=lambda frame: frame.delivered_s))

    def observe(self, now: float) -> ObservedTrafficState:
        if not math.isfinite(now) or now < max(self.last_truth_s, self.last_observed_s):
            raise ValueError("Observation read clock must be finite and current")
        self.last_observed_s = now
        while self.pending and self.pending[0].delivered_s <= now + 1e-9:
            delivery = self.pending.popleft()
            previous = self.latest.get(delivery.direction)
            if previous is not None and delivery.captured_s <= previous.captured_s:
                continue  # Late frames cannot replace a newer delivered observation.
            self.latest[delivery.direction] = delivery
            history = self.delivered_times[delivery.direction]
            history.append(delivery.captured_s)
            while history and history[0] < delivery.captured_s - 5:
                history.popleft()
        approaches: list[ApproachState] = []
        tracks: list[ObservedTrack] = []
        all_pedestrian_waits: list[float] = []
        for direction in Direction:
            frame = self.latest.get(direction)
            age = now - frame.captured_s if frame else None
            health: dict[str, bool | None] = dict.fromkeys(HEALTH_KEYS, True)
            health["frame_fresh"] = age is not None and age <= self.profile.stale_after_s
            times = self.delivered_times[direction]
            observed_fps = (
                (len(times) - 1) / (times[-1] - times[0]) if len(times) > 1 else self.profile.fps
            )
            health["frame_rate_adequate"] = observed_fps >= self.profile.minimum_fps and (
                age is None
                or age
                <= self.profile.camera_delay_s
                + self.profile.inference_latency_s
                + 1 / self.profile.minimum_fps
            )
            faults = self._faults(direction, now)
            health["camera_connected"] = "outage" not in faults
            health["image_not_frozen"] = not (
                "freeze" in faults and now - faults["freeze"] >= self.profile.freeze_detection_s
            )
            # Calibration/visibility failure is reported only once captured evidence is delivered.
            reasons = frame.reasons if frame else ("no_delivered_frame",)
            health["roi_visible"] = "occlusion" not in reasons
            health["calibration_available"] = "calibration_invalid" not in reasons
            failures = tuple(sorted(set(reasons) | {k for k, good in health.items() if not good}))
            evidence = ObservationEvidence(
                observation_status=ObservationStatus.OBSERVED
                if not failures
                else ObservationStatus.DEGRADED,
                measurement_timestamp=frame.captured_s if frame else None,
                measurement_age_ms=age * 1000 if age is not None else None,
                source_camera_ids=(f"twin-camera-{direction}",),
                camera_health=health,
                calibration_status="valid" if health["calibration_available"] else "suspect",
                visibility_score=1 - self.profile.occlusion_probability,
                detection_coverage=1 - self.profile.missed_detection_probability,
                tracking_quality=1 - self.profile.id_switch_probability,
                measurement_confidence=0.9 if not failures else 0,
                failure_reasons=failures,
            )
            measured = frame.tracks if frame else ()
            vehicles = [
                (t.kind, t.observed_wait_s) for t in measured if t.kind != ObjectKind.PEDESTRIAN
            ]
            peds = [t.observed_wait_s for t in measured if t.kind == ObjectKind.PEDESTRIAN]
            base = build_approach_state(
                direction,
                vehicles,
                frame.arrivals_per_minute if frame else 0,
                waiting_weight=self.waiting_weight,
                moderate_queue=5,
                high_queue=15,
                measurement_timestamp=frame.captured_s if frame else now,
            )
            values = base.model_dump()
            values.update(
                observation=evidence,
                pedestrian_count_waiting=len(peds),
                pedestrian_mean_wait_seconds=sum(peds) / len(peds) if peds else 0,
                pedestrian_oldest_wait_seconds=max(peds, default=0),
                pedestrian_arrival_rate=frame.pedestrian_arrivals_per_minute if frame else 0,
                pedestrian_crossing_count=None,
            )  # Not inferred from latent crossing count.
            approaches.append(ApproachState.model_validate(values))
            if evidence.reliable:
                tracks.extend(measured)
                all_pedestrian_waits.extend(peds)
        return ObservedTrafficState(
            traffic=TrafficState(
                simulation_time=now,
                approaches=tuple(approaches),
                pedestrians_waiting=len(all_pedestrian_waits),
                pedestrian_oldest_wait_seconds=max(all_pedestrian_waits, default=0),
            ),
            tracks=tuple(tracks),
        )

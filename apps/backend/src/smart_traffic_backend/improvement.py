"""Offline improvement evidence. Never trains, mutates weights or actuates hardware."""

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any
from uuid import uuid4

from traffic_core.models import TrafficState


class ImprovementLog:
    """Local bounded durable journal; no images, predictions are never ground truth."""

    def __init__(self, path: Path, capacity: int = 10_000, retention_s: float = 604800) -> None:
        if capacity < 1 or retention_s <= 0:
            raise ValueError("Positive retention required")
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.capacity, self.retention_s = path, capacity, retention_s
        self.session_id = str(uuid4())
        self.previous: str | None = None
        self.pending: dict[int, tuple[dict[str, Any] | None, str | None]] = {}
        self.drift: DriftMonitor | None = None
        with self.connect() as db:
            db.execute("PRAGMA auto_vacuum=FULL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS decisions "
                "(event TEXT PRIMARY KEY, session TEXT, sequence INTEGER, "
                "timestamp REAL, body TEXT)"
            )
            db.execute("CREATE INDEX IF NOT EXISTS decision_time ON decisions(timestamp)")
            db.execute("CREATE INDEX IF NOT EXISTS decision_seq ON decisions(session, sequence)")
            self._prune(db)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=0.2)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _prune(self, db: sqlite3.Connection) -> None:
        db.execute("DELETE FROM decisions WHERE timestamp < ?", (time.time() - self.retention_s,))
        db.execute(
            "DELETE FROM decisions WHERE event IN "
            "(SELECT event FROM decisions ORDER BY timestamp DESC, rowid DESC LIMIT -1 OFFSET ?)",
            (self.capacity,),
        )

    def decision(
        self,
        observation: TrafficState,
        sequence: int,
        cameras: list[str],
        vision_version: str,
        policy_version: str,
        stages: dict[str, Any],
        acknowledgement: dict[str, Any] | None,
        collection_evidence: dict[str, float | bool] | None = None,
    ) -> str:
        event = str(uuid4())
        confidence = min(a.observation.measurement_confidence for a in observation.approaches)
        queue = sum(a.queue_length or 0 for a in observation.approaches)
        queue_known = all(a.queue_length is not None for a in observation.approaches)
        accepted = all(
            stages["requested"][key] == stages["safety_approved"][key] for key in ("phase", "stage")
        )
        tags = []
        if collection_evidence:
            tags.extend(hard_case_tags(collection_evidence))
        if confidence < 0.5:
            tags.append("low_confidence")
        if not accepted:
            tags.append("safety_rejection")
        reasons = {
            reason for a in observation.approaches for reason in a.observation.failure_reasons
        }
        tags.extend(
            hard_case_tags(
                {
                    "night_weather_degradation": any(
                        word in reason
                        for reason in reasons
                        for word in ("dark", "night", "weather", "glare", "contrast")
                    ),
                    "unusual_scene": any("unknown" in reason for reason in reasons),
                }
            )
        )
        counts: dict[str, int] = {}
        for approach in observation.approaches:
            for kind, count in (
                approach.counts.model_dump() if approach.counts is not None else {}
            ).items():
                counts[str(kind)] = counts.get(str(kind), 0) + count
        features = {
            "input": float(sum(counts.values())),
            "class_distribution": sum(
                (index + 1) * counts[key] for index, key in enumerate(sorted(counts))
            )
            / max(sum(counts.values()), 1),
            "confidence": confidence,
            "camera_condition": float(len(reasons)),
            "tracking": sum(
                a.observation.uncertainty.tracking_confidence for a in observation.approaches
            )
            / 4,
            "queue_distribution": float(queue),
            "policy_intervention": float(not accepted),
        }
        if self.drift is None:
            self.drift = DriftMonitor(features)
        drift = (
            self.drift.observe(features)
            if queue_known
            else {
                "evaluation_required": True,
                "quality_regression": "UNKNOWN",
                "reason": "incomplete_observation",
            }
        )
        record = {
            "event_id": event,
            "session_id": self.session_id,
            "timestamp": stages.get("requested_at") or time.time(),
            "camera_ids": cameras,
            "observation_schema_version": "traffic-core-1",
            "observation": observation.model_dump(mode="json"),
            "observation_confidence": confidence,
            "vision_model_version": vision_version,
            "controller_policy_version": policy_version,
            "proposed_action": stages["requested"],
            "ai_proposed_action": None,
            "safety_accepted": accepted,
            "executed_action": stages["command_sent"],
            "execution_evidence": "command_sent_only",
            "hardware_acknowledgement": acknowledgement,
            "subsequent_outcomes": None,
            "fallback_reason": stages.get("error")
            or ("safety_intervention" if not accepted else None),
            "review_tags": tags,
            "collection_evidence": collection_evidence or {},
            "label_status": "UNVERIFIED_PREDICTION",
            "queue_total": queue if queue_known else None,
            "drift": drift,
        }
        if len(json.dumps(record, allow_nan=False).encode()) > 262144:
            raise ValueError("Decision exceeds 256 KiB retention envelope")
        with self.connect() as db:
            if self.previous:
                old = db.execute(
                    "SELECT body FROM decisions WHERE event=?", (self.previous,)
                ).fetchone()
                if old:
                    prior = json.loads(old[0])
                    if (
                        queue_known
                        and prior["queue_total"] is not None
                        and abs(queue - prior["queue_total"]) >= 10
                    ):
                        tags.append("large_queue_change")
                    prior["subsequent_outcomes"] = {
                        "observed_at": record["timestamp"],
                        "queue_total": queue if queue_known else None,
                        "observation_confidence": confidence,
                    }
                    db.execute(
                        "UPDATE decisions SET body=? WHERE event=?",
                        (json.dumps(prior, allow_nan=False), self.previous),
                    )
            db.execute(
                "INSERT INTO decisions VALUES (?, ?, ?, ?, ?)",
                (
                    event,
                    self.session_id,
                    sequence,
                    record["timestamp"],
                    json.dumps(record, allow_nan=False),
                ),
            )
            self._prune(db)
        self.previous = event
        pending = self.pending.pop(sequence, None)
        if pending is not None:
            self.proposal(sequence, *pending)
        return event

    def proposal(self, sequence: int, proposal: dict[str, Any] | None, error: str | None) -> None:
        with self.connect() as db:
            row = db.execute(
                "SELECT event, body FROM decisions WHERE session=? AND sequence=?",
                (self.session_id, sequence),
            ).fetchone()
            if row is None:
                self.pending[sequence] = (proposal, error)
                while len(self.pending) > 16:
                    del self.pending[next(iter(self.pending))]
                return
            record = json.loads(row[1])
            record["ai_proposed_action"] = proposal
            record["fallback_reason"] = error or record["fallback_reason"]
            if proposal and proposal.get("phase") != record["proposed_action"].get("phase"):
                if "policy_disagreement" not in record["review_tags"]:
                    record["review_tags"].append("policy_disagreement")
            db.execute(
                "UPDATE decisions SET body=? WHERE event=?",
                (json.dumps(record, allow_nan=False), row[0]),
            )

    def records(self) -> list[dict[str, Any]]:
        with self.connect() as db:
            self._prune(db)
            return [
                json.loads(row[0])
                for row in db.execute("SELECT body FROM decisions ORDER BY timestamp, rowid")
            ]


class DriftMonitor:
    """Fixed reference, bounded window; distance triggers evaluation, not a quality claim."""

    categories = (
        "input",
        "class_distribution",
        "confidence",
        "camera_condition",
        "tracking",
        "queue_distribution",
        "policy_intervention",
    )

    def __init__(
        self, reference: dict[str, float], threshold: float = 0.2, window: int = 100
    ) -> None:
        import math
        from collections import deque

        if set(reference) != set(self.categories) or window < 2 or threshold <= 0:
            raise ValueError("Complete drift reference and positive limits required")
        if not all(math.isfinite(value) for value in reference.values()):
            raise ValueError("Finite reference required")
        self.reference, self.threshold = dict(reference), threshold
        self.samples: Any = deque(maxlen=window)

    def observe(self, sample: dict[str, float]) -> dict[str, Any]:
        import math

        if set(sample) != set(self.categories) or not all(
            math.isfinite(value) for value in sample.values()
        ):
            raise ValueError("Complete finite drift sample required")
        self.samples.append(dict(sample))
        distances = {
            key: abs(sum(s[key] for s in self.samples) / len(self.samples) - baseline)
            / max(abs(baseline), 1.0)
            for key, baseline in self.reference.items()
        }
        return {
            "distances": distances,
            "evaluation_required": any(value > self.threshold for value in distances.values()),
            "quality_regression": "UNKNOWN",
            "sample_count": len(self.samples),
        }


def hard_case_tags(evidence: dict[str, float | bool]) -> list[str]:
    """Only supplied evidence is classified; absent signals remain unknown."""
    tags = []
    thresholds = {
        "low_confidence": ("confidence", 0.5),
        "track_fragmentation": ("fragmentations", 0),
        "large_queue_change": ("queue_delta", 10),
    }
    for tag, (key, limit) in thresholds.items():
        if key in evidence and (
            float(evidence[key]) < limit
            if tag == "low_confidence"
            else abs(float(evidence[key])) > limit
        ):
            tags.append(tag)
    for key in (
        "model_disagreement",
        "unusual_scene",
        "night_weather_degradation",
        "policy_disagreement",
        "safety_rejection",
    ):
        if evidence.get(key) is True:
            tags.append(key)
    return tags


class ImprovementWriter:
    """Bounded best-effort off-thread persistence; disk cannot delay control ticks."""

    def __init__(self, log: ImprovementLog) -> None:
        self.log = log
        self.queue: Queue[tuple[str, tuple[Any, ...]]] = Queue(maxsize=128)
        self.dropped = 0
        self.errors = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def submit(self, operation: str, *args: Any) -> None:
        if self._stop.is_set():
            self.dropped += 1
            return
        if self._thread is None:
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="improvement-writer"
            )
            self._thread.start()
        try:
            self.queue.put_nowait((operation, args))
        except Full:
            self.dropped += 1

    def _run(self) -> None:
        while not self._stop.is_set() or not self.queue.empty():
            try:
                operation, args = self.queue.get(timeout=0.1)
            except Empty:
                continue
            try:
                if operation == "decision":
                    self.log.decision(*args)
                elif operation == "proposal":
                    self.log.proposal(*args)
                else:
                    raise ValueError("Unknown improvement operation")
            except Exception:
                self.errors += 1
            finally:
                self.queue.task_done()

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)

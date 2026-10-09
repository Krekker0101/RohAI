"""Observed-only sidecar. This module has no hardware/serial/command dependencies."""

import copy
import json
import queue
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Literal

from traffic_core.models import TrafficState
from traffic_core.safety import SafetyController
from traffic_core.settings import Timing

from traffic_simulator.learning.intelligence_contracts import (
    ControlGeometry,
    PolicyState,
    SafetyConstraints,
    TrafficObservation,
)
from traffic_simulator.learning.intelligence_engine import IntelligenceEngine
from traffic_simulator.learning.intelligence_policy import PolicyAdapter
from traffic_simulator.learning.observation import ObservationStack, encode
from traffic_simulator.learning.policy import CurrentAdaptiveController
from traffic_simulator.learning.safe_actor import IsolatedPolicy
from traffic_simulator.learning.safety import SafetySupervisor
from traffic_simulator.twin_models import ObservedTrafficState


class ShadowIntelligence:
    def __init__(
        self,
        actor: IsolatedPolicy,
        timing: Timing,
        journal: Path,
        source: Literal["RECORDED VIDEO", "LIVE CAMERA"],
        geometry: ControlGeometry,
    ) -> None:
        self.actor, self.timing, self.source = actor, timing, source
        self.geometry = geometry
        self.journal = journal
        journal.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(journal) as db:
            db.execute("CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY, body TEXT)")
        self.inputs: queue.Queue[Any] = queue.Queue(maxsize=1)
        self.records: deque[dict[str, Any]] = deque(maxlen=128)
        self.stack = ObservationStack()
        self.sequence = 0
        self.dropped = 0
        self.failures = 0
        self.closed = False
        self.last_time = -1.0
        self.lock = threading.Lock()
        self.worker = threading.Thread(target=self._run, daemon=True, name="rohai-shadow-sidecar")
        self.worker.start()

    def submit(self, traffic: TrafficState, safety: SafetyController) -> None:
        if self.closed:
            return
        # Caller never waits for model, disk, network, or journal locks.
        task = (traffic, copy.deepcopy(safety), time.monotonic())
        try:
            self.inputs.put_nowait(task)
        except queue.Full:
            self.dropped += 1

    def _run(self) -> None:
        while not self.closed:
            try:
                task = self.inputs.get(timeout=0.1)
            except queue.Empty:
                continue
            traffic, actual_safety, submitted = task
            try:
                if traffic.simulation_time <= self.last_time:
                    continue
                self.last_time = traffic.simulation_time
                # Account for time spent in sidecar queue; missing evidence remains missing.
                age_ms = (time.monotonic() - submitted) * 1000
                approaches = tuple(
                    a.model_copy(
                        update={
                            "observation": a.observation.model_copy(
                                update={
                                    "measurement_age_ms": a.observation.measurement_age_ms + age_ms
                                    if a.observation.measurement_age_ms is not None
                                    else None
                                }
                            )
                        }
                    )
                    for a in traffic.approaches
                )
                traffic = TrafficState.model_validate(
                    {**traffic.model_dump(), "approaches": [a.model_dump() for a in approaches]}
                )
                observed = ObservedTrafficState(source="live_camera_observation", traffic=traffic)
                supervisor = SafetySupervisor(self.timing)
                supervisor.machine = actual_safety
                supervisor.phase_started_at = actual_safety.entered_at
                # No invented service history: age-based fairness cannot be reconstructed here.
                supervisor.last_service = dict.fromkeys(
                    supervisor.last_service, traffic.simulation_time
                )
                supervisor.pending_duration = actual_safety.green_seconds
                actual_signals = actual_safety.snapshot(traffic.simulation_time).model_dump(
                    mode="json"
                )
                raw = encode(observed, supervisor)
                observation = TrafficObservation(**raw.model_dump(), geometry=self.geometry)
                history = self.stack.append(raw)
                state = PolicyState(
                    sequence=self.sequence,
                    history=tuple(tuple(float(v) for v in row) for row in history),
                )
                constraints = SafetyConstraints(timing=self.timing)
                baseline = PolicyAdapter(CurrentAdaptiveController(self.timing))
                baseline_started = time.perf_counter()
                baseline_decision = baseline.decide(observation, state, constraints)
                baseline_ms = (time.perf_counter() - baseline_started) * 1000
                baseline_supervisor = copy.deepcopy(supervisor)
                baseline_supervisor.apply(baseline_decision.proposed, observed)
                engine = IntelligenceEngine(self.actor, supervisor, constraints, mode="SHADOW")
                # Isolated actor lifetime belongs to the sidecar, not this snapshot validator.
                audit = engine.decide(observation, state, observed, traffic.simulation_time)
                record = {
                    "observation_id": f"{self.source}:{self.sequence}:{traffic.simulation_time}",
                    "policy_id": self.actor.metadata.policy_id,
                    "model_version": self.actor.metadata.version,
                    "source": self.source,
                    "physical_execution": False,
                    "actual_signals": actual_signals,
                    "baseline": baseline_decision.model_dump(mode="json"),
                    "baseline_safety": baseline_supervisor.outcome.reason,
                    "baseline_inference_ms": baseline_ms,
                    "candidate": audit.model_dump(mode="json"),
                    "differs": baseline_decision.proposed != audit.requested,
                    "confidence": min(a.observation.measurement_confidence for a in approaches),
                    "freshness_ms": max(
                        (a.observation.measurement_age_ms or 0) for a in approaches
                    ),
                    "counterfactual_delay": None,
                    "simulation_outcomes": None,
                    "warnings": [
                        "Reference adaptive proposal; physical controller unchanged",
                        "Service history and geometry require explicit field calibration",
                    ],
                    "compute_ms": self.actor.last_compute_ms,
                }
                self.sequence += 1
                with sqlite3.connect(self.journal, timeout=0.2) as db:
                    db.execute("INSERT INTO decisions(body) VALUES (?)", (json.dumps(record),))
                    db.execute(
                        "DELETE FROM decisions WHERE id <= (SELECT MAX(id)-5000 FROM decisions)"
                    )
                with self.lock:
                    self.records.append(record)
            except Exception as exc:
                self.failures += 1
                with self.lock:
                    self.records.append(
                        {
                            "source": self.source,
                            "error": type(exc).__name__,
                            "physical_execution": False,
                            "policy_id": self.actor.metadata.policy_id,
                            "model_version": self.actor.metadata.version,
                        }
                    )

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            records = list(self.records)
        return {
            "state": "STOPPED" if self.closed else "RUNNING",
            "source": self.source,
            "records": records,
            "dropped_inputs": self.dropped,
            "failures": self.failures,
            "drift": "NOT VERIFIED: no field reference distribution",
            "journal_retention": 5000,
            "physical_execution": False,
        }

    def close(self) -> None:
        self.closed = True
        self.actor.close()
        self.worker.join(timeout=1)

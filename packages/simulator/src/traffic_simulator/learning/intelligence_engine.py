"""Bounded actor inference and existing independent safety; SIMULATION/SHADOW only."""

import queue
import threading
import time
from collections import deque
from typing import Any, Literal

from traffic_simulator.learning.intelligence_contracts import (
    ControlAction,
    ControlPolicy,
    DecisionAudit,
    PolicyDecision,
    PolicyState,
    SafetyConstraints,
    TrafficObservation,
)
from traffic_simulator.learning.intelligence_policy import fixed_adapter
from traffic_simulator.learning.safe_actor import IsolatedPolicy
from traffic_simulator.learning.safety import SafetySupervisor
from traffic_simulator.twin_models import ObservedTrafficState


class IntelligenceEngine:
    def __init__(
        self,
        policy: ControlPolicy,
        supervisor: SafetySupervisor,
        constraints: SafetyConstraints | None = None,
        mode: Literal["SIMULATION", "SHADOW"] = "SIMULATION",
    ) -> None:
        if mode not in ("SIMULATION", "SHADOW") or mode not in policy.metadata.allowed_modes:
            raise PermissionError("Intelligence Engine cannot activate physical outputs")
        self.constraints = constraints or SafetyConstraints(timing=supervisor.timing)
        if self.constraints.timing != supervisor.timing:
            raise ValueError("Policy/safety timing mismatch")
        self.policy, self.supervisor = policy, supervisor
        self.mode = mode
        self.isolated = isinstance(policy, IsolatedPolicy)
        if mode == "SHADOW" and not self.isolated:
            raise PermissionError("SHADOW requires a terminable numeric actor process")
        self.requests: queue.Queue[Any] = queue.Queue(maxsize=1)
        self.results: queue.Queue[Any] = queue.Queue(maxsize=1)
        self.quarantined = isinstance(policy, IsolatedPolicy) and policy.closed
        self.closed = False
        self.audit: deque[DecisionAudit] = deque(maxlen=256)
        self.worker = threading.Thread(
            target=self._worker, daemon=True, name="rohai-policy-inference"
        )
        if not self.isolated:
            self.worker.start()

    def _worker(self) -> None:
        while True:
            task = self.requests.get()
            if task is None:
                return
            observation, state = task
            result: PolicyDecision | Exception
            try:
                result = self.policy.decide(observation, state, self.constraints)
            except Exception as exc:
                result = exc
            self.results.put(result)

    def decide(
        self,
        observation: TrafficObservation,
        state: PolicyState,
        observed: ObservedTrafficState,
        now_s: float,
    ) -> DecisionAudit:
        if self.closed:
            raise RuntimeError("Engine closed")
        if (
            observation.timestamp_s != observed.traffic.simulation_time
            or now_s != observed.traffic.simulation_time
        ):
            raise ValueError("Actor/safety snapshot clocks differ")
        reason = None
        from traffic_simulator.learning.contracts import FEATURES
        from traffic_simulator.learning.observation import encode

        canonical = encode(observed, self.supervisor)
        for index, feature in enumerate(FEATURES):
            if feature.name.endswith(("departure_rate", "downstream_occupancy")):
                continue
            if (
                self.mode == "SHADOW"
                and observation.valid[index] == 0
                and (
                    feature.name.endswith(("time_since_service_s", "has_received_service"))
                    or feature.name in ("phase_age_s", "pending_budget_s")
                )
            ):
                continue  # Camera runtime may not expose prior service/pending budget history.
            if (
                abs(observation.values[index] - canonical.values[index]) > 1e-6
                or observation.valid[index] != canonical.valid[index]
                or observation.clipped[index] != canonical.clipped[index]
            ):
                raise ValueError("Actor/safety input features differ")
        expected = (*observation.values, *observation.valid, *observation.clipped, 1)
        if any(abs(a - b) > 1e-6 for a, b in zip(state.history[-1], expected, strict=True)):
            raise ValueError("Latest history differs from observation")
        signal = self.supervisor.machine.snapshot(now_s)
        if (
            observation.phase != signal.phase
            or observation.physical(f"stage.{signal.stage.value}") != 1
        ):
            raise ValueError("Actor signal differs from safety snapshot")
        for approach in observed.traffic.approaches:
            evidence = approach.observation
            age = observation.physical(f"{approach.direction.value}.measurement_age_s")
            if age is None or age > self.constraints.observation_ttl_s:
                reason = "stale_observation"
            if (
                not evidence.reliable
                or evidence.measurement_confidence < self.constraints.minimum_confidence
            ):
                reason = reason or "low_confidence"
        if self.audit and (
            state.sequence <= self.audit[-1].sequence or now_s <= self.audit[-1].timestamp_s
        ):
            raise ValueError("Decision sequence/time must increase")
        proposed = None
        started = time.perf_counter()
        # Observational SHADOW may display a proposal on unreliable input, but the
        # existing reason still forces baseline fallback and independent safety rejection.
        if (reason is None or self.mode == "SHADOW") and not self.quarantined:
            if not self.isolated:
                self.requests.put_nowait((observation, state))
            try:
                result = (
                    self.policy.decide(observation, state, self.constraints)
                    if self.isolated
                    else self.results.get(timeout=self.constraints.inference_timeout_s)
                )
                if isinstance(result, Exception):
                    reason = f"policy_error:{type(result).__name__}"
                else:
                    proposed = PolicyDecision.model_validate(
                        result.model_dump() if isinstance(result, PolicyDecision) else result
                    )
                    if proposed.metadata != self.policy.metadata:
                        reason = "policy_metadata_mismatch"
                    elif proposed.proposed.requested_phase not in self.constraints.allowed_phases:
                        reason = "unsupported_action"
            except (queue.Empty, TimeoutError):
                reason = "inference_timeout"
                self.quarantined = True
            except (TypeError, ValueError, RuntimeError, OSError):
                reason = "invalid_action"
                if self.isolated:
                    self.quarantined = True
        elif reason is None:
            reason = "actor_quarantined"
        elapsed = (time.perf_counter() - started) * 1000
        fallback = fixed_adapter(self.constraints).decide(observation, state, self.constraints)
        request = (
            proposed.proposed if proposed is not None and reason is None else fallback.proposed
        )
        if request.requested_phase not in self.constraints.allowed_phases:
            from traffic_core.models import Phase

            target = self.constraints.allowed_phases[0]
            request = ControlAction(
                requested_phase=target,
                requested_duration_s=self.constraints.timing.pedestrian_green_seconds
                if target == Phase.PEDESTRIAN
                else self.constraints.timing.min_green_seconds,
            )
        signals = self.supervisor.apply(request, observed)
        outcome = self.supervisor.outcome
        audit = DecisionAudit(
            sequence=state.sequence,
            timestamp_s=now_s,
            observation=observation,
            proposed=proposed,
            requested=ControlAction.model_validate(request.model_dump()),
            safety_reason=outcome.reason,
            accepted=outcome.accepted,
            signals=signals,
            effective_phase=outcome.effective_phase,
            effective_duration_s=outcome.effective_duration_s,
            fallback_reason=reason,
            inference_ms=elapsed,
        )
        self.audit.append(audit)
        return audit

    def close(self) -> None:
        self.closed = True
        if isinstance(self.policy, IsolatedPolicy):
            self.policy.close()
            return
        try:
            self.requests.put_nowait(None)
        except queue.Full:
            pass
        self.worker.join(timeout=0.1)

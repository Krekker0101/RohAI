"""Common adapters around existing fixed/adaptive/actuated/PPO implementations."""

from typing import Any

import numpy as np
from traffic_core.models import Direction, Phase, phase_for

from traffic_simulator.learning.intelligence_contracts import (
    ControlAction,
    PolicyDecision,
    PolicyMetadata,
    PolicyState,
    SafetyConstraints,
    TrafficObservation,
)
from traffic_simulator.learning.policy import FixedTimeController, TrafficPolicy


class PolicyAdapter:
    def __init__(self, policy: TrafficPolicy, metadata: PolicyMetadata | None = None) -> None:
        self.policy = policy
        self.metadata = metadata or PolicyMetadata(
            policy_id=policy.name, version="legacy-compatible-1"
        )

    def decide(
        self, observation: TrafficObservation, state: PolicyState, constraints: SafetyConstraints
    ) -> PolicyDecision:
        request = self.policy.propose(observation, np.asarray(state.history, dtype=np.float32))
        return PolicyDecision(
            metadata=self.metadata,
            proposed=ControlAction.model_validate(request.model_dump()),
            available_phases=constraints.allowed_phases,
            facts={
                f"{d.value}.{key}": observation.physical(f"{d.value}.{key}")
                for d in Direction
                for key in ("queue_count", "oldest_wait_s", "arrival_rate")
            },
        )


class MaxPressurePolicy:
    metadata = PolicyMetadata(policy_id="max_pressure", version="single-intersection-1")

    def decide(
        self, observation: TrafficObservation, state: PolicyState, constraints: SafetyConstraints
    ) -> PolicyDecision:
        scores = dict.fromkeys(Phase, 0.0)
        measured = observation.downstream_queue_counts
        # Without exit counters this is explicitly upstream max-queue, never oracle pressure.
        for d in Direction:
            queue = observation.physical(f"{d.value}.queue_count") or 0
            exit_queue = measured[d] if measured is not None else 0
            scores[phase_for(d)] += (
                (queue - exit_queue)
                * observation.geometry.lanes_per_approach
                / observation.geometry.headway_s
            )
            scores[Phase.PEDESTRIAN] += observation.physical(f"{d.value}.pedestrian_waiting") or 0
        current = observation.phase
        target = max(constraints.allowed_phases, key=lambda p: (scores[p], p == current))
        age = observation.physical("phase_age_s") or 0
        minimum = (
            constraints.timing.pedestrian_green_seconds
            if target == Phase.PEDESTRIAN
            else constraints.timing.min_green_seconds
        )
        duration = (
            min(constraints.timing.max_green_seconds, max(minimum, age + minimum))
            if target == current
            else minimum
        )
        return PolicyDecision(
            metadata=self.metadata,
            proposed=ControlAction(requested_phase=target, requested_duration_s=duration),
            available_phases=constraints.allowed_phases,
            facts={
                **{p.value: s for p, s in scores.items()},
                "pressure_mode": "measured_exit_counts"
                if measured is not None
                else "upstream_only",
            },
            interpretation="Aggregate pressure; no network optimality claim",
        )


class PPOPolicy:
    """Trusted local model adapter; loading checks are separate from actor inference."""

    def __init__(self, model: Any, weights_sha256: str, geometry_features: bool = True) -> None:
        self.model = model
        self.geometry_features = geometry_features
        self.metadata = PolicyMetadata(
            policy_id="rohai_ppo",
            version="ai20-control-2",
            learned=True,
            weights_sha256=weights_sha256,
        )

    def decide(
        self, observation: TrafficObservation, state: PolicyState, constraints: SafetyConstraints
    ) -> PolicyDecision:
        history = np.asarray(state.history, dtype=np.float32)
        if self.geometry_features:
            g = observation.geometry
            extra = np.tile(
                np.asarray(
                    [g.lanes_per_approach / 8, g.lane_length_m / 1000, g.headway_s / 10],
                    dtype=np.float32,
                ),
                (len(history), 1),
            )
            history = np.concatenate((history, extra), axis=1)
        index = int(self.model.predict(history, deterministic=True)[0])
        from traffic_simulator.learning.contracts import decode_action

        return PolicyDecision(
            metadata=self.metadata,
            proposed=ControlAction.model_validate(decode_action(index).model_dump()),
            available_phases=constraints.allowed_phases,
            facts={
                "action_index": float(index),
                "phase_age_s": observation.physical("phase_age_s"),
            },
        )


def fixed_adapter(constraints: SafetyConstraints) -> PolicyAdapter:
    return PolicyAdapter(FixedTimeController(constraints.timing))

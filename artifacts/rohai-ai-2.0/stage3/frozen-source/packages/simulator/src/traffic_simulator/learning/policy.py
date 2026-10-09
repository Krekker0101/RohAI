"""One request contract for fixed, existing adaptive, actuated and learned adapters."""

from collections.abc import Callable
from typing import Protocol

import numpy as np
from numpy.typing import NDArray
from traffic_core.controller import adaptive_green_seconds, next_phase
from traffic_core.models import Direction, Phase, SignalStage, phase_for
from traffic_core.settings import Timing

from traffic_simulator.learning.contracts import ControlRequest, ObservationV1, decode_action


class TrafficPolicy(Protocol):
    name: str

    def reset(self, seed: int) -> None: ...
    def propose(
        self, observation: ObservationV1, history: NDArray[np.float32]
    ) -> ControlRequest: ...


class FixedTimeController:
    name = "fixed"

    def __init__(self, timing: Timing) -> None:
        self.timing = timing

    def reset(self, seed: int) -> None:
        pass

    def target(self, obs: ObservationV1) -> Phase:
        phase = obs.phase
        if obs.physical(f"stage.{SignalStage.GREEN.value}") == 1:
            if (obs.physical("phase_age_s") or 0) >= (obs.physical("green_budget_s") or 0):
                return next_phase(phase)
            return phase
        return next(p for p in Phase if obs.physical(f"pending.{p.value}") == 1)

    def duration(self, obs: ObservationV1, phase: Phase) -> float:
        if phase == Phase.PEDESTRIAN:
            return self.timing.pedestrian_green_seconds
        return self.timing.fixed_green_seconds

    def propose(self, observation: ObservationV1, history: NDArray[np.float32]) -> ControlRequest:
        phase = self.target(observation)
        duration = self.duration(observation, phase)
        if phase == observation.phase and observation.physical("stage.green") == 1:
            duration = observation.physical("green_budget_s") or duration
        return ControlRequest(requested_phase=phase, requested_duration_s=duration)


class CurrentAdaptiveController(FixedTimeController):
    """Existing cyclic controller formula, with the same Timing and no hidden simulator inputs."""

    name = "adaptive"

    def duration(self, obs: ObservationV1, phase: Phase) -> float:
        if phase == Phase.PEDESTRIAN:
            return self.timing.pedestrian_green_seconds
        score = sum(
            obs.physical(f"{d.value}.traffic_score") or 0
            for d in Direction
            if phase_for(d) == phase
        )
        return adaptive_green_seconds(score, self.timing)


class ActuatedController(FixedTimeController):
    """Queue-responsive non-learning control, not true network max-pressure without exit sensors."""

    name = "actuated"

    def propose(self, observation: ObservationV1, history: NDArray[np.float32]) -> ControlRequest:
        if observation.physical("stage.green") != 1:
            return super().propose(observation, history)
        phase = observation.phase
        age = observation.physical("phase_age_s") or 0
        minimum = (
            self.timing.pedestrian_green_seconds
            if phase == Phase.PEDESTRIAN
            else self.timing.min_green_seconds
        )
        scores = {p: 0.0 for p in Phase}
        for direction in Direction:
            queue = observation.physical(f"{direction.value}.queue_count") or 0
            occupancy = observation.physical(f"{direction.value}.downstream_occupancy")
            # Exit saturation attenuates demand only if actually measured.
            scores[phase_for(direction)] += queue * (1 - occupancy if occupancy is not None else 1)
            scores[Phase.PEDESTRIAN] += (
                observation.physical(f"{direction.value}.pedestrian_waiting") or 0
            )
        target = max(Phase, key=lambda p: (scores[p], p == phase)) if age >= minimum else phase
        if age >= self.timing.max_green_seconds and target == phase:
            target = max((p for p in Phase if p != phase), key=lambda p: scores[p])
        # Gap out if demand moved elsewhere; otherwise extend within the identical safety cap.
        duration = (
            min(self.timing.max_green_seconds, max(minimum, age + self.timing.min_green_seconds))
            if target == phase
            else self.timing.min_green_seconds
        )
        if target == Phase.PEDESTRIAN:
            duration = max(duration, self.timing.pedestrian_green_seconds)
        return ControlRequest(requested_phase=target, requested_duration_s=duration)


class LearnedPolicy:
    """Inference adapter only. No network, optimizer or training is instantiated here."""

    name = "learned"

    def __init__(
        self,
        predictor: Callable[[NDArray[np.float32]], int],
        reset_predictor: Callable[[int], None],
    ) -> None:
        self.predictor, self.reset_predictor = predictor, reset_predictor

    def reset(self, seed: int) -> None:
        self.reset_predictor(seed)

    def propose(self, observation: ObservationV1, history: NDArray[np.float32]) -> ControlRequest:
        return decode_action(self.predictor(history.copy()))

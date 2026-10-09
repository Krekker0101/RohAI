"""Gymnasium API for the shielded POMDP. No algorithm is selected or trained here."""

import math
from dataclasses import asdict
from typing import Any

import gymnasium as gym
import numpy as np
from gymnasium import spaces
from numpy.typing import NDArray
from traffic_core.models import SignalState
from traffic_core.settings import Timing

from traffic_simulator.environment import DigitalTwinEnvironment
from traffic_simulator.learning.contracts import ControlRequest, RewardSpecification, decode_action
from traffic_simulator.learning.observation import ObservationStack, encode
from traffic_simulator.learning.reward import RewardLedger, fairness_report, reward_terms
from traffic_simulator.learning.safety import SafetySupervisor
from traffic_simulator.twin_evaluation import code_fingerprint
from traffic_simulator.twin_models import ScenarioDefinition


class LearningControlEnvironment(gym.Env[NDArray[np.float32], int]):
    metadata = {"render_modes": []}

    def __init__(
        self,
        scenario: ScenarioDefinition,
        *,
        timing: Timing | None = None,
        reward: RewardSpecification | None = None,
        decision_interval_s: float = 1,
        stack_depth: int = 4,
        lock_scenario_seed: bool = False,
    ) -> None:
        self.scenario = scenario
        self.timing = timing or Timing()
        self.reward_spec = reward or RewardSpecification()
        ticks = decision_interval_s / scenario.step_s
        if not math.isfinite(ticks) or ticks < 1 or not math.isclose(ticks, round(ticks)):
            raise ValueError(
                "Decision interval must be an integer positive number of physics ticks"
            )
        self.ticks_per_action = round(ticks)
        self.decision_interval_s = decision_interval_s
        self.lock_scenario_seed = lock_scenario_seed
        self.stack_depth = stack_depth
        self.stack = ObservationStack(stack_depth)
        self.observation_space = spaces.Box(0, 1, shape=self.stack.shape, dtype=np.float32)
        self.action_space = spaces.Discrete(9)
        self.world: DigitalTwinEnvironment | None = None
        self.supervisor = SafetySupervisor(self.timing)
        self.ledger = RewardLedger(self.reward_spec)
        self.ended = True
        self.intervention_counts: dict[str, int] = {}
        self.invariants_passed = True

    def reset(
        self, *, seed: int | None = None, options: dict[str, Any] | None = None
    ) -> tuple[NDArray[np.float32], dict[str, Any]]:
        super().reset(seed=seed)
        if options:
            raise ValueError("No oracle/reset-state options are supported")
        if self.world is not None:
            self.world.close()
        world_seed = (
            self.scenario.seed
            if self.lock_scenario_seed
            else seed
            if seed is not None
            else int(self.np_random.integers(0, 2147483647))
        )
        self.world = DigitalTwinEnvironment(
            self.scenario.model_copy(update={"seed": world_seed}), self.timing
        )
        self.supervisor = SafetySupervisor(self.timing)
        self.stack = ObservationStack(self.stack_depth)
        self.ledger = RewardLedger(self.reward_spec)
        self.ended = False
        self.intervention_counts = {}
        self.invariants_passed = True
        self.action_space.seed(seed)
        self.latest = encode(self.world.observe(), self.supervisor)
        return self.stack.append(self.latest), {
            "environment_version": "rohai-control-env-v1",
            "observation_schema": self.latest.schema_version,
        }

    def step(self, action: int) -> tuple[NDArray[np.float32], float, bool, bool, dict[str, Any]]:
        if isinstance(action, (bool, np.bool_)) or not self.action_space.contains(action):
            raise ValueError("Action must be a Discrete(9) phase-duration request")
        return self.step_request(decode_action(int(action)))

    def step_request(
        self,
        request: ControlRequest,
        *,
        first_signal: SignalState | None = None,
        prior_switches: int | None = None,
    ) -> tuple[NDArray[np.float32], float, bool, bool, dict[str, Any]]:
        if self.ended or self.world is None:
            raise RuntimeError("reset() required before step or after episode end")
        if not isinstance(request, ControlRequest):
            raise TypeError("Lamp states and raw phases are not policy actions")
        if first_signal is not None and (
            prior_switches is None
            or first_signal != self.supervisor.machine.snapshot(self.world.now)
        ):
            raise ValueError("Pre-applied signal must be the current shield snapshot")
        reward = 0.0
        components = dict.fromkeys(self.ledger.raw, 0.0)
        for tick in range(self.ticks_per_action):
            before = self.world.ground_truth()  # privileged scorer only
            previous_switches = (
                prior_switches
                if tick == 0 and prior_switches is not None
                else self.supervisor.switches
            )
            action = (
                first_signal
                if tick == 0 and first_signal is not None
                else self.supervisor.apply(request, self.world.observe())
            )
            reason = self.supervisor.outcome.reason
            self.intervention_counts[reason] = self.intervention_counts.get(reason, 0) + 1
            observed = self.world.step(action)
            after = self.world.ground_truth()
            value, terms = self.ledger.record(
                reward_terms(
                    before,
                    after,
                    self.supervisor.switches - previous_switches,
                    self.world.dynamics.service_records,
                )
            )
            reward += value
            for key, contribution in terms.items():
                components[key] += contribution
            self.invariants_passed &= all(self.world.dynamics.invariants().values())
            if self.world.now >= self.scenario.duration_s - 1e-9:
                break
        self.latest = encode(observed, self.supervisor)
        truncated = self.world.now >= self.scenario.duration_s - 1e-9
        terminated = not self.invariants_passed
        self.ended = terminated or truncated
        info: dict[str, Any] = {
            "request": request.model_dump(mode="json"),
            "supervisor": asdict(self.supervisor.outcome),
            "reward_components": components,
        }
        if self.ended:
            info["episode_report"] = self.episode_report()
        return self.stack.append(self.latest), reward, terminated, truncated, info

    def episode_report(self) -> dict[str, Any]:
        if self.world is None:
            raise RuntimeError("No episode initialized")
        truth = self.world.ground_truth()
        return {
            "environment_version": "rohai-control-env-v1",
            "observation_schema": "rohai-observation-v1",
            "code_sha256": code_fingerprint(),
            "seed": self.world.scenario.seed,
            "scenario": self.world.scenario.model_dump(mode="json"),
            "timing": self.timing.model_dump(mode="json"),
            "decision_interval_s": self.decision_interval_s,
            "stack_depth": self.stack_depth,
            "arrival_tape_sha256": self.world.dynamics.tape_sha256,
            "reward": self.ledger.report(),
            "fairness": fairness_report(truth, self.world.dynamics.service_records),
            "service_opportunities": {
                p.value: {
                    "last_service_s": self.supervisor.last_service[p],
                    "has_served": self.supervisor.has_served[p],
                }
                for p in self.supervisor.last_service
            },
            "supervisor_interventions": self.intervention_counts,
            "invariants_passed": self.invariants_passed,
            "arrived": truth.arrived,
            "departed": truth.departed,
            "remaining": truth.arrived - truth.departed,
            "pedestrian_departed": truth.pedestrian_departed,
            "fault_latched": self.supervisor.machine.faulted,
            "training_started": False,
            "held_out_improvement_claim": False,
        }

    def close(self) -> None:
        if self.world is not None:
            self.world.close()
        self.ended = True

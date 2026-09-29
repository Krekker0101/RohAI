"""All non-learning and future learned policies use the same supervisor and scenario snapshots."""

from typing import Any

from traffic_core.settings import Timing

from traffic_simulator.learning.contracts import RewardSpecification
from traffic_simulator.learning.environment import LearningControlEnvironment
from traffic_simulator.learning.policy import TrafficPolicy
from traffic_simulator.twin_models import ScenarioDefinition


def evaluate_policy(
    scenario: ScenarioDefinition,
    policy: TrafficPolicy,
    reward: RewardSpecification | None = None,
    timing: Timing | None = None,
) -> dict[str, Any]:
    environment = LearningControlEnvironment(
        scenario, timing=timing, reward=reward, lock_scenario_seed=True
    )
    try:
        history, _ = environment.reset(seed=scenario.seed)
        policy.reset(scenario.seed)
        done = False
        while not done:
            request = policy.propose(environment.latest, history.copy())
            history, _, terminated, truncated, info = environment.step_request(request)
            done = terminated or truncated
        report: dict[str, Any] = info["episode_report"]
        return {"policy": policy.name, **report}
    finally:
        environment.close()

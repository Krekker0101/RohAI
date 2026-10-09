import runpy
import time
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError
from traffic_core.models import Direction, Phase
from traffic_simulator.learning.environment import LearningControlEnvironment
from traffic_simulator.learning.forecast import ArrivalForecaster, ArrivalSample
from traffic_simulator.learning.intelligence_contracts import (
    ControlAction,
    PolicyDecision,
    PolicyMetadata,
    PolicyState,
    SafetyConstraints,
    TrafficObservation,
)
from traffic_simulator.learning.intelligence_engine import IntelligenceEngine
from traffic_simulator.learning.intelligence_experiment import (
    ExperimentConfig,
    cases,
    evaluate_case,
)
from traffic_simulator.learning.intelligence_policy import MaxPressurePolicy, fixed_adapter
from traffic_simulator.scenarios import catalog


class SlowPolicy:
    metadata = PolicyMetadata(policy_id="slow-test", version="test")

    def decide(
        self, observation: TrafficObservation, state: PolicyState, constraints: SafetyConstraints
    ) -> PolicyDecision:
        time.sleep(0.1)
        return fixed_adapter(constraints).decide(observation, state, constraints)


class BadPolicy(SlowPolicy):
    def decide(
        self, observation: TrafficObservation, state: PolicyState, constraints: SafetyConstraints
    ) -> PolicyDecision:
        raise ValueError("unsupported model result")


@pytest.mark.parametrize(
    "policy,reason", [(SlowPolicy(), "inference_timeout"), (BadPolicy(), "policy_error:ValueError")]
)
def test_actor_fallback_and_independent_shield(policy: SlowPolicy, reason: str) -> None:
    env = LearningControlEnvironment(catalog(duration_s=8)["balanced"])
    history, _ = env.reset(seed=12)
    history, _, _, _, _ = env.step(0)
    constraints = SafetyConstraints(inference_timeout_s=0.01)
    engine = IntelligenceEngine(policy, env.supervisor, constraints)
    assert env.world is not None
    observation = TrafficObservation(**env.latest.model_dump())
    state = PolicyState(sequence=0, history=tuple(map(tuple, history.tolist())))
    try:
        started = time.monotonic()
        audit = engine.decide(observation, state, env.world.observe(), env.world.now)
        assert time.monotonic() - started < 0.08
        assert audit.fallback_reason == reason
        assert audit.signals == env.supervisor.machine.snapshot(env.world.now)
        assert len(engine.audit) == 1
        with pytest.raises(PermissionError):
            IntelligenceEngine(policy, env.supervisor, mode="PHYSICAL_MAKET")  # type: ignore[arg-type]
    finally:
        engine.close()
        env.close()


def test_observation_history_actions_and_forecast() -> None:
    with pytest.raises(ValidationError):
        ControlAction(requested_phase=Phase.NS, requested_duration_s=float("nan"))
    with pytest.raises(ValidationError):
        PolicyState(sequence=0, history=((0.0,),))
    forecaster = ArrivalForecaster("exponential", alpha=0.5)
    assert forecaster.predict(0) is None
    for i, v in enumerate((10, 20, 30)):
        forecaster.add(ArrivalSample(timestamp_s=i, rate_per_minute=v))
    forecast = forecaster.predict(2)
    assert forecast is not None and forecast.rate_per_minute == 22.5
    assert forecast.lower_rate <= forecast.rate_per_minute <= forecast.upper_rate
    assert forecaster.predict(100) is None
    with pytest.raises(ValueError):
        forecaster.add(ArrivalSample(timestamp_s=2, rate_per_minute=0))


def test_max_pressure_does_not_invent_downstream() -> None:
    env = LearningControlEnvironment(catalog(duration_s=8)["balanced"])
    history, _ = env.reset(seed=7)
    observation = TrafficObservation(**env.latest.model_dump())
    state = PolicyState(sequence=0, history=tuple(map(tuple, history.tolist())))
    result = MaxPressurePolicy().decide(observation, state, SafetyConstraints())
    assert result.facts["pressure_mode"] == "upstream_only"
    measured = observation.model_copy(
        update={
            "downstream_queue_counts": dict.fromkeys(Direction, 10),
            "downstream_captured_at_s": 0,
        }
    )
    result = MaxPressurePolicy().decide(measured, state, SafetyConstraints())
    assert result.facts["pressure_mode"] == "measured_exit_counts"
    pressure = result.facts[Phase.NS.value]
    assert isinstance(pressure, float) and pressure < 0
    env.close()


def test_splits_and_resource_config() -> None:
    train, val, test = (cases(split, 120) for split in ("training", "validation", "test"))
    assert not ({s["seed"] for s in train} & {s["seed"] for s in val + test})
    assert {s["scenario_id"].split("-")[1] for s in train}
    with pytest.raises(ValidationError):
        ExperimentConfig(training_steps=2411)


def test_benchmark_determinism_and_no_double_shield(tmp_path: Path) -> None:
    case = catalog(duration_s=8)["balanced"].model_dump(mode="json")
    plan = {"sha256": "unit-test-only"}
    a = evaluate_case(plan, case, "fixed", None, tmp_path / "a.json")
    b = evaluate_case(plan, case, "fixed", None, tmp_path / "b.json")
    assert a["metrics"] == b["metrics"]
    assert a["arrival_tape_sha256"] == b["arrival_tape_sha256"]
    assert a["invariants_passed"] and not a["fault_latched"]
    assert sum(a["supervisor_interventions"].values()) == 32
    assert np.isfinite(a["metrics"]["average_delay_s"])


def test_forecast_does_not_grade_late_recovered_observation(tmp_path: Path) -> None:
    import json

    env = LearningControlEnvironment(catalog(duration_s=8)["balanced"])
    env.reset(seed=7)
    env.step(0)
    observation = TrafficObservation(**env.latest.model_dump())
    # Exactly 5s target is missing. An 8s recovery must NOT stand in for it.
    path = tmp_path / "decisions.json"
    rows = [
        {"observation": observation.model_copy(update={"timestamp_s": t}).model_dump(mode="json")}
        for t in (1, 2, 3, 8)
    ]
    path.write_text(json.dumps(rows), encoding="utf-8")
    function = runpy.run_path("scripts/evaluation/intelligence_reports.py")["forecast_quality"]
    result = function([path], horizon_s=2)
    assert result["missing_or_right_censored_targets"] > 0
    assert all(row["samples"] == 0 for row in result["quality"].values())
    env.close()

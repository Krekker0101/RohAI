import pytest
from pydantic import ValidationError
from traffic_core.models import Phase, Policy, SignalStage
from traffic_core.settings import Timing
from traffic_simulator.comparison import ComparisonRequest, compare
from traffic_simulator.engine import SimulationEngine
from traffic_simulator.simulator import Scenario


def test_deterministic_trace_and_conservation() -> None:
    left = SimulationEngine(Scenario(), Timing(), Policy.ADAPTIVE)
    right = SimulationEngine(Scenario(), Timing(), Policy.ADAPTIVE)
    phases = set()
    for _ in range(1200):
        a, b = left.step(0.5), right.step(0.5)
        assert a == b
        assert a.kpi.arrived == a.kpi.departed + a.kpi.remaining
        assert a.kpi.total_wait_seconds >= 0
        if a.signals.stage == SignalStage.GREEN:
            phases.add(a.signals.phase)
    assert phases == set(Phase)


def test_paired_comparison_reproducible_and_same_arrivals() -> None:
    request = ComparisonRequest(duration_seconds=180)
    first = compare(request, Timing())
    assert first == compare(request, Timing())
    assert first.fixed.arrived == first.adaptive.arrived
    assert first.wait_reduction_percent is not None


def test_empty_scenario_has_finite_zero_kpis() -> None:
    scenario = Scenario(
        north_per_minute=0,
        south_per_minute=0,
        east_per_minute=0,
        west_per_minute=0,
        pedestrians_per_minute=0,
    )
    result = compare(ComparisonRequest(scenario=scenario, duration_seconds=30), Timing())
    assert result.fixed.arrived == result.adaptive.arrived == 0
    assert result.wait_reduction_percent is None
    assert result.fixed.total_wait_seconds == 0


def test_emergency_expires_and_normal_service_resumes() -> None:
    engine = SimulationEngine(Scenario(), Timing(), Policy.ADAPTIVE)
    engine.set_emergency(Phase.EW, 30)
    seen = set()
    for _ in range(360):
        result = engine.step(0.5)
        if result.signals.stage == SignalStage.GREEN:
            seen.add(result.signals.phase)
    assert engine.emergency_phase is None
    assert seen == set(Phase)
    with pytest.raises(ValueError):
        engine.set_emergency(Phase.PEDESTRIAN, 10)


def test_invalid_configuration_and_step_rejected() -> None:
    with pytest.raises(ValidationError):
        Timing(min_green_seconds=30)
    with pytest.raises(ValidationError):
        Scenario(north_per_minute=-1)
    engine = SimulationEngine(Scenario(), Timing(), Policy.FIXED)
    with pytest.raises(ValueError):
        engine.step(0)

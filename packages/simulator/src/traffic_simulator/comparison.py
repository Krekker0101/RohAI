from pydantic import Field
from traffic_core.models import KPI, Model, Policy
from traffic_core.settings import Timing

from traffic_simulator.engine import SimulationEngine
from traffic_simulator.simulator import Scenario


class ComparisonRequest(Model):
    scenario: Scenario = Field(default_factory=Scenario)
    duration_seconds: int = Field(default=600, ge=30, le=3600)


class ComparisonResult(Model):
    seed: int
    duration_seconds: int
    step_seconds: float
    fixed: KPI
    adaptive: KPI
    wait_reduction_percent: float | None


def compare(request: ComparisonRequest, timing: Timing) -> ComparisonResult:
    results: dict[Policy, KPI] = {}
    step = 0.5
    for policy in Policy:
        engine = SimulationEngine(request.scenario, timing, policy)
        for _ in range(int(request.duration_seconds / step)):
            engine.step(step)
        results[policy] = engine.simulator.kpi()
    fixed, adaptive = results[Policy.FIXED], results[Policy.ADAPTIVE]
    return ComparisonResult(
        seed=request.scenario.seed,
        duration_seconds=request.duration_seconds,
        step_seconds=step,
        fixed=fixed,
        adaptive=adaptive,
        wait_reduction_percent=(
            100
            * (fixed.total_wait_seconds - adaptive.total_wait_seconds)
            / fixed.total_wait_seconds
            if fixed.total_wait_seconds
            else None
        ),
    )

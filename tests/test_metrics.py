import pytest
from traffic_core.models import Direction, ObjectKind, Phase, SignalStage, SignalState
from traffic_simulator.simulator import Arrival, Scenario, Simulator


def empty_scenario() -> Scenario:
    return Scenario(
        north_per_minute=0,
        south_per_minute=0,
        east_per_minute=0,
        west_per_minute=0,
        pedestrians_per_minute=0,
    )


def test_exact_delay_includes_unserved_queue() -> None:
    simulator = Simulator(empty_scenario())
    simulator.queues[Direction.NORTH].append(Arrival(1, 0, Direction.NORTH, ObjectKind.BUS))
    simulator.queues[Direction.EAST].append(Arrival(2, 0, Direction.EAST, ObjectKind.CAR))
    simulator.arrived = 2
    red = SignalState(phase=Phase.NS, stage=SignalStage.ALL_RED, elapsed_seconds=0)
    simulator.advance(10, red)
    assert simulator.kpi().total_wait_seconds == 20
    north = simulator.snapshot().approaches[0]
    assert north.counts.buses == 1
    assert north.traffic_score == 3.0
    green = SignalState(phase=Phase.NS, stage=SignalStage.GREEN, elapsed_seconds=0)
    simulator.advance(5, green)
    kpi = simulator.kpi()
    assert kpi.departed == 1
    assert kpi.remaining == 1
    assert kpi.mean_completed_wait_seconds == 12
    assert kpi.total_wait_seconds == 27  # completed 12 + still waiting 15
    assert kpi.mean_wait_per_arrival_seconds == 13.5
    assert kpi.max_queue == 2


def test_arrival_stream_independent_of_step_partition() -> None:
    left, right = Simulator(Scenario()), Simulator(Scenario())
    red = SignalState(phase=Phase.NS, stage=SignalStage.ALL_RED, elapsed_seconds=0)
    for _ in range(120):
        left.advance(0.5, red)
    for _ in range(60):
        right.advance(1, red)
    assert left.queues == right.queues
    assert left.snapshot() == right.snapshot()
    assert left.kpi() == right.kpi()


def test_arrival_rate_uses_observed_window() -> None:
    simulator = Simulator(empty_scenario())
    simulator.now = 30
    simulator.recent[Direction.NORTH].extend([10, 20, 25])
    assert simulator.snapshot().approaches[0].arrival_rate_per_minute == pytest.approx(6)

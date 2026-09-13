import random

import pytest
from traffic_core.models import Decision, Lamp, Phase, SignalStage
from traffic_core.safety import SafetyController
from traffic_core.settings import Timing


def decision(phase: Phase, duration: float = 8) -> Decision:
    return Decision(phase=phase, green_seconds=duration, reason="test")


def test_startup_and_conflicting_emergency_preserve_clearance() -> None:
    safety = SafetyController(Timing())
    assert safety.advance(1, decision(Phase.NS)).stage == SignalStage.ALL_RED
    assert safety.advance(2, decision(Phase.NS)).stage == SignalStage.GREEN
    assert safety.advance(9, decision(Phase.EW)).phase == Phase.NS
    assert safety.advance(10, decision(Phase.EW)).stage == SignalStage.YELLOW
    assert safety.advance(12.9, decision(Phase.EW)).stage == SignalStage.YELLOW
    assert safety.advance(13, decision(Phase.EW)).stage == SignalStage.ALL_RED
    assert safety.advance(14.9, decision(Phase.EW)).stage == SignalStage.ALL_RED
    assert safety.advance(15, decision(Phase.EW)).phase == Phase.EW
    assert safety.snapshot(15).stage == SignalStage.GREEN


def test_pedestrian_minimum_and_clearance_cannot_be_preempted() -> None:
    safety = SafetyController(Timing())
    safety.advance(2, decision(Phase.PEDESTRIAN))
    assert safety.advance(10, decision(Phase.NS)).stage == SignalStage.GREEN
    signal = safety.advance(12, decision(Phase.NS))
    assert signal.stage == SignalStage.YELLOW
    assert set(signal.lamps().values()) == {Lamp.RED}
    assert safety.advance(16.9, decision(Phase.NS)).stage == SignalStage.YELLOW
    assert safety.advance(17, decision(Phase.NS)).stage == SignalStage.ALL_RED
    assert safety.advance(19, decision(Phase.NS)).stage == SignalStage.GREEN


def test_large_clock_jump_never_skips_clearance_and_fault_latches() -> None:
    safety = SafetyController(Timing())
    safety.advance(2, decision(Phase.NS))
    assert safety.advance(1000, decision(Phase.EW)).stage == SignalStage.YELLOW
    assert safety.advance(1001, decision(Phase.EW)).stage == SignalStage.YELLOW
    safety.fail_safe(1001)
    assert safety.advance(2000, decision(Phase.EW)).stage == SignalStage.ALL_RED
    with pytest.raises(ValueError):
        safety.advance(1999, decision(Phase.NS))
    with pytest.raises(ValueError):
        safety.advance(float("nan"), decision(Phase.NS))


def test_random_requests_never_conflict_or_skip_stages() -> None:
    safety = SafetyController(Timing())
    rng = random.Random(901)
    previous = safety.snapshot(0)
    allowed = {
        (SignalStage.ALL_RED, SignalStage.GREEN),
        (SignalStage.GREEN, SignalStage.YELLOW),
        (SignalStage.YELLOW, SignalStage.ALL_RED),
    }
    for tick in range(1, 10001):
        current = safety.advance(tick * 0.5, decision(rng.choice(list(Phase)), 1000))
        lamps = current.lamps()
        assert not (lamps["north"] == Lamp.GREEN and lamps["east"] == Lamp.GREEN)
        if lamps["pedestrian"] == Lamp.GREEN:
            assert all(lamps[d] == Lamp.RED for d in ("north", "south", "east", "west"))
        if current.stage != previous.stage:
            assert (previous.stage, current.stage) in allowed
        if current.stage == SignalStage.GREEN:
            assert current.elapsed_seconds < 45
        previous = current

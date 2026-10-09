import pytest
from pydantic import ValidationError
from smart_traffic_backend.safety_architecture import (
    DecisionAudit,
    DecisionJournal,
    PolicyCandidate,
    RecoveryChecks,
    RecoveryState,
    RecoveryStateMachine,
)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_policy_revalidates_untrusted_constructed_instances(value: float) -> None:
    candidate = PolicyCandidate.model_construct(phase="north_south", green_seconds=value)
    with pytest.raises(ValidationError):
        PolicyCandidate.model_validate(candidate)


def test_recovery_requires_all_checks_and_operator_confirmation() -> None:
    recovery = RecoveryStateMachine()
    recovery.fault("camera_disconnected")
    recovery.mark_safe()
    recovery.begin_recovery()
    assert not recovery.qualify(RecoveryChecks(source=True))
    assert recovery.state == RecoveryState.SAFE
    with pytest.raises(RuntimeError):
        recovery.confirm_operator()

    recovery.begin_recovery()
    assert recovery.qualify(
        RecoveryChecks(
            source=True,
            calibration=True,
            freshness=True,
            analytics=True,
            controller=True,
            hardware=True,
        )
    )
    assert str(recovery.state) == RecoveryState.READY_FOR_OPERATOR_CONFIRMATION.value
    recovery.confirm_operator()
    assert str(recovery.state) == RecoveryState.HEALTHY.value


def test_degraded_is_operator_visible_and_clears_without_latching_fault() -> None:
    recovery = RecoveryStateMachine()
    recovery.degrade("observation_degraded")
    assert recovery.state == RecoveryState.DEGRADED
    assert recovery.reason == "observation_degraded"
    # A second, different degradation replaces the reason but stays in DEGRADED.
    recovery.degrade("analytics_degraded")
    assert recovery.reason == "analytics_degraded"
    recovery.restore()
    assert str(recovery.state) == RecoveryState.HEALTHY.value
    assert recovery.reason is None


def test_degradation_cannot_mask_a_latched_fault_or_safe_state() -> None:
    recovery = RecoveryStateMachine()
    recovery.fault("vision_stale")
    assert recovery.state == RecoveryState.FAULT
    recovery.mark_safe()
    recovery.degrade("observation_degraded")
    recovery.restore()
    assert str(recovery.state) == RecoveryState.SAFE.value
    assert recovery.reason == "vision_stale"
    # Leaving SAFE still requires the explicit recovery path and operator confirmation.
    with pytest.raises(RuntimeError):
        recovery.confirm_operator()


def test_decision_journal_retains_baseline_proposal_safety_and_future_outcome() -> None:
    journal = DecisionJournal(capacity=1)
    journal.append(
        DecisionAudit(
            observation_sequence=7,
            actual_baseline_action={"phase": "north_south"},
            ai_proposed_action={"phase": "east_west"},
            safety_response="baseline_retained_shadow_mode",
        )
    )
    assert journal.record_outcome(7, {"observed_queue": 3.0})
    record = journal.snapshot()[0]
    assert record.actual_baseline_action["phase"] == "north_south"
    assert record.ai_proposed_action == {"phase": "east_west"}
    assert record.future_observed_outcome == {"observed_queue": 3.0}
    assert not journal.record_outcome(6, {})


def test_decision_journal_snapshot_is_ordered_by_observation_sequence() -> None:
    journal = DecisionJournal()
    for sequence in (2, 1):
        journal.append(
            DecisionAudit(
                observation_sequence=sequence,
                actual_baseline_action={"phase": "north_south"},
                safety_response="baseline_retained_shadow_mode",
            )
        )
    assert [record.observation_sequence for record in journal.snapshot()] == [1, 2]


def test_operator_cannot_enable_policy_without_evaluator_or_activate() -> None:
    from smart_traffic_backend.config import Settings
    from smart_traffic_backend.hardware import MockHardwareController
    from smart_traffic_backend.runtime import Runtime
    from smart_traffic_backend.safety_architecture import PolicyMode

    runtime = Runtime(Settings(), MockHardwareController())
    with pytest.raises(ValueError):
        runtime.configure_policy(PolicyMode.SHADOW, None)
    with pytest.raises(PermissionError):
        runtime.configure_policy(PolicyMode.ACTIVE, None)


def test_requested_approved_and_sent_signal_states_remain_distinct() -> None:
    import asyncio

    from smart_traffic_backend.config import Settings
    from smart_traffic_backend.hardware import MockHardwareController
    from smart_traffic_backend.runtime import Runtime
    from traffic_core.models import Phase, SignalStage, SignalState

    async def exercise() -> None:
        hardware = MockHardwareController()
        runtime = Runtime(Settings(), hardware)
        requested = SignalState(phase=Phase.EW, stage=SignalStage.GREEN, elapsed_seconds=0)
        approved = SignalState(phase=Phase.NS, stage=SignalStage.ALL_RED, elapsed_seconds=2)
        await runtime._apply(approved, requested)
        stages = runtime.safety_status().hardware_state
        assert stages.requested == requested
        assert stages.safety_approved == approved
        assert stages.command_sent == approved

    asyncio.run(exercise())

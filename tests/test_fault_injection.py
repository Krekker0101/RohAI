"""Fault injection against the authenticated device protocol and the host boundary.

Software contracts only. No cabinet, MCU firmware, lamp sensor or live network is
involved, and no result here establishes physical production readiness.
"""

import asyncio
import time
from collections.abc import Callable
from functools import partial
from typing import Literal

import pytest
from pydantic import ValidationError
from smart_traffic_backend.device_emulator import DeviceKernel
from smart_traffic_backend.protocol_hardware import ProtocolHardwareController
from smart_traffic_backend.safety_architecture import capture_hardware_state
from traffic_core.hardware_protocol import (
    DeviceCapabilities,
    DeviceRequirements,
    HardwareAck,
    HardwareCommand,
    HardwareFeedback,
    HardwareTransport,
    authenticate,
)
from traffic_core.models import Phase, SignalStage, SignalState
from traffic_core.settings import Timing

KEY = b"fault-injection-key-32-bytes-0000"
INSTANCE = "controller-fault-injection"
DEVICE_ID = "junction-fault-injection"
SAFE_MODE_ID = "emulator-all-red"
MAX_LEASE_S = 20.0


def requirements(**overrides: object) -> DeviceRequirements:
    values: dict[str, object] = {
        "device_id": DEVICE_ID,
        "safe_mode_id": SAFE_MODE_ID,
        "requirements_reference": "fault-injection-v1",
        "max_lease_s": MAX_LEASE_S,
        "ack_timeout_s": 2.0,
        "feedback_timeout_s": 5.0,
        "max_clock_skew_s": 0.2,
    }
    values.update(overrides)
    return DeviceRequirements.model_validate(values)


def green() -> SignalState:
    return SignalState(phase=Phase.NS, stage=SignalStage.GREEN, elapsed_seconds=0)


def signal_command(
    device: DeviceKernel, sequence: int, issued_at: float, **overrides: object
) -> HardwareCommand:
    values: dict[str, object] = {
        "device_id": DEVICE_ID,
        "device_boot_id": device.boot_id,
        "controller_instance": INSTANCE,
        "sequence": sequence,
        "kind": "SIGNAL",
        "requested_state": green(),
        "issued_at": issued_at,
        "expires_at": issued_at + MAX_LEASE_S,
    }
    values.update(overrides)
    return authenticate(HardwareCommand.model_validate(values), KEY)


def device_accepting_green() -> tuple[DeviceKernel, float, float]:
    """A device whose first all-red clearance has elapsed, so a green is legal."""
    monotonic_now, utc_now = 100.0, time.time()
    device = DeviceKernel(requirements(), INSTANCE, KEY, Timing(), monotonic_now)
    assert device.receive(signal_command(device, 1, utc_now), monotonic_now + 3, utc_now).accepted
    return device, monotonic_now + 3, utc_now


class ScriptedTransport(HardwareTransport):
    """Deterministic device double that can corrupt one specific host-bound message."""

    def __init__(self) -> None:
        self.monotonic = 100.0
        self.device = DeviceKernel(requirements(), INSTANCE, KEY, Timing(), self.monotonic)
        self.capabilities_hook: Callable[[DeviceCapabilities], DeviceCapabilities] | None = None
        self.ack_hook: Callable[[HardwareAck], HardwareAck] | None = None
        self.feedback_hook: Callable[[HardwareFeedback], HardwareFeedback] | None = None

    async def capabilities(self) -> DeviceCapabilities:
        capability = self.device.capabilities()
        return self.capabilities_hook(capability) if self.capabilities_hook else capability

    async def send(self, command: HardwareCommand) -> HardwareAck:
        ack = self.device.receive(command, self.monotonic + 3, time.time())
        return self.ack_hook(ack) if self.ack_hook else ack

    async def feedback(self) -> HardwareFeedback:
        feedback = self.device.feedback(self.monotonic + 3, time.time())
        return self.feedback_hook(feedback) if self.feedback_hook else feedback

    async def close(self) -> None:
        pass


def test_device_rejects_tampered_signature_and_payload() -> None:
    device = DeviceKernel(requirements(), INSTANCE, KEY, Timing(), 100)
    utc = time.time()
    good = signal_command(device, 1, utc)

    wrong_signature = good.model_copy(update={"signature": "0" * 64})
    ack = device.receive(wrong_signature, 103, utc)
    assert not ack.accepted
    assert ack.reason == "Device message authentication failed"

    # Same stored signature, different payload: the checksum/signature must not match.
    rewired = good.model_copy(
        update={
            "requested_state": SignalState(
                phase=Phase.EW, stage=SignalStage.GREEN, elapsed_seconds=0
            )
        }
    )
    assert not device.receive(rewired, 104, utc).accepted
    assert not device.watchdog_latched
    assert device.signal.stage == SignalStage.ALL_RED


def test_device_rejects_reused_command_id_with_changed_payload() -> None:
    device, now, utc = device_accepting_green()
    accepted = device.last_command
    assert accepted is not None
    changed = authenticate(
        accepted.model_copy(
            update={
                "requested_state": SignalState(
                    phase=Phase.EW, stage=SignalStage.GREEN, elapsed_seconds=0
                )
            }
        ),
        KEY,
    )
    ack = device.receive(changed, now + 1, utc)
    assert not ack.accepted
    assert ack.reason == "duplicate_id_changed_payload"


def test_device_latch_blocks_new_signal_until_explicit_rearm() -> None:
    device, now, utc = device_accepting_green()
    device.tick(now + MAX_LEASE_S)
    assert device.watchdog_latched
    assert device.signal.stage == SignalStage.YELLOW

    blocked = device.receive(signal_command(device, 2, utc), now + MAX_LEASE_S + 1, utc)
    assert not blocked.accepted
    assert blocked.reason == "operator_rearm_required"

    safe = authenticate(
        HardwareCommand(
            device_id=DEVICE_ID,
            device_boot_id=device.boot_id,
            controller_instance=INSTANCE,
            sequence=2,
            kind="ENTER_SAFE",
            safe_mode_id=SAFE_MODE_ID,
            issued_at=utc,
            expires_at=utc + MAX_LEASE_S,
        ),
        KEY,
    )
    assert device.receive(safe, now + MAX_LEASE_S + 1, utc).accepted


CAPABILITY_FORGERIES: dict[str, dict[str, object]] = {
    "unsigned": {"signature": ""},
    "device": {"device_id": "other-junction"},
    "controller": {"controller_instance": "other-controller"},
    "safe_mode": {"safe_mode_id": "other-safe-mode"},
    "watchdog": {"independent_watchdog": False},
}


def forged_capabilities(kind: str, capability: DeviceCapabilities) -> DeviceCapabilities:
    """Forge one field: an unsigned message, or a coherently re-signed wrong identity."""
    mutated = capability.model_copy(update=CAPABILITY_FORGERIES[kind])
    return mutated if kind == "unsigned" else authenticate(mutated, KEY)


@pytest.mark.parametrize("kind", sorted(CAPABILITY_FORGERIES))
def test_host_refuses_unauthenticated_or_mismatched_capabilities(kind: str) -> None:
    async def exercise() -> None:
        transport = ScriptedTransport()
        transport.capabilities_hook = partial(forged_capabilities, kind)
        hardware = ProtocolHardwareController(transport, requirements(), INSTANCE, KEY)
        with pytest.raises(ValueError):
            await hardware.initialize()
        assert hardware.capability is None

    asyncio.run(exercise())


def test_host_refuses_physical_device_and_requires_provisioned_key() -> None:
    async def exercise() -> None:
        transport = ScriptedTransport()
        transport.capabilities_hook = lambda capability: authenticate(
            capability.model_copy(update={"physical_device": True}), KEY
        )
        hardware = ProtocolHardwareController(transport, requirements(), INSTANCE, KEY)
        with pytest.raises(PermissionError):
            await hardware.initialize()

    asyncio.run(exercise())
    with pytest.raises(ValueError, match="authentication key"):
        ProtocolHardwareController(ScriptedTransport(), requirements(), INSTANCE, b"too-short")


@pytest.mark.parametrize(
    "overrides",
    [
        {"ack_timeout_s": 20.0},
        {"feedback_timeout_s": 20.0},
        {"requirements_reference": ""},
        {"safe_mode_id": ""},
    ],
)
def test_requirements_reject_unsafe_deadlines_and_missing_authority_reference(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        requirements(**overrides)


def test_host_rejects_ack_that_does_not_identify_the_outstanding_command() -> None:
    async def exercise() -> None:
        transport = ScriptedTransport()
        transport.ack_hook = lambda ack: authenticate(
            ack.model_copy(update={"command_id": "not-the-outstanding-command"}), KEY
        )
        hardware = ProtocolHardwareController(transport, requirements(), INSTANCE, KEY)
        with pytest.raises(ValueError, match="ACK does not identify the outstanding command"):
            await hardware.apply(green())
        assert str(hardware.delivery.status) == "FAULT"
        assert hardware.delivery.acknowledged_state is None
        assert hardware.delivery.physically_confirmed is None

    asyncio.run(exercise())


def stale_feedback(feedback: HardwareFeedback) -> HardwareFeedback:
    return authenticate(feedback.model_copy(update={"measured_at": time.time() - 60}), KEY)


def future_feedback(feedback: HardwareFeedback) -> HardwareFeedback:
    return authenticate(feedback.model_copy(update={"measured_at": time.time() + 60}), KEY)


def tampered_feedback(feedback: HardwareFeedback) -> HardwareFeedback:
    return feedback.model_copy(update={"signature": "0" * 64})


def replaying_hook() -> Callable[[HardwareFeedback], HardwareFeedback]:
    seen: list[HardwareFeedback] = []

    def hook(feedback: HardwareFeedback) -> HardwareFeedback:
        if seen:
            return seen[0]
        seen.append(feedback)
        return feedback

    return hook


@pytest.mark.parametrize("mode", ["stale", "future", "tampered"])
def test_host_rejects_stale_future_and_tampered_feedback(
    mode: Literal["stale", "future", "tampered"],
) -> None:
    async def exercise() -> None:
        transport = ScriptedTransport()
        hooks = {
            "stale": stale_feedback,
            "future": future_feedback,
            "tampered": tampered_feedback,
        }
        transport.feedback_hook = hooks[mode]
        hardware = ProtocolHardwareController(transport, requirements(), INSTANCE, KEY)
        with pytest.raises(ValueError):
            await hardware.apply(green())
        assert str(hardware.delivery.status) == "FAULT"
        assert hardware.delivery.physically_confirmed is None
        assert hardware.delivery.emulated_feedback is None

    asyncio.run(exercise())


def test_host_rejects_replayed_feedback_sample() -> None:
    async def exercise() -> None:
        transport = ScriptedTransport()
        transport.feedback_hook = replaying_hook()
        hardware = ProtocolHardwareController(transport, requirements(), INSTANCE, KEY)
        await hardware.apply(green())
        assert hardware.delivery.status == "ACKNOWLEDGED"
        with pytest.raises(ValueError, match="Replayed feedback"):
            await hardware.refresh_feedback()
        assert str(hardware.delivery.status) == "FAULT"
        assert hardware.delivery.emulated_feedback is None
        assert hardware.delivery.physically_confirmed is None

    asyncio.run(exercise())


def test_fresh_commands_cannot_extend_green_past_device_limit() -> None:
    device, now, utc = device_accepting_green()
    limit = device.timing.max_green_seconds
    for sequence, elapsed in enumerate(range(5, int(limit), 5), start=2):
        assert device.receive(
            signal_command(device, sequence, utc + elapsed), now + elapsed, utc + elapsed
        ).accepted
    assert device.lease_deadline > now + limit
    device.tick(now + limit)
    assert device.watchdog_latched
    assert device.signal.stage == SignalStage.YELLOW


def test_safe_request_rejects_uncorrelated_feedback() -> None:
    async def exercise() -> None:
        transport = ScriptedTransport()
        transport.feedback_hook = lambda feedback: authenticate(
            feedback.model_copy(update={"command_id": "previous-command"}), KEY
        )
        hardware = ProtocolHardwareController(transport, requirements(), INSTANCE, KEY)
        with pytest.raises(ValueError, match="Safe-program feedback"):
            await hardware.enter_safe()
        assert str(hardware.delivery.status) == "FAULT"
        assert hardware.delivery.emulated_feedback is None

    asyncio.run(exercise())


def test_protocol_adapter_keeps_five_delivery_stages_distinct() -> None:
    async def exercise() -> None:
        transport = ScriptedTransport()
        hardware = ProtocolHardwareController(transport, requirements(), INSTANCE, KEY)
        await hardware.apply(green())

        requested = SignalState(phase=Phase.EW, stage=SignalStage.GREEN, elapsed_seconds=0)
        stages = capture_hardware_state(hardware, requested, green())
        assert stages.requested == requested
        assert stages.safety_approved == green()
        assert stages.command_sent == green()
        assert stages.hardware_acknowledged == green()
        # Emulator readback is never physical lamp evidence.
        assert stages.physically_confirmed is None
        assert hardware.delivery.emulated_feedback is not None
        command = hardware.delivery.command_sent
        assert command is not None
        assert stages.command_id == command.command_id
        assert stages.sequence == command.sequence
        assert stages.sent_at == command.issued_at
        assert stages.error is None
        await hardware.close()

    asyncio.run(exercise())

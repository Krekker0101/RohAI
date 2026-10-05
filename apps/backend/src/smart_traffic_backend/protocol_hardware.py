"""Host boundary: leases, authenticated ACK and independent readback are distinct stages."""

import asyncio
import time
from typing import Literal

from traffic_core.hardware_protocol import (
    DeviceCapabilities,
    DeviceRequirements,
    HardwareAck,
    HardwareCommand,
    HardwareFeedback,
    HardwareTransport,
    authenticate,
    verify,
)
from traffic_core.models import Model, SignalState


class DeliveryState(Model):
    command_sent: HardwareCommand | None = None
    hardware_acknowledged: HardwareAck | None = None
    acknowledged_state: SignalState | None = None
    physically_confirmed: HardwareFeedback | None = None
    emulated_feedback: HardwareFeedback | None = None
    status: Literal["UNCONFIRMED", "AWAITING_ACK", "ACKNOWLEDGED", "CONFIRMED", "FAULT"] = (
        "UNCONFIRMED"
    )
    error: str | None = None
    last_ack_monotonic: float | None = None
    last_feedback_monotonic: float | None = None


class ProtocolHardwareController:
    """Production protocol boundary; no vendor physical transport is supplied by RohAI."""

    def __init__(
        self,
        transport: HardwareTransport,
        requirements: DeviceRequirements,
        controller_instance: str,
        key: bytes,
    ) -> None:
        if len(key) < 32:
            raise ValueError("Provision a device authentication key")
        self.transport, self.requirements, self.instance, self.key = (
            transport,
            requirements,
            controller_instance,
            key,
        )
        self.capability: DeviceCapabilities | None = None
        self.sequence = 0
        self.feedback_sequence = 0
        self.delivery = DeliveryState()
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        async with asyncio.timeout(self.requirements.ack_timeout_s):
            capability = await self.transport.capabilities()
        verify(capability, self.key)
        if (
            capability.device_id != self.requirements.device_id
            or capability.controller_instance != self.instance
            or capability.safe_mode_id != self.requirements.safe_mode_id
            or not capability.independent_watchdog
        ):
            raise ValueError("Device identity, safe mode or independent watchdog mismatch")
        if capability.physical_device:
            # This release deliberately cannot arm a real cabinet.
            raise PermissionError("Physical commissioning/transport activation is not implemented")
        self.capability = capability

    async def apply(self, signals: SignalState) -> None:
        await self._send(signals)

    async def enter_safe(self) -> None:
        await self._send(None)

    async def _send(self, signals: SignalState | None) -> None:
        async with self._lock:
            if self.capability is None:
                try:
                    await self.initialize()
                except BaseException as error:
                    self.delivery = self.delivery.model_copy(
                        update={
                            "status": "FAULT",
                            "error": type(error).__name__,
                        }
                    )
                    raise
            assert self.capability is not None
            self.sequence += 1
            now = time.time()
            command = authenticate(
                HardwareCommand(
                    device_id=self.requirements.device_id,
                    device_boot_id=self.capability.device_boot_id,
                    controller_instance=self.instance,
                    sequence=self.sequence,
                    kind="SIGNAL" if signals else "ENTER_SAFE",
                    requested_state=signals,
                    safe_mode_id=None if signals else self.requirements.safe_mode_id,
                    issued_at=now,
                    expires_at=now + self.requirements.max_lease_s,
                ),
                self.key,
            )
            self.delivery = self.delivery.model_copy(
                update={
                    "command_sent": command,
                    "hardware_acknowledged": None,
                    "acknowledged_state": None,
                    "physically_confirmed": None,
                    "emulated_feedback": None,
                    "status": "AWAITING_ACK",
                    "error": None,
                }
            )
            try:
                async with asyncio.timeout(self.requirements.ack_timeout_s):
                    ack = await self.transport.send(command)
                verify(ack, self.key)
                if (
                    ack.device_id,
                    ack.device_boot_id,
                    ack.controller_instance,
                    ack.command_id,
                    ack.sequence,
                    ack.command_checksum,
                ) != (
                    command.device_id,
                    command.device_boot_id,
                    command.controller_instance,
                    command.command_id,
                    command.sequence,
                    command.checksum,
                ):
                    raise ValueError("ACK does not identify the outstanding command")
                if (
                    not command.issued_at - self.requirements.max_clock_skew_s
                    <= ack.acknowledged_at
                    <= time.time() + self.requirements.max_clock_skew_s
                ):
                    raise ValueError("ACK timestamp is invalid")
                if not ack.accepted:
                    raise RuntimeError(f"Device rejected command: {ack.reason}")
                self.delivery = self.delivery.model_copy(
                    update={
                        "hardware_acknowledged": ack,
                        "acknowledged_state": signals,
                        "status": "ACKNOWLEDGED",
                        "last_ack_monotonic": time.monotonic(),
                    }
                )
                await self.refresh_feedback()
            except BaseException as error:
                self.delivery = self.delivery.model_copy(
                    update={"status": "FAULT", "error": type(error).__name__}
                )
                raise

    async def refresh_feedback(self) -> HardwareFeedback:
        try:
            return await self._refresh_feedback()
        except BaseException as error:
            self.delivery = self.delivery.model_copy(
                update={
                    "physically_confirmed": None,
                    "emulated_feedback": None,
                    "status": "FAULT",
                    "error": type(error).__name__,
                }
            )
            raise

    async def _refresh_feedback(self) -> HardwareFeedback:
        assert self.capability is not None
        async with asyncio.timeout(self.requirements.feedback_timeout_s):
            feedback = await self.transport.feedback()
        verify(feedback, self.key)
        if (feedback.device_id, feedback.device_boot_id, feedback.controller_instance) != (
            self.capability.device_id,
            self.capability.device_boot_id,
            self.instance,
        ):
            raise ValueError("Feedback identity mismatch")
        age = time.time() - feedback.measured_at
        if not -self.requirements.max_clock_skew_s <= age <= self.requirements.feedback_timeout_s:
            raise ValueError("Stale/future physical feedback")
        if feedback.sample_sequence <= self.feedback_sequence:
            raise ValueError("Replayed feedback")
        self.feedback_sequence = feedback.sample_sequence
        command = self.delivery.command_sent
        if command is not None and command.kind == "ENTER_SAFE":
            if (
                feedback.command_id != command.command_id
                or feedback.command_sequence != command.sequence
                or not feedback.watchdog_latched
                or feedback.safe_mode_id not in (None, self.requirements.safe_mode_id)
            ):
                raise ValueError("Safe-program feedback disagrees with acknowledged command")
        if command is not None and command.kind == "SIGNAL":
            assert command.requested_state is not None
            if (
                feedback.command_id != command.command_id
                or feedback.command_sequence != command.sequence
                or feedback.measured_lamps != command.requested_state.lamps()
                or feedback.watchdog_latched
            ):
                raise ValueError("Lamp readback disagrees with acknowledged command")
        physical = feedback.origin == "independent_sensor" and self.capability.physical_device
        self.delivery = self.delivery.model_copy(
            update={
                "physically_confirmed": feedback if physical else None,
                "emulated_feedback": None if physical else feedback,
                "last_feedback_monotonic": time.monotonic(),
                "status": "CONFIRMED" if physical else "ACKNOWLEDGED",
            }
        )
        return feedback

    async def close(self) -> None:
        await (
            self.transport.close()
        )  # Local device watchdog remains authoritative after disconnect.

"""Executable device contract model, NOT cabinet firmware or physical feedback.

The watchdog runs in a separate OS process and uses its own monotonic clock.
The only implemented local safety program is explicitly named emulator-all-red.
"""

import asyncio
import multiprocessing as mp
import threading
import time
from multiprocessing.connection import Connection
from typing import Any
from uuid import uuid4

from traffic_core.hardware_protocol import (
    DeviceCapabilities,
    DeviceRequirements,
    HardwareAck,
    HardwareCommand,
    HardwareFeedback,
    authenticate,
    verify,
)
from traffic_core.models import Phase, SignalStage, SignalState
from traffic_core.settings import Timing


class DeviceKernel:
    def __init__(
        self,
        requirements: DeviceRequirements,
        instance: str,
        key: bytes,
        timing: Timing,
        now: float,
    ) -> None:
        if requirements.safe_mode_id != "emulator-all-red":
            raise ValueError("Emulator implements only its explicitly selected test safety program")
        self.requirements, self.instance, self.key, self.timing = (
            requirements,
            instance,
            key,
            timing,
        )
        self.boot_id = str(uuid4())
        self.sequence = 0
        self.last_command: HardwareCommand | None = None
        self.sample_sequence = 0
        self.signal = SignalState(phase=Phase.NS, stage=SignalStage.ALL_RED, elapsed_seconds=0)
        self.entered_at = now
        self.lease_deadline = now + requirements.max_lease_s
        self.watchdog_latched = False
        self.stuck_lamps: dict[str, Any] | None = None

    def capabilities(self) -> DeviceCapabilities:
        return authenticate(
            DeviceCapabilities(
                device_id=self.requirements.device_id,
                device_boot_id=self.boot_id,
                controller_instance=self.instance,
                safe_mode_id=self.requirements.safe_mode_id,
                independent_watchdog=True,
                physical_feedback=False,
                physical_device=False,
            ),
            self.key,
        )

    def enter_safe(self, now: float) -> None:
        if self.watchdog_latched:
            return
        self.watchdog_latched = True
        if self.signal.stage == SignalStage.GREEN:
            self.signal = self.signal.model_copy(update={"stage": SignalStage.YELLOW})
            self.entered_at = now

    def tick(self, now: float) -> None:
        if now >= self.lease_deadline:
            self.enter_safe(now)
        if self.watchdog_latched and self.signal.stage == SignalStage.YELLOW:
            clearance = (
                self.timing.pedestrian_clearance_seconds
                if self.signal.phase == Phase.PEDESTRIAN
                else self.timing.yellow_seconds
            )
            if now - self.entered_at >= clearance:
                self.signal = self.signal.model_copy(update={"stage": SignalStage.ALL_RED})
                self.entered_at = now

    def _transition_allowed(self, target: SignalState, now: float) -> bool:
        current = self.signal
        elapsed = now - self.entered_at
        if target.stage == current.stage and (
            target.phase == current.phase or target.stage == SignalStage.ALL_RED
        ):
            return target.stage != SignalStage.GREEN or elapsed < self.timing.max_green_seconds
        if current.stage == SignalStage.ALL_RED:
            return target.stage == SignalStage.GREEN and elapsed >= self.timing.all_red_seconds
        if current.stage == SignalStage.GREEN:
            minimum = (
                self.timing.pedestrian_green_seconds
                if current.phase == Phase.PEDESTRIAN
                else self.timing.min_green_seconds
            )
            return (
                target.stage == SignalStage.YELLOW
                and target.phase == current.phase
                and elapsed >= minimum
            )
        clearance = (
            self.timing.pedestrian_clearance_seconds
            if current.phase == Phase.PEDESTRIAN
            else self.timing.yellow_seconds
        )
        return target.stage == SignalStage.ALL_RED and elapsed >= clearance

    def receive(self, command: HardwareCommand, now: float, utc: float) -> HardwareAck:
        self.tick(now)
        reason = "accepted"
        try:
            verify(command, self.key)
            if (command.device_id, command.device_boot_id, command.controller_instance) != (
                self.requirements.device_id,
                self.boot_id,
                self.instance,
            ):
                raise ValueError("wrong_session")
            if (
                command.issued_at > utc + self.requirements.max_clock_skew_s
                or command.expires_at <= utc
                or command.expires_at - command.issued_at > self.requirements.max_lease_s + 1e-6
            ):
                raise ValueError("expired_or_invalid_ttl")
            if self.last_command and command.command_id == self.last_command.command_id:
                if command.checksum != self.last_command.checksum:
                    raise ValueError("duplicate_id_changed_payload")
                reason = "duplicate_no_lease_refresh"
            else:
                if command.sequence != self.sequence + 1:
                    raise ValueError("old_replay_or_out_of_order")
                if command.kind == "ENTER_SAFE":
                    if command.safe_mode_id != self.requirements.safe_mode_id:
                        raise ValueError("unknown_safe_program")
                    self.enter_safe(now)
                else:
                    if self.watchdog_latched:
                        raise ValueError("operator_rearm_required")
                    assert command.requested_state is not None
                    if not self._transition_allowed(command.requested_state, now):
                        raise ValueError("unsafe_transition")
                    if (self.signal.stage, self.signal.phase) != (
                        command.requested_state.stage,
                        command.requested_state.phase,
                    ):
                        self.entered_at = now
                    self.signal = command.requested_state.model_copy(update={"elapsed_seconds": 0})
                self.sequence, self.last_command = command.sequence, command
                # Heartbeats, duplicates, and malformed messages cannot renew the lease.
                self.lease_deadline = now + min(
                    self.requirements.max_lease_s, command.expires_at - utc
                )
            accepted = True
        except ValueError as error:
            accepted, reason = False, str(error)
        return authenticate(
            HardwareAck(
                device_id=self.requirements.device_id,
                device_boot_id=self.boot_id,
                controller_instance=self.instance,
                command_id=command.command_id,
                sequence=command.sequence,
                command_checksum=command.checksum,
                accepted=accepted,
                reason=reason,
                acknowledged_at=utc,
            ),
            self.key,
        )

    def feedback(self, now: float, utc: float) -> HardwareFeedback:
        self.tick(now)
        self.sample_sequence += 1
        return authenticate(
            HardwareFeedback(
                device_id=self.requirements.device_id,
                device_boot_id=self.boot_id,
                controller_instance=self.instance,
                sample_sequence=self.sample_sequence,
                command_id=self.last_command.command_id if self.last_command else None,
                command_sequence=self.sequence,
                measured_at=utc,
                measured_lamps=self.stuck_lamps or self.signal.lamps(),
                origin="emulator",
                watchdog_latched=self.watchdog_latched,
                safe_mode_id=self.requirements.safe_mode_id
                if self.watchdog_latched and self.signal.stage == SignalStage.ALL_RED
                else None,
            ),
            self.key,
        )


def device_process(
    connection: Connection,
    requirements: dict[str, Any],
    instance: str,
    key: bytes,
    timing: dict[str, Any],
) -> None:
    kernel = DeviceKernel(
        DeviceRequirements.model_validate(requirements),
        instance,
        key,
        Timing.model_validate(timing),
        time.monotonic(),
    )
    while True:
        kernel.tick(time.monotonic())
        if not connection.poll(0.01):
            continue
        try:
            request = connection.recv()
        except EOFError:
            # Device safety is not cancelled by loss of the host connection.
            while True:
                kernel.tick(time.monotonic())
                time.sleep(0.01)
        operation = request["operation"]
        if operation == "shutdown_emulator":
            return  # Test harness only; not part of the production protocol.
        if operation == "capabilities":
            response = kernel.capabilities().model_dump(mode="json")
        elif operation == "feedback":
            response = kernel.feedback(time.monotonic(), time.time()).model_dump(mode="json")
        else:
            response = kernel.receive(
                HardwareCommand.model_validate(request["command"]),
                time.monotonic(),
                time.time(),
            ).model_dump(mode="json")
        connection.send(response)


class ProcessDeviceTransport:
    """Integration fixture. It explicitly reports physical_device=False."""

    def __init__(
        self, requirements: DeviceRequirements, instance: str, key: bytes, timing: Timing
    ) -> None:
        context = mp.get_context("spawn")
        self.connection, child = context.Pipe()
        self.process = context.Process(
            target=device_process,
            args=(child, requirements.model_dump(), instance, key, timing.model_dump()),
            daemon=True,
        )
        self.lock = threading.Lock()
        self.process.start()
        child.close()

    def exchange(self, operation: str, command: HardwareCommand | None = None) -> dict[str, Any]:
        with self.lock:
            self.connection.send(
                {
                    "operation": operation,
                    "command": command.model_dump(mode="json") if command else None,
                }
            )
            if not self.connection.poll(2):
                raise TimeoutError("Device transport timeout")
            result: dict[str, Any] = self.connection.recv()
            return result

    async def capabilities(self) -> DeviceCapabilities:
        return DeviceCapabilities.model_validate(
            await asyncio.to_thread(self.exchange, "capabilities")
        )

    async def send(self, command: HardwareCommand) -> HardwareAck:
        return HardwareAck.model_validate(
            await asyncio.to_thread(self.exchange, "command", command)
        )

    async def feedback(self) -> HardwareFeedback:
        return HardwareFeedback.model_validate(await asyncio.to_thread(self.exchange, "feedback"))

    async def close(self) -> None:
        self.process.terminate()
        await asyncio.to_thread(self.process.join, 2)
        self.connection.close()

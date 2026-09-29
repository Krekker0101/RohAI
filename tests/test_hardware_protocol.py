import asyncio
import multiprocessing as mp
import os
import time
from multiprocessing.connection import Connection
from typing import cast

import pytest
from smart_traffic_backend.device_emulator import DeviceKernel, ProcessDeviceTransport
from smart_traffic_backend.protocol_hardware import ProtocolHardwareController
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

KEY = b"test-device-key-32-bytes-long-000"
INSTANCE = "controller-test"


def requirements() -> DeviceRequirements:
    return DeviceRequirements(
        device_id="junction-test",
        safe_mode_id="emulator-all-red",
        requirements_reference="test-requirements-v1",
        max_lease_s=8,
        ack_timeout_s=1,
        feedback_timeout_s=1,
        max_clock_skew_s=0.2,
    )


def active_device() -> tuple[DeviceKernel, float, float]:
    monotonic_now = 100.0
    utc_now = time.time()
    device = DeviceKernel(requirements(), INSTANCE, KEY, Timing(), monotonic_now)
    command = command_for(device, 1, utc_now, green())
    ack = device.receive(command, monotonic_now + 2, utc_now)
    assert ack.accepted
    return device, monotonic_now + 2, utc_now


def green() -> SignalState:
    return SignalState(phase=Phase.NS, stage=SignalStage.GREEN, elapsed_seconds=0)


def command_for(
    device: DeviceKernel, sequence: int, utc: float, signal: SignalState
) -> HardwareCommand:
    return authenticate(
        HardwareCommand(
            device_id="junction-test",
            device_boot_id=device.boot_id,
            controller_instance=INSTANCE,
            sequence=sequence,
            kind="SIGNAL",
            requested_state=signal,
            issued_at=utc,
            expires_at=utc + requirements().max_lease_s,
        ),
        KEY,
    )


def test_device_rejects_invalid_phase_request_and_expired_command() -> None:
    device = DeviceKernel(requirements(), INSTANCE, KEY, Timing(), 100)
    utc = time.time()
    invalid = command_for(device, 1, utc, green())
    assert not device.receive(invalid, 101, utc).accepted
    expired = command_for(device, 1, utc - 20, green())
    assert not device.receive(expired, 102, utc).accepted


def test_duplicate_is_idempotent_and_replay_or_out_of_order_is_rejected() -> None:
    device = DeviceKernel(requirements(), INSTANCE, KEY, Timing(), 100)
    utc = time.time()
    first = command_for(device, 1, utc, green())
    assert device.receive(first, 102, utc).accepted
    duplicate = device.receive(first, 103, utc + 1)
    assert duplicate.accepted and duplicate.reason == "duplicate_no_lease_refresh"
    replay = command_for(device, 1, utc + 2, green())
    assert not device.receive(replay, 103, utc + 2).accepted
    skipped = command_for(device, 3, utc + 2, green())
    assert not device.receive(skipped, 103, utc + 2).accepted


def test_watchdog_latches_and_runs_configured_emulator_safe_program() -> None:
    device, now, utc = active_device()
    device.tick(now + requirements().max_lease_s)
    assert device.watchdog_latched
    assert device.signal.stage == SignalStage.YELLOW
    device.tick(now + requirements().max_lease_s + Timing().yellow_seconds)
    assert str(device.signal.stage) == SignalStage.ALL_RED.value
    feedback = device.feedback(now + 12, utc + 12)
    assert feedback.safe_mode_id == "emulator-all-red"
    assert feedback.origin == "emulator"


def test_separate_device_process_watchdog_expires_without_host_commands() -> None:
    async def exercise() -> None:
        req = DeviceRequirements(
            device_id="junction-process-test",
            safe_mode_id="emulator-all-red",
            requirements_reference="process-watchdog-test-v1",
            max_lease_s=2,
            ack_timeout_s=0.2,
            feedback_timeout_s=0.2,
            max_clock_skew_s=0.2,
        )
        timing = Timing(
            min_green_seconds=1,
            max_green_seconds=45,
            fixed_green_seconds=1,
            yellow_seconds=1,
            all_red_seconds=1,
            pedestrian_green_seconds=1,
        )
        transport = ProcessDeviceTransport(req, INSTANCE, KEY, timing)
        device_id = req.device_id
        try:
            capability = await transport.capabilities()
            await asyncio.sleep(timing.all_red_seconds + 0.1)
            issued_at = time.time()
            command = authenticate(
                HardwareCommand(
                    device_id=device_id,
                    device_boot_id=capability.device_boot_id,
                    controller_instance=INSTANCE,
                    sequence=1,
                    kind="SIGNAL",
                    requested_state=green(),
                    issued_at=issued_at,
                    expires_at=issued_at + req.max_lease_s,
                ),
                KEY,
            )
            assert (await transport.send(command)).accepted
            deadline = time.monotonic() + 5
            feedback = await transport.feedback()
            while not feedback.watchdog_latched or feedback.safe_mode_id is None:
                if time.monotonic() >= deadline:
                    pytest.fail("device process watchdog did not enter its safe program")
                await asyncio.sleep(0.05)
                feedback = await transport.feedback()
            assert (
                feedback.measured_lamps
                == SignalState(phase=Phase.NS, stage=SignalStage.ALL_RED, elapsed_seconds=0).lamps()
            )
        finally:
            await transport.close()

    asyncio.run(exercise())


def controller_crash_worker(
    connection: Connection,
    result_connection: Connection,
    device_requirements: dict[str, str | float],
    key: bytes,
    instance: str,
) -> None:
    connection.send({"operation": "capabilities", "command": None})
    if not connection.poll(2):
        result_connection.send(False)
        os._exit(2)
    capability = DeviceCapabilities.model_validate(connection.recv())
    issued_at = time.time()
    requirements_model = DeviceRequirements.model_validate(device_requirements)
    command = authenticate(
        HardwareCommand(
            device_id=requirements_model.device_id,
            device_boot_id=capability.device_boot_id,
            controller_instance=instance,
            sequence=1,
            kind="SIGNAL",
            requested_state=green(),
            issued_at=issued_at,
            expires_at=issued_at + requirements_model.max_lease_s,
        ),
        key,
    )
    connection.send({"operation": "command", "command": command.model_dump(mode="json")})
    if not connection.poll(2):
        result_connection.send(False)
        os._exit(2)
    ack = HardwareAck.model_validate(connection.recv())
    result_connection.send(ack.accepted)
    os._exit(0)


def test_controller_process_crash_leaves_device_watchdog_running() -> None:
    async def exercise() -> None:
        req = DeviceRequirements(
            device_id="junction-crash-test",
            safe_mode_id="emulator-all-red",
            requirements_reference="controller-crash-test-v1",
            max_lease_s=3,
            ack_timeout_s=0.2,
            feedback_timeout_s=0.2,
            max_clock_skew_s=0.2,
        )
        timing = Timing(
            min_green_seconds=1,
            max_green_seconds=45,
            fixed_green_seconds=1,
            yellow_seconds=1,
            all_red_seconds=1,
            pedestrian_green_seconds=1,
        )
        transport = ProcessDeviceTransport(req, INSTANCE, KEY, timing)
        context = mp.get_context("spawn")
        parent_result, child_result = context.Pipe(duplex=False)
        controller = context.Process(
            target=controller_crash_worker,
            args=(transport.connection, child_result, req.model_dump(), KEY, INSTANCE),
        )
        try:
            await asyncio.sleep(timing.all_red_seconds + 0.1)
            controller.start()
            child_result.close()
            await asyncio.to_thread(controller.join, 3)
            assert controller.exitcode == 0
            assert parent_result.recv()

            deadline = time.monotonic() + 6
            feedback = await transport.feedback()
            while not feedback.watchdog_latched or feedback.safe_mode_id is None:
                if time.monotonic() >= deadline:
                    pytest.fail("device watchdog did not recover after controller process crash")
                await asyncio.sleep(0.05)
                feedback = await transport.feedback()
            assert (
                feedback.measured_lamps
                == SignalState(phase=Phase.NS, stage=SignalStage.ALL_RED, elapsed_seconds=0).lamps()
            )
        finally:
            if controller.is_alive():
                controller.terminate()
                await asyncio.to_thread(controller.join, 2)
            parent_result.close()
            await transport.close()

    asyncio.run(exercise())


class KernelTransport(HardwareTransport):
    def __init__(
        self, *, ack_loss: bool = False, network_loss: bool = False, stuck: bool = False
    ) -> None:
        self.monotonic = 100.0
        self.utc = time.time()
        self.device = DeviceKernel(requirements(), INSTANCE, KEY, Timing(), self.monotonic)
        self.ack_loss, self.network_loss = ack_loss, network_loss
        self.stuck = stuck

    async def capabilities(self) -> DeviceCapabilities:
        if self.network_loss:
            raise ConnectionError("injected network loss")
        return self.device.capabilities()

    async def send(self, command: HardwareCommand) -> HardwareAck:
        ack = self.device.receive(command, self.monotonic + 2, time.time())
        if self.ack_loss:
            raise TimeoutError("injected ACK loss")
        return ack

    async def feedback(self) -> HardwareFeedback:
        if self.stuck:
            self.device.stuck_lamps = {key: "red" for key in green().lamps()}
        return self.device.feedback(self.monotonic + 2, time.time())

    async def close(self) -> None:
        pass


def test_ack_and_feedback_are_separate_and_emulator_is_not_physical_confirmation() -> None:
    async def exercise() -> None:
        hardware_mock = KernelTransport()
        hardware = ProtocolHardwareController(hardware_mock, requirements(), INSTANCE, KEY)
        await hardware.apply(green())
        assert hardware.delivery.hardware_acknowledged is not None
        assert hardware.delivery.acknowledged_state == green()
        assert hardware.delivery.physically_confirmed is None
        assert hardware.delivery.emulated_feedback is not None
        await hardware.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("fault", ["ack_loss", "network_loss", "stuck"])
def test_transport_faults_do_not_report_physical_success(fault: str) -> None:
    async def exercise() -> None:
        transport = KernelTransport(**{fault: True})
        hardware = ProtocolHardwareController(transport, requirements(), INSTANCE, KEY)
        if fault == "network_loss":
            with pytest.raises(ConnectionError):
                await hardware.apply(green())
        else:
            with pytest.raises((TimeoutError, ValueError)):
                await hardware.apply(green())
        assert hardware.delivery.status == "FAULT"
        assert hardware.delivery.physically_confirmed is None

    asyncio.run(exercise())


@pytest.mark.parametrize("fault", ["invalid_action", "policy_exception", "policy_latency"])
def test_policy_faults_keep_baseline_control(fault: str) -> None:
    from smart_traffic_backend.config import Settings
    from smart_traffic_backend.hardware import MockHardwareController
    from smart_traffic_backend.runtime import Runtime
    from smart_traffic_backend.safety_architecture import PolicyCandidate, PolicyMode
    from traffic_core.models import TrafficState

    class InvalidPolicy:
        async def propose(self, observation: TrafficState) -> PolicyCandidate:
            if fault == "policy_exception":
                raise RuntimeError("injected policy exception")
            if fault == "policy_latency":
                await asyncio.sleep(0.2)
            return cast(
                PolicyCandidate,
                {"phase": "invalid_phase", "green_seconds": float("nan")},
            )

    async def exercise() -> None:
        hardware = MockHardwareController()
        runtime = Runtime(Settings(tick_seconds=0.05, hardware_timeout_seconds=0.05), hardware)
        runtime.configure_policy(PolicyMode.SHADOW, InvalidPolicy())
        runtime.engine.simulator.now = 2
        await runtime.start()
        await asyncio.sleep(0.15)
        assert runtime.latest.signals.stage == SignalStage.GREEN
        assert runtime._latest_decision is not None
        assert runtime._latest_decision.policy_error is not None
        assert runtime.latest.signals == hardware.last_signal
        await runtime.stop()

    asyncio.run(exercise())

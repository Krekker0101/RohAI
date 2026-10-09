"""Software fault injection, never evidence of physical readiness."""

import asyncio
import json
import time
import zlib

import pytest
from fastapi.testclient import TestClient
from smart_traffic_backend.config import Settings
from smart_traffic_backend.hardware import MockHardwareAdapter, ReplayHardwareAdapter
from smart_traffic_backend.hardware_service import HardwareApplicationService
from smart_traffic_backend.main import create_app
from smart_traffic_backend.maket.config import SerialSettings
from smart_traffic_backend.maket.serial import SerialESP32Adapter
from test_maket_mode import WireFixture
from traffic_core.device_state import DeviceLifecycle, DeviceState
from traffic_core.models import Phase, SignalStage, SignalState
from traffic_core.safety import validate_active_groups
from traffic_core.settings import Timing


def signal(phase: Phase = Phase.NS, stage: SignalStage = SignalStage.ALL_RED) -> SignalState:
    return SignalState(phase=phase, stage=stage, elapsed_seconds=0)


@pytest.mark.parametrize("adapter_type", [MockHardwareAdapter, ReplayHardwareAdapter])
def test_nonphysical_contract(adapter_type: type[MockHardwareAdapter]) -> None:
    async def run() -> None:
        adapter = adapter_type()
        service = HardwareApplicationService(adapter, Timing())
        await service.apply(signal())
        assert service.lifecycle.state == DeviceState.ACTIVE
        assert service.snapshot()["physical_execution"] is False
        await service.enter_safe()
        assert str(service.lifecycle.state) == DeviceState.EMERGENCY_STOP
        await service.close()
        assert adapter.closed

    asyncio.run(run())


def test_state_machine_and_conflicts() -> None:
    with pytest.raises(ValueError):
        DeviceLifecycle().transition(DeviceState.ACTIVE, "invalid")
    for groups in ({Phase.NS, Phase.EW}, {Phase.NS, Phase.PEDESTRIAN}):
        with pytest.raises(ValueError, match="Conflicting"):
            validate_active_groups(groups)
    validate_active_groups({Phase.NS})


def test_backend_replay_selection_is_observable_and_nonphysical() -> None:
    with TestClient(create_app(Settings(hardware_mode="replay"))) as client:
        system = client.get("/api/v1/system").json()
        assert system["hardware"] == "ReplayHardwareAdapter"
        assert system["hardware_mode"] == "replay"
        assert system["physical_execution"] is False
        hardware = client.get("/api/v1/hardware").json()
        assert hardware["confirmation_kind"] == "software_only"
        assert hardware["physical_execution"] is False


def test_physical_boundary_clearances() -> None:
    async def run() -> None:
        now = [0.0]
        service = HardwareApplicationService(
            MockHardwareAdapter(), Timing(), physical=True, clock=lambda: now[0]
        )
        with pytest.raises(RuntimeError, match="ARM"):
            await service.apply(signal(stage=SignalStage.GREEN))
        service.reset()
        for timestamp, phase, stage, accepted in (
            (0, Phase.NS, SignalStage.GREEN, False),
            (2, Phase.NS, SignalStage.GREEN, True),
            (9, Phase.NS, SignalStage.YELLOW, False),
            (10, Phase.NS, SignalStage.YELLOW, True),
            (12, Phase.NS, SignalStage.ALL_RED, False),
            (13, Phase.NS, SignalStage.ALL_RED, True),
            (13, Phase.EW, SignalStage.GREEN, False),
            (15, Phase.EW, SignalStage.GREEN, True),
            (16, Phase.NS, SignalStage.GREEN, False),
        ):
            now[0] = timestamp
            if accepted:
                await service.apply(signal(phase, stage))
            else:
                with pytest.raises(ValueError):
                    await service.apply(signal(phase, stage))

    asyncio.run(run())


class FaultWire(WireFixture):
    def __init__(self, fault: str) -> None:
        super().__init__()
        self.inject = fault

    def write(self, data: bytes) -> int:
        if self.inject == "disconnect" and self.commands:
            raise OSError("USB disconnected")
        count = super().write(data)
        if self.inject == "firmware":
            ack = json.loads(json.loads(self.response)["payload"])
            ack["firmware_version"] = "0.9.0"
            payload = json.dumps(ack)
            self.response = (
                json.dumps({"payload": payload, "crc32": f"{zlib.crc32(payload.encode()):08x}"})
                + "\n"
            ).encode()
        return count

    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
        if len(self.commands) > 1:
            if self.inject == "late":
                time.sleep(0.06)
            if self.inject == "retry" and len(self.commands) == 2:
                time.sleep(0.005)
                return b""
        return super().read_until(expected, size)


@pytest.mark.parametrize("fault", ["disconnect", "late", "firmware"])
def test_faults_invalidate_confirmation(fault: str) -> None:
    async def run() -> None:
        device = SerialESP32Adapter(SerialSettings(timeout_seconds=0.05), FaultWire(fault))
        try:
            if fault == "firmware":
                with pytest.raises(ValueError, match="firmware"):
                    await device.connect()
            else:
                await device.connect()
                with pytest.raises((OSError, TimeoutError)):
                    await device.command("STATUS")
            assert device.lifecycle.state == DeviceState.FAULT
            assert not device.connected and device.confirmed_phase is None
            with pytest.raises(RuntimeError, match="latched"):
                await device.command("SIGNAL", "NS_GREEN")
        finally:
            await device.close()

    asyncio.run(run())


def test_retry_is_bounded_and_preserves_identity() -> None:
    async def run() -> None:
        wire = FaultWire("retry")
        device = SerialESP32Adapter(SerialSettings(timeout_seconds=0.1), wire)
        await device.connect()
        await device.command("STATUS")
        assert len(wire.commands) == 3
        assert wire.commands[1] == wire.commands[2]
        await device.close()

    asyncio.run(run())


def test_unverified_protection_blocks_arm() -> None:
    async def run() -> None:
        wire = WireFixture()
        device = SerialESP32Adapter(SerialSettings(), wire)
        await device.connect()
        with pytest.raises(ValueError, match="protection"):
            await device.command("ARM")
        assert len(wire.commands) == 1
        await device.close()

    asyncio.run(run())


def test_duplicate_ack_cannot_confirm_a_new_command() -> None:
    class DuplicateAckWire(WireFixture):
        def __init__(self) -> None:
            super().__init__()
            self.old_response = b""
            self.duplicate = b""

        def write(self, data: bytes) -> int:
            count = super().write(data)
            if len(self.commands) == 1:
                self.old_response = self.response
            else:
                self.duplicate = self.old_response
            return count

        def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes:
            if self.duplicate:
                duplicate, self.duplicate = self.duplicate, b""
                return duplicate
            return super().read_until(expected, size)

    async def run() -> None:
        wire = DuplicateAckWire()
        device = SerialESP32Adapter(SerialSettings(), wire)
        await device.connect()
        first_id = device.ack["command_id"] if device.ack else None
        await device.command("STATUS")
        assert device.ack and device.ack["sequence"] == 2
        assert device.ack["command_id"] != first_id
        await device.close()

    asyncio.run(run())


def test_no_queue_cancellation_and_explicit_reconnection() -> None:
    async def run() -> None:
        wire = WireFixture()
        device = SerialESP32Adapter(SerialSettings(timeout_seconds=0.05), wire)
        await device.connect()
        wire.fault = "missing"
        task = asyncio.create_task(device.command("STATUS"))
        await asyncio.sleep(0.01)
        with pytest.raises(RuntimeError, match="queueing"):
            await device.command("STATUS")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert device.lifecycle.state == DeviceState.FAULT
        assert device.confirmed_phase is None
        await asyncio.sleep(0.06)
        wire.fault = None
        old_session = device.session
        await device.close()
        device.port = wire
        await device.connect()
        assert device.session != old_session
        assert str(device.lifecycle.state) == DeviceState.READY
        assert device.ack and device.ack["watchdog"] == "LATCHED"
        await device.close()

    asyncio.run(run())

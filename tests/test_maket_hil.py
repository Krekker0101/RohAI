"""Opt-in real USB HIL. Missing equipment is SKIP, never an emulator PASS."""

import asyncio
import json
import os
from pathlib import Path

import pytest
from smart_traffic_backend.maket.config import SerialSettings
from smart_traffic_backend.maket.serial import Esp32TrafficLightController, checksum


@pytest.mark.parametrize(
    "fault", ["duplicate", "out_of_order", "old_boot", "expired", "bad_crc", "version"]
)
def test_physical_firmware_rejects_bad_serial_commands(fault: str) -> None:
    port = os.environ.get("ROHAI_HIL_PORT")
    if not port:
        pytest.skip("No real ESP32 configured; firmware fault injection NOT TESTED")

    async def run() -> None:
        device = Esp32TrafficLightController(SerialSettings(port=port))
        try:
            await device.connect()
            await device.command("STATUS")
            assert device.last_command is not None
            packet = device.last_command.copy()
            if fault == "out_of_order":
                packet["sequence"] -= 1
            if fault == "old_boot":
                packet["boot"] += 1
            if fault == "expired":
                packet.update(sequence=packet["sequence"] + 1, deadline_ms=0)
            if fault == "version":
                packet["protocol"] = 2
            packet["crc32"] = checksum(packet)
            if fault == "bad_crc":
                packet["crc32"] = "00000000"
            with pytest.raises((RuntimeError, TimeoutError)):
                await asyncio.to_thread(device._exchange, packet)
            await device.command("STATUS")
            assert device.ack and device.ack["watchdog"] == "LATCHED"
        finally:
            await device.close()

    asyncio.run(run())


def test_physical_esp32_readback_estop_and_watchdog() -> None:
    port = os.environ.get("ROHAI_HIL_PORT")
    if not port:
        pytest.skip("Real ESP32 not configured: set ROHAI_HIL_PORT; no mock substitution")
    pytest.importorskip("serial")

    async def run() -> None:
        device = Esp32TrafficLightController(SerialSettings(port=port))
        try:
            await device.connect()
            await device.enter_safe()
            ack = device.ack
            assert ack and ack["actual_phase"] == "ALL_RED" and ack["watchdog"] == "LATCHED"
            assert ack["sense_mask"] == ack["output_mask"]
            evidence = {
                "equipment": "REAL_USB_ESP32",
                "port": port,
                "estop_ack": ack,
                "watchdog_timeout": "NOT_TESTED_LOCAL_ARM_REQUIRED",
            }
            if os.environ.get("ROHAI_HIL_ARM") == "1":
                # Operator must hold the local button. Green is never requested by this test.
                await device.command("ARM")
                await asyncio.sleep(1.6)
                await device.command("STATUS")
                ack = device.ack
                assert ack and ack["watchdog"] == "LATCHED" and ack["actual_phase"] == "ALL_RED"
                evidence["watchdog_timeout"] = "MEASURED_ALL_RED_AFTER_LEASE"
                evidence["watchdog_ack"] = ack
            await asyncio.to_thread(
                Path("artifacts/maket-validation/hil.json").write_text,
                json.dumps(evidence, indent=2),
            )
        finally:
            await device.close()

    asyncio.run(run())

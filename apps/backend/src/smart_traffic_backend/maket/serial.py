"""Bounded USB serial transactions. CRC is corruption detection, not authentication.

Exclusive physically trusted USB is required; Wi-Fi is intentionally unsupported.
Firmware ACK reports electrical sense bits separately from output intent.
"""

import asyncio
import json
import time
import uuid
import zlib
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field
from traffic_core.models import SignalStage, SignalState

from smart_traffic_backend.maket.config import SerialSettings

FIELDS = (
    "protocol",
    "boot",
    "session",
    "sequence",
    "command_id",
    "deadline_ms",
    "ttl_ms",
    "kind",
    "phase",
)
PHASES = {"ALL_RED", "NS_GREEN", "NS_YELLOW", "EW_GREEN", "EW_YELLOW", "PED_GREEN"}
MASKS = {
    "ALL_RED": 4681,
    "NS_GREEN": 4708,
    "NS_YELLOW": 4690,
    "EW_GREEN": 6409,
    "EW_YELLOW": 5257,
    "PED_GREEN": 8777,
}


class DeviceAck(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    protocol: int = Field(ge=1, le=1)
    boot: int = Field(ge=1, le=4294967295)
    session: str = Field(pattern=r"^[a-f0-9]{32}$")
    command_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    sequence: int = Field(ge=1)
    accepted: bool
    reason: str
    uptime_ms: int = Field(ge=0, le=4294967295)
    device_id: str
    domain: str
    watchdog_present: bool
    actual_phase: str
    output_mask: int = Field(ge=0, le=16383)
    sense_mask: int = Field(ge=0, le=16383)
    watchdog: str
    sense_fault: bool
    estop: bool
    min_green_ms: int = Field(gt=0)
    max_green_ms: int = Field(gt=0)
    yellow_ms: int = Field(gt=0)
    all_red_ms: int = Field(gt=0)
    ped_green_ms: int = Field(gt=0)
    ped_clearance_ms: int = Field(gt=0)


def checksum(packet: dict[str, Any]) -> str:
    text = "|".join(str(packet[k]) for k in FIELDS)
    return f"{zlib.crc32(text.encode('ascii')) & 0xFFFFFFFF:08x}"


def signal_name(signal: SignalState) -> str:
    if signal.stage == SignalStage.ALL_RED:
        return "ALL_RED"
    prefix = {"north_south": "NS", "east_west": "EW", "pedestrian": "PED"}[signal.phase.value]
    if prefix == "PED":
        return "PED_GREEN" if signal.stage == SignalStage.GREEN else "ALL_RED"
    return f"{prefix}_{signal.stage.value.upper()}"


class SerialPort(Protocol):
    def write(self, data: bytes) -> int: ...
    def read_until(self, expected: bytes = b"\n", size: int | None = None) -> bytes: ...
    def close(self) -> None: ...


class Esp32TrafficLightController:
    """Maket-only physical adapter; never creates an emulator on failure."""

    def __init__(self, settings: SerialSettings, port: SerialPort | None = None) -> None:
        self.settings = settings
        self.port = port
        self.session = uuid.uuid4().hex
        self.boot = 0
        self.sequence = 0
        self.device_time = 0
        self.clock_sample = 0.0
        self.ack: dict[str, Any] | None = None
        self.last_command: dict[str, Any] | None = None
        self.ack_latency_ms: float | None = None
        self.last_heartbeat: float | None = None
        self.connected = False
        self.fault: str | None = None
        self._lock = asyncio.Lock()
        self._inflight: asyncio.Task[dict[str, Any]] | None = None

    async def connect(self) -> None:
        if self.port is None:
            import serial  # type: ignore[import-untyped]

            from smart_traffic_backend.maket.discovery import automatic_port

            selected = (
                await asyncio.to_thread(automatic_port)
                if self.settings.port == "AUTO"
                else self.settings.port
            )

            self.port = await asyncio.to_thread(
                serial.Serial,
                selected,
                self.settings.baudrate,
                timeout=self.settings.timeout_seconds,
                write_timeout=self.settings.timeout_seconds,
            )
            # Most USB development boards reboot on DTR/open; no control during boot.
            await asyncio.sleep(2)
        response = await self.command("HELLO")
        if response.get("device_id") != self.settings.device_id:
            raise ValueError("Wrong ESP32 device identity")
        if response.get("domain") != "PHYSICAL_MAKET" or not response.get("watchdog_present"):
            raise ValueError("Firmware domain/watchdog mismatch")
        self.connected = True

    def _exchange(self, packet: dict[str, Any]) -> dict[str, Any]:
        assert self.port is not None
        raw = (json.dumps(packet, separators=(",", ":")) + "\n").encode("ascii")
        if self.port.write(raw) != len(raw):
            raise OSError("Partial serial write")
        deadline = time.monotonic() + self.settings.timeout_seconds
        while time.monotonic() < deadline:
            line = self.port.read_until(size=1536)
            if not line.endswith(b"\n") or len(line) >= 1536:
                continue
            try:
                response = json.loads(line)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(response, dict):
                continue
            # ACK integrity covers complete response bytes through a canonical payload.
            payload = response.get("payload")
            if not isinstance(payload, str) or response.get("crc32") != (
                f"{zlib.crc32(payload.encode('ascii')) & 0xFFFFFFFF:08x}"
            ):
                continue
            ack: dict[str, Any] = json.loads(payload)
            ack = DeviceAck.model_validate(ack).model_dump()
            if (
                ack.get("protocol") != 1
                or ack.get("session") != self.session
                or ack.get("command_id") != packet["command_id"]
                or ack.get("sequence") != packet["sequence"]
            ):
                continue
            if packet["kind"] != "HELLO" and ack.get("boot") != self.boot:
                raise RuntimeError("ESP32 restarted; operator rearm required")
            if not ack.get("accepted"):
                raise RuntimeError(f"ESP32 rejected: {ack.get('reason')}")
            if ack["device_id"] != self.settings.device_id or ack["domain"] != "PHYSICAL_MAKET":
                raise ValueError("ACK device/domain mismatch")
            if ack["actual_phase"] not in MASKS or ack["output_mask"] != MASKS[ack["actual_phase"]]:
                raise ValueError("ACK phase/output conflict")
            if packet["kind"] == "SIGNAL" and ack["actual_phase"] != packet["phase"]:
                raise ValueError("ACK actual phase differs from outstanding command")
            if packet["kind"] == "SIGNAL" and (
                ack["sense_mask"] != ack["output_mask"]
                or ack["watchdog"] != "OK"
                or ack["sense_fault"]
                or ack["estop"]
            ):
                raise ValueError("Electrical sense/watchdog disagrees with SIGNAL ACK")
            return ack
        raise TimeoutError("ESP32 ACK missing/corrupt; no mock fallback")

    async def command(self, kind: str, phase: str = "ALL_RED") -> dict[str, Any]:
        if phase not in PHASES:
            raise ValueError("Invalid phase")
        async with self._lock:
            if self._inflight is not None and not self._inflight.done():
                raise RuntimeError("Serial worker still occupied after timeout")
            self.sequence += 1
            now = time.monotonic()
            packet = {
                "protocol": 1,
                "boot": self.boot,
                "session": self.session,
                "sequence": self.sequence,
                "command_id": uuid.uuid4().hex,
                "deadline_ms": (
                    self.device_time + int((now - self.clock_sample) * 1000) + self.settings.ttl_ms
                )
                % (2**32)
                if self.boot
                else 0,
                "ttl_ms": self.settings.ttl_ms,
                "kind": kind,
                "phase": phase,
            }
            packet["crc32"] = checksum(packet)
            self.last_command = packet.copy()
            self._inflight = asyncio.create_task(asyncio.to_thread(self._exchange, packet))
            self._inflight.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
            try:
                async with asyncio.timeout(self.settings.timeout_seconds + 0.1):
                    ack = await asyncio.shield(self._inflight)
                self.ack_latency_ms = (time.monotonic() - now) * 1000
                self.boot, self.device_time = ack["boot"], ack["uptime_ms"]
                self.clock_sample = time.monotonic()
                self.ack = ack
                self.last_heartbeat = time.time()
                self.fault = None
                return ack
            except Exception as error:
                self.connected = False
                self.fault = str(error)
                raise

    async def apply(self, signals: SignalState) -> None:
        await self.command("SIGNAL", signal_name(signals))

    async def enter_safe(self) -> None:
        await self.command("ESTOP")

    async def close(self) -> None:
        self.connected = False
        if self.port is not None:
            await asyncio.to_thread(self.port.close)
            self.port = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "command_sent": self.last_command,
            "ack": self.ack,
            "fault": self.fault,
            "ack_latency_ms": self.ack_latency_ms,
            "last_heartbeat": self.last_heartbeat,
            "feedback_kind": "electrical_readback_not_optical_lamp_confirmation",
        }

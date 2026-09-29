"""Authenticated device lease protocol. ACK is never physical lamp evidence."""

import hashlib
import hmac
import json
from typing import Literal, Protocol, Self
from uuid import uuid4

from pydantic import Field, model_validator

from traffic_core.models import Lamp, Model, SignalState


class DeviceRequirements(Model):
    """No default safe traffic mode: supplied by the site requirements authority."""

    device_id: str = Field(min_length=1)
    safe_mode_id: str = Field(min_length=1)
    requirements_reference: str = Field(min_length=1)
    max_lease_s: float = Field(gt=0, le=30)
    ack_timeout_s: float = Field(gt=0, le=5)
    feedback_timeout_s: float = Field(gt=0, le=5)
    max_clock_skew_s: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def deadlines(self) -> Self:
        if max(self.ack_timeout_s, self.feedback_timeout_s) >= self.max_lease_s:
            raise ValueError("ACK/feedback deadlines must be shorter than the device lease")
        return self


class HardwareCommand(Model):
    schema_version: Literal["rohai-hardware-v1"] = "rohai-hardware-v1"
    command_id: str = Field(default_factory=lambda: str(uuid4()))
    device_id: str
    device_boot_id: str
    controller_instance: str
    sequence: int = Field(ge=1)
    kind: Literal["SIGNAL", "ENTER_SAFE"]
    requested_state: SignalState | None = None
    safe_mode_id: str | None = None
    issued_at: float = Field(ge=0)
    expires_at: float = Field(gt=0)
    checksum: str = ""
    signature: str = ""

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if self.expires_at <= self.issued_at:
            raise ValueError("Positive TTL required")
        if self.kind == "SIGNAL" and (
            self.requested_state is None or self.safe_mode_id is not None
        ):
            raise ValueError("Signal command needs only requested_state")
        if self.kind == "ENTER_SAFE" and (
            not self.safe_mode_id or self.requested_state is not None
        ):
            raise ValueError("Safe-mode command needs only configured safe_mode_id")
        return self


class HardwareAck(Model):
    device_id: str
    device_boot_id: str
    controller_instance: str
    command_id: str
    sequence: int = Field(ge=1)
    command_checksum: str
    accepted: bool
    reason: str
    acknowledged_at: float = Field(ge=0)
    signature: str = ""


class HardwareFeedback(Model):
    device_id: str
    device_boot_id: str
    controller_instance: str
    sample_sequence: int = Field(ge=1)
    command_id: str | None
    command_sequence: int = Field(ge=0)
    measured_at: float = Field(ge=0)
    measured_lamps: dict[str, Lamp]
    origin: Literal["independent_sensor", "emulator"]
    watchdog_latched: bool
    safe_mode_id: str | None
    signature: str = ""

    @model_validator(mode="after")
    def complete_readback(self) -> Self:
        if set(self.measured_lamps) != {"north", "south", "east", "west", "pedestrian"}:
            raise ValueError("Readback must cover every lamp group")
        return self


class DeviceCapabilities(Model):
    device_id: str
    device_boot_id: str
    controller_instance: str
    safe_mode_id: str
    independent_watchdog: bool
    physical_feedback: bool
    physical_device: bool
    commissioning_evidence: str | None = None
    signature: str = ""


def canonical(value: Model, *, exclude: set[str]) -> bytes:
    return json.dumps(
        value.model_dump(mode="json", exclude=exclude),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def authenticate[T: Model](value: T, key: bytes) -> T:
    if len(key) < 32:
        raise ValueError("HMAC key must contain at least 32 bytes")
    if isinstance(value, HardwareCommand):
        value = value.model_copy(
            update={
                "checksum": hashlib.sha256(
                    canonical(value, exclude={"checksum", "signature"})
                ).hexdigest()
            }
        )
    signature = hmac.new(key, canonical(value, exclude={"signature"}), hashlib.sha256).hexdigest()
    return value.model_copy(update={"signature": signature})


def verify(value: Model, key: bytes) -> None:
    expected = authenticate(value, key)
    signature = expected.model_dump(mode="python").get("signature")
    if not hmac.compare_digest(str(getattr(value, "signature", "")), str(signature)):
        raise ValueError("Device message authentication failed")
    checksum = expected.model_dump(mode="python").get("checksum")
    if isinstance(value, HardwareCommand) and value.checksum != checksum:
        raise ValueError("Command checksum failed")


class HardwareTransport(Protocol):
    """A vendor adapter must preserve correlation and enforce its own bounded I/O."""

    async def capabilities(self) -> DeviceCapabilities: ...
    async def send(self, command: HardwareCommand) -> HardwareAck: ...
    async def feedback(self) -> HardwareFeedback: ...
    async def close(self) -> None: ...

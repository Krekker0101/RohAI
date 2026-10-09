"""Explicit device lifecycle; faults never imply successful physical execution."""

from collections import deque
from enum import StrEnum
from time import monotonic


class DeviceState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    READY = "READY"
    ACTIVE = "ACTIVE"
    DEGRADED = "DEGRADED"
    FAULT = "FAULT"
    EMERGENCY_STOP = "EMERGENCY_STOP"


TRANSITIONS: dict[DeviceState, set[DeviceState]] = {
    DeviceState.DISCONNECTED: {DeviceState.CONNECTING},
    DeviceState.CONNECTING: {DeviceState.READY, DeviceState.FAULT, DeviceState.DISCONNECTED},
    DeviceState.READY: {DeviceState.ACTIVE, DeviceState.EMERGENCY_STOP, DeviceState.FAULT},
    DeviceState.ACTIVE: {DeviceState.DEGRADED, DeviceState.FAULT, DeviceState.EMERGENCY_STOP},
    DeviceState.DEGRADED: {DeviceState.FAULT, DeviceState.EMERGENCY_STOP},
    DeviceState.FAULT: {DeviceState.CONNECTING, DeviceState.EMERGENCY_STOP},
    DeviceState.EMERGENCY_STOP: {DeviceState.CONNECTING, DeviceState.READY, DeviceState.FAULT},
}


class DeviceLifecycle:
    def __init__(self) -> None:
        self.state = DeviceState.DISCONNECTED
        self.events: deque[dict[str, str | float]] = deque(maxlen=256)

    def transition(self, target: DeviceState, reason: str) -> None:
        if target == self.state:
            return
        if target != DeviceState.DISCONNECTED and target not in TRANSITIONS[self.state]:
            raise ValueError(f"Invalid device transition: {self.state} -> {target}")
        self.events.append({"at": monotonic(), "from": self.state, "to": target, "reason": reason})
        self.state = target

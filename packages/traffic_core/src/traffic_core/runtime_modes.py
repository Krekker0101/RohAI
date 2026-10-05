"""Domain boundaries; adapters never infer permission from an input URI."""

from enum import StrEnum
from typing import Protocol

from traffic_core.models import Decision, SignalState, TrafficState


class RuntimeMode(StrEnum):
    SIMULATION = "SIMULATION"
    VIDEO_REPLAY = "VIDEO_REPLAY"
    PHYSICAL_MAKET = "PHYSICAL_MAKET"
    REAL_ROAD = "REAL_ROAD"


# Existing state is the common observation contract, not simulator truth.
type TrafficObservation = TrafficState


class TrafficPolicy(Protocol):
    async def decide(self, observation: TrafficObservation) -> Decision: ...


class OptionalMotionAdapter(Protocol):
    """Future motorized stand extension; no implementation or implicit actuation."""

    async def stop(self) -> None: ...


class PhysicalOutputs(Protocol):
    async def apply(self, signals: SignalState) -> None: ...
    async def enter_safe(self) -> None: ...
    async def close(self) -> None: ...

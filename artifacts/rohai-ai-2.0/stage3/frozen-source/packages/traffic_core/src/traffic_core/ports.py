from typing import Protocol

from traffic_core.models import SignalState, TrafficState


class TrafficStateSource(Protocol):
    def snapshot(self) -> TrafficState: ...


class HardwareController(Protocol):
    async def apply(self, signals: SignalState) -> None: ...

    async def close(self) -> None: ...

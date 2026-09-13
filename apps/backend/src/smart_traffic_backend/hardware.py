from traffic_core.models import Phase, SignalStage, SignalState


class MockHardwareController:
    """Bounded memory mock. Real adapters must add ACK and device watchdog."""

    def __init__(self) -> None:
        self.last_signal = SignalState(phase=Phase.NS, stage=SignalStage.ALL_RED, elapsed_seconds=0)
        self.commands_sent = 0
        self.closed = False

    async def apply(self, signals: SignalState) -> None:
        if self.closed:
            raise RuntimeError("Hardware controller is closed")
        self.last_signal = signals
        self.commands_sent += 1

    async def close(self) -> None:
        self.closed = True

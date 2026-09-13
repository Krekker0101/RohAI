import asyncio
import logging
import threading
import time
from contextlib import suppress

from traffic_core.models import SignalState, Telemetry
from traffic_core.ports import HardwareController
from traffic_simulator.engine import SimulationEngine

from smart_traffic_backend.config import Settings

logger = logging.getLogger(__name__)


class Runtime:
    def __init__(self, settings: Settings, hardware: HardwareController) -> None:
        self.settings = settings
        self.hardware = hardware
        self.engine = SimulationEngine(settings.scenario, settings.timing, settings.policy)
        self.task: asyncio.Task[None] | None = None
        self.last_tick: float | None = None
        self.failure: str | None = None
        self.latest = self.engine.snapshot()
        self.subscribers: set[asyncio.Queue[Telemetry]] = set()
        self.comparison_lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return (
            self.task is not None
            and not self.task.done()
            and self.failure is None
            and self.last_tick is not None
            and time.monotonic() - self.last_tick < self.settings.stale_after_seconds
        )

    async def start(self) -> None:
        await self._apply(self.latest.signals)
        self.last_tick = time.monotonic()
        self.task = asyncio.create_task(self._run(), name="traffic-simulation")

    async def _apply(self, signal: SignalState) -> None:
        async with asyncio.timeout(self.settings.hardware_timeout_seconds):
            await self.hardware.apply(signal)

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.settings.tick_seconds)
                if self.last_tick is not None and (
                    time.monotonic() - self.last_tick >= self.settings.stale_after_seconds
                ):
                    raise RuntimeError("Telemetry clock became stale")
                snapshot = self.engine.step(self.settings.tick_seconds)
                await self._apply(snapshot.signals)
                if (snapshot.signals.phase, snapshot.signals.stage) != (
                    self.latest.signals.phase,
                    self.latest.signals.stage,
                ):
                    logger.info(
                        "Signal transition: phase=%s stage=%s simulation_time=%.2f",
                        snapshot.signals.phase,
                        snapshot.signals.stage,
                        snapshot.traffic.simulation_time,
                    )
                self.latest = snapshot
                self.last_tick = time.monotonic()
                self._publish()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Traffic runtime failed; requesting all red")
            self.failure = "runtime_failure"
            await self._all_red()

    def _publish(self) -> None:
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(self.latest)

    async def _all_red(self) -> None:
        signal = self.engine.safety.fail_safe(self.engine.simulator.now)
        self.latest = self.engine.snapshot()
        self._publish()
        try:
            await self._apply(signal)
        except Exception:
            logger.exception("All-red command failed; device watchdog must stop hardware")

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        await self._all_red()
        async with asyncio.timeout(self.settings.hardware_timeout_seconds):
            await self.hardware.close()

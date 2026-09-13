from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import TYPE_CHECKING

from traffic_core.models import Decision, Lamp, Model, Phase, SignalState
from traffic_core.observed_controller import ObservedTrafficController
from traffic_core.ports import HardwareController
from traffic_vision.geometry import load_geometry
from traffic_vision.models import PipelineStatus, TrackSnapshot, VisionTrafficState

from smart_traffic_backend.config import Settings

if TYPE_CHECKING:
    from traffic_vision.pipeline import VisionPipeline

logger = logging.getLogger(__name__)


class VisionTelemetry(Model):
    schema_version: str = "2.0-vision"
    sequence: int
    mode: str
    traffic: VisionTrafficState | None
    tracks: tuple[TrackSnapshot, ...]
    signals: SignalState
    lamps: dict[str, Lamp]
    decision: Decision
    green_target_seconds: float
    emergency_phase: Phase | None
    pipeline: PipelineStatus
    failure: str | None


class VisionRuntime:
    def __init__(
        self,
        settings: Settings,
        hardware: HardwareController,
        pipeline: VisionPipeline | None = None,
    ) -> None:
        self.settings, self.hardware = settings, hardware
        self.engine = ObservedTrafficController(settings.timing, settings.policy)
        self._signal_state = self.engine.safety.snapshot(0)
        if settings.mode == "calibration":
            self.engine.safety.fail_safe(0)
        if pipeline is None:
            from traffic_vision.analytics import TrafficAnalyzer
            from traffic_vision.detector import CalibrationDetector, SyntheticDetector, YOLODetector
            from traffic_vision.pipeline import VisionPipeline
            from traffic_vision.sources import create_source

            geometry = load_geometry(settings.vision.geometry_path)
            if geometry.source_id != settings.vision.source_id:
                raise ValueError("Vision source and geometry IDs do not match")
            detector = (
                CalibrationDetector()
                if settings.mode == "calibration"
                else SyntheticDetector()
                if settings.vision.source == "synthetic"
                else YOLODetector(settings.vision)
            )
            pipeline = VisionPipeline(
                create_source(settings.vision),
                detector,
                TrafficAnalyzer(geometry, settings.vision.analytics),
                settings.vision,
            )
        self.pipeline = pipeline
        self.pipeline.signal_provider = lambda: self._signal_state
        self.task: asyncio.Task[None] | None = None
        self.failure: str | None = None
        self.comparison_lock = threading.Lock()
        self._subscribers: set[asyncio.Queue[VisionTelemetry]] = set()
        self._sequence = 0
        self._started = 0.0
        self.latest = self._snapshot()

    @property
    def ready(self) -> bool:
        latest = self.pipeline.latest
        return (
            self.task is not None
            and not self.task.done()
            and self.failure is None
            and self.latest.traffic is not None
            and self.pipeline.status().state == "running"
            and latest is not None
            and time.monotonic() - latest.captured_at <= self.settings.vision.stale_seconds
        )

    def _snapshot(self) -> VisionTelemetry:
        published = self.pipeline.latest
        return VisionTelemetry(
            sequence=self._sequence,
            mode=self.settings.mode,
            traffic=published.analysis.traffic if published is not None else None,
            tracks=published.analysis.tracks if published is not None else (),
            signals=self._signal_state,
            lamps=self._signal_state.lamps(),
            decision=self.engine.decision,
            green_target_seconds=self.engine.safety.green_seconds,
            emergency_phase=self.engine.emergency_phase,
            pipeline=self.pipeline.status(),
            failure=self.failure,
        )

    async def start(self) -> None:
        self._started = time.monotonic()
        await self._apply(self._signal_state)
        self.pipeline.start()
        self.task = asyncio.create_task(self._run(), name="vision-controller")

    async def _apply(self, signals: SignalState) -> None:
        async with asyncio.timeout(self.settings.hardware_timeout_seconds):
            await self.hardware.apply(signals)
        self._signal_state = signals

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.settings.tick_seconds)
                now = time.monotonic() - self._started
                status = self.pipeline.status()
                observed = self.pipeline.latest
                if status.state in ("failed", "ended", "stopped"):
                    self.failure = f"vision_{status.state}"
                    break
                if observed is None:
                    if now > self.settings.vision.startup_timeout_seconds:
                        self.failure = "vision_startup_timeout"
                        break
                    continue
                if time.monotonic() - observed.captured_at > self.settings.vision.stale_seconds:
                    self.failure = "vision_stale"
                    break
                self.engine.advance(observed.core_state, now)
                await self._apply(self.engine.safety.snapshot(now))
                self._publish()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Vision control loop failed")
            self.failure = "vision_control_failure"
        finally:
            try:
                await self._all_red()
            finally:
                try:
                    await asyncio.to_thread(self.pipeline.stop)
                finally:
                    self._publish()

    def _publish(self) -> None:
        self._sequence += 1
        self.latest = self._snapshot()
        for queue in self._subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(self.latest)

    async def _all_red(self) -> None:
        now = max(0, time.monotonic() - self._started) if self._started else 0
        signal = self.engine.safety.fail_safe(now)
        self._signal_state = signal
        try:
            await self._apply(signal)
        except Exception:
            logger.exception("All-red request failed; device watchdog is required")
        try:
            await asyncio.to_thread(self.pipeline.terminal_frame, signal)
        except Exception:
            logger.exception("Could not render terminal vision frame")
        self._publish()

    async def stream(self) -> AsyncGenerator[VisionTelemetry, None]:
        queue: asyncio.Queue[VisionTelemetry] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        queue.put_nowait(self.latest)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    async def stop(self) -> None:
        try:
            if self.task is not None:
                self.task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.task
        finally:
            await self._all_red()
            try:
                await asyncio.to_thread(self.pipeline.stop)
            finally:
                async with asyncio.timeout(self.settings.hardware_timeout_seconds):
                    await self.hardware.close()

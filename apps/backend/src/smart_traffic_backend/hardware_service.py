"""Shared execution boundary. Physical adapters must be commissioned by MAKET."""

import asyncio
import math
import time
from collections.abc import Callable

from traffic_core.device_state import DeviceLifecycle, DeviceState
from traffic_core.models import Phase, SignalStage, SignalState
from traffic_core.ports import HardwareController
from traffic_core.settings import Timing


class HardwareApplicationService:
    def __init__(
        self,
        adapter: HardwareController,
        timing: Timing,
        *,
        physical: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.adapter, self.timing, self.physical, self.clock = adapter, timing, physical, clock
        self.lifecycle = DeviceLifecycle()
        self.desired: SignalState | None = None
        self.confirmed: SignalState | None = None
        self.entered_at = clock()
        self._busy = False
        self._generation = 0

    def reset(self) -> None:
        """Call only after explicit operator ARM and successful device preflight."""
        self._generation += 1
        self.lifecycle.transition(DeviceState.CONNECTING, "operator_reset")
        self.lifecycle.transition(DeviceState.READY, "preflight_and_arm_completed")
        self.confirmed = SignalState(phase=Phase.NS, stage=SignalStage.ALL_RED, elapsed_seconds=0)
        self.desired = None
        self.entered_at = self.clock()

    def _validate_transition(self, signal: SignalState) -> None:
        if not self.physical:
            return  # Simulation time is governed by the existing SafetyController.
        if self.lifecycle.state not in {DeviceState.READY, DeviceState.ACTIVE}:
            raise RuntimeError("Physical execution requires operator ARM")
        previous = self.confirmed
        assert previous is not None
        age = self.clock() - self.entered_at
        if not math.isfinite(age) or age < 0:
            raise ValueError("Hardware safety clock must be finite and monotonic")
        if (previous.phase, previous.stage) == (signal.phase, signal.stage):
            if signal.stage == SignalStage.GREEN and age >= self.timing.max_green_seconds:
                raise ValueError("Maximum continuous green exceeded")
            return
        if previous.stage == SignalStage.ALL_RED:
            allowed = signal.stage == SignalStage.GREEN and age >= self.timing.all_red_seconds
        elif previous.stage == SignalStage.GREEN:
            minimum = (
                self.timing.pedestrian_green_seconds
                if previous.phase == Phase.PEDESTRIAN
                else self.timing.min_green_seconds
            )
            allowed = (
                signal.phase == previous.phase
                and signal.stage == SignalStage.YELLOW
                and age >= minimum
            )
        else:
            clearance = (
                self.timing.pedestrian_clearance_seconds
                if previous.phase == Phase.PEDESTRIAN
                else self.timing.yellow_seconds
            )
            allowed = signal.stage == SignalStage.ALL_RED and age >= clearance
        if not allowed:
            raise ValueError("Conflicting phase or incomplete green/yellow/all-red interval")

    async def apply(self, signals: SignalState) -> None:
        if self._busy:
            raise RuntimeError("Hardware command already in flight; queueing forbidden")
        signal = SignalState.model_validate(signals.model_dump())
        self._validate_transition(signal)
        self.desired = signal
        self._busy = True
        generation = self._generation
        try:
            await self.adapter.apply(signal)
            if generation != self._generation:
                raise RuntimeError("Signal superseded by emergency stop")
            if self.confirmed is None or (self.confirmed.phase, self.confirmed.stage) != (
                signal.phase,
                signal.stage,
            ):
                self.entered_at = self.clock()
            self.confirmed = signal
            if self.lifecycle.state == DeviceState.DISCONNECTED:
                self.lifecycle.transition(DeviceState.CONNECTING, "nonphysical_start")
                self.lifecycle.transition(DeviceState.READY, "nonphysical_ready")
            self.lifecycle.transition(DeviceState.ACTIVE, "adapter_completed")
        except (Exception, asyncio.CancelledError):
            self.confirmed = None
            if self.lifecycle.state != DeviceState.DISCONNECTED:
                self.lifecycle.transition(DeviceState.FAULT, "execution_failed_or_cancelled")
            raise
        finally:
            self._busy = False

    async def enter_safe(self) -> None:
        self._generation += 1
        self.desired = SignalState(phase=Phase.NS, stage=SignalStage.ALL_RED, elapsed_seconds=0)
        self.confirmed = None
        stop = getattr(self.adapter, "enter_safe", None)
        try:
            if stop is not None:
                await stop()
            elif not self.physical:
                await self.adapter.apply(self.desired)
            else:
                raise RuntimeError("Physical safe program unavailable")
            self.confirmed = self.desired
            if self.lifecycle.state == DeviceState.DISCONNECTED:
                self.lifecycle.transition(DeviceState.CONNECTING, "stop")
                self.lifecycle.transition(DeviceState.READY, "nonphysical_stop")
            self.lifecycle.transition(DeviceState.EMERGENCY_STOP, "stop_acknowledged")
        except Exception:
            if self.lifecycle.state != DeviceState.DISCONNECTED:
                self.lifecycle.transition(DeviceState.FAULT, "stop_unconfirmed")
            raise

    async def close(self) -> None:
        try:
            if self.physical:
                await self.enter_safe()
        finally:
            await self.adapter.close()
            self.lifecycle.transition(DeviceState.DISCONNECTED, "closed")

    def snapshot(self) -> dict[str, object]:
        return {
            "state": self.lifecycle.state,
            "physical_execution": self.physical,
            "desired": self.desired.model_dump(mode="json") if self.desired else None,
            "confirmed": self.confirmed.model_dump(mode="json") if self.confirmed else None,
            "confirmation_kind": "electrical_readback" if self.physical else "software_only",
            "events": list(self.lifecycle.events),
        }

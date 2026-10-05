from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import TYPE_CHECKING

from traffic_core.models import Decision, Lamp, Model, Phase, SignalStage, SignalState, TrafficState
from traffic_core.observed_controller import ObservedTrafficController
from traffic_core.ports import HardwareController
from traffic_vision.geometry import load_geometry
from traffic_vision.models import (
    AnalysisSnapshot,
    PipelineStatus,
    TrackSnapshot,
    VisionTrafficState,
)

from smart_traffic_backend.config import Settings
from smart_traffic_backend.improvement import ImprovementLog, ImprovementWriter
from smart_traffic_backend.safety_architecture import (
    ComponentHealth,
    DecisionAudit,
    DecisionJournal,
    HardwareStateStages,
    LearnedPolicyProposer,
    OperationalHealth,
    PolicyCandidate,
    PolicyMode,
    PolicyWorker,
    RecoveryChecks,
    RecoveryStateMachine,
    SafetyStatus,
    capture_hardware_state,
)

if TYPE_CHECKING:
    from traffic_vision.pipeline import VisionPipeline

logger = logging.getLogger(__name__)


class VisionTelemetry(Model):
    schema_version: str = "3.0-vision"
    control_ready: bool = False
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
    safety: SafetyStatus = SafetyStatus()


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
        self.policy_mode = PolicyMode.DISABLED
        self.policy_proposer: LearnedPolicyProposer | None = None
        self._policy_worker = PolicyWorker()
        self.decision_journal = DecisionJournal()
        self.improvement_log = ImprovementLog(settings.improvement_log_path)
        self.improvement_writer = ImprovementWriter(self.improvement_log)
        self._audit_observation: TrafficState | None = None
        self._review_tracks: set[str] = set()
        self._observation_sequence = 0
        self._latest_decision: DecisionAudit | None = None
        self._policy_task: asyncio.Task[None] | None = None
        self.recovery = RecoveryStateMachine()
        self.hardware_state = HardwareStateStages()
        self.health = OperationalHealth()
        self.latest = self._snapshot()

    @property
    def ready(self) -> bool:
        latest = self.pipeline.latest_analysis
        return (
            self.task is not None
            and not self.task.done()
            and self.failure is None
            and self.latest.traffic is not None
            and self.pipeline.status().state == "running"
            and latest is not None
            and time.monotonic() - latest.captured_at <= self.settings.vision.stale_seconds
        )

    @property
    def control_ready(self) -> bool:
        observation = self.pipeline.observation()
        return (
            self.settings.mode == "vision"
            and self.ready
            and not self.engine.safety.faulted
            and observation is not None
            and all(
                m.observation.reliable and m.queue_count is not None
                for m in observation.traffic.directions.values()
            )
        )

    def _snapshot(self) -> VisionTelemetry:
        published = self.pipeline.latest_analysis
        observation = self.pipeline.observation()
        return VisionTelemetry(
            control_ready=self.control_ready,
            sequence=self._sequence,
            mode=self.settings.mode,
            traffic=observation.traffic if observation is not None else None,
            tracks=published.analysis.tracks if published is not None else (),
            signals=self._signal_state,
            lamps=self._signal_state.lamps(),
            decision=self.engine.decision,
            green_target_seconds=self.engine.safety.green_seconds,
            emergency_phase=self.engine.emergency_phase,
            pipeline=self.pipeline.status(),
            failure=self.failure,
            safety=self.safety_status(),
        )

    def safety_status(self) -> SafetyStatus:
        return SafetyStatus(
            policy_mode=self.policy_mode,
            recovery_state=self.recovery.state,
            recovery_reason=self.recovery.reason,
            recovery_checks=self.recovery.checks,
            health=self.health,
            hardware_state=self.hardware_state,
            latest_decision=self._latest_decision,
        )

    def configure_policy(self, mode: PolicyMode, proposer: LearnedPolicyProposer | None) -> None:
        if mode in (PolicyMode.SHADOW, PolicyMode.ADVISORY) and proposer is None:
            raise ValueError("A policy evaluator must be loaded before SHADOW or ADVISORY")
        if mode == PolicyMode.ACTIVE:
            raise PermissionError("ACTIVE is blocked: no hardware production release gate exists")
        self.policy_mode, self.policy_proposer = mode, proposer

    def _schedule_policy_audit(
        self, traffic: TrafficState, baseline_phase: str, baseline_green_seconds: float
    ) -> None:
        self._audit_observation = traffic
        self._observation_sequence += 1
        sequence = self._observation_sequence
        mode, proposer = self.policy_mode, self.policy_proposer
        if mode not in (PolicyMode.SHADOW, PolicyMode.ADVISORY):
            return
        if (
            self._policy_task is not None and not self._policy_task.done()
        ) or self._policy_worker.busy:
            record = DecisionAudit(
                observation_sequence=sequence,
                actual_baseline_action={
                    "phase": baseline_phase,
                    "green_seconds": baseline_green_seconds,
                },
                safety_response="baseline_retained_policy_busy",
                policy_error="previous_policy_evaluation_still_running",
            )
            self.decision_journal.append(record)
            self._update_latest_decision(record)
            return

        async def evaluate() -> None:
            candidate: PolicyCandidate | None = None
            error: str | None = None
            if proposer is None:
                error = "policy_evaluator_unavailable"
            else:
                try:
                    async with asyncio.timeout(self.settings.hardware_timeout_seconds):
                        result = await self._policy_worker.propose(
                            proposer, traffic, self.settings.hardware_timeout_seconds
                        )
                    candidate = PolicyCandidate.model_validate(result)
                except TimeoutError:
                    error = "policy_timeout"
                except Exception as exception:
                    error = type(exception).__name__
            record = DecisionAudit(
                observation_sequence=sequence,
                actual_baseline_action={
                    "phase": baseline_phase,
                    "green_seconds": baseline_green_seconds,
                },
                ai_proposed_action=candidate.model_dump(mode="json") if candidate else None,
                safety_response=(
                    "baseline_retained_shadow_mode"
                    if mode == PolicyMode.SHADOW
                    else "baseline_retained_advisory_mode"
                ),
                policy_error=error,
            )
            self.decision_journal.append(record)
            self._update_latest_decision(record)

        self._policy_task = asyncio.create_task(evaluate(), name="vision-policy-audit")

    def _record_policy_outcome(self, traffic: TrafficState) -> None:
        record = self._latest_decision
        if record is None:
            return
        outcome: dict[str, str | float] = {"simulation_time": traffic.simulation_time}
        for approach in traffic.approaches:
            if approach.queue_length is not None:
                outcome[f"{approach.direction.value}_queue"] = float(approach.queue_length)
        if self.decision_journal.record_outcome(record.observation_sequence, outcome):
            self._update_latest_decision(self.decision_journal.snapshot()[-1])

    def _update_latest_decision(self, record: DecisionAudit) -> None:
        try:
            self.improvement_writer.submit(
                "proposal",
                record.observation_sequence,
                record.ai_proposed_action,
                record.policy_error,
            )
        except Exception:
            logger.exception("Could not persist policy review evidence")
        if (
            self._latest_decision is None
            or record.observation_sequence >= self._latest_decision.observation_sequence
        ):
            self._latest_decision = record

    async def start(self) -> None:
        await self._apply(self._signal_state)
        self.pipeline.start()
        # The control clock must be stamped when the loop starts. Left at 0.0 the
        # first tick measured against process uptime and looked infinitely stale,
        # so control declared a startup timeout before perception could publish.
        self._started = time.monotonic()
        self.task = asyncio.create_task(self._run(), name="vision-controller")

    async def _apply(self, signals: SignalState, requested: SignalState | None = None) -> None:
        try:
            async with asyncio.timeout(self.settings.hardware_timeout_seconds):
                enter_safe = getattr(self.hardware, "enter_safe", None)
                if self.engine.safety.faulted and enter_safe is not None:
                    await enter_safe()
                else:
                    await self.hardware.apply(signals)
        except Exception as error:
            self._record_hardware(requested or signals, signals, type(error).__name__)
            self.health = self.health.model_copy(
                update={
                    "hardware": ComponentHealth(status="fault", detail=type(error).__name__),
                }
            )
            raise
        self._record_hardware(requested or signals, signals)
        self._signal_state = signals

    def _record_hardware(
        self, requested: SignalState, approved: SignalState, error: str | None = None
    ) -> None:
        self.hardware_state = capture_hardware_state(self.hardware, requested, approved, error)
        delivery = getattr(self.hardware, "delivery", None)
        ack = getattr(delivery, "hardware_acknowledged", None)
        observation = self._audit_observation
        if observation is not None:
            analyzed = self.pipeline.latest_analysis
            evidence: dict[str, float | bool] = {}
            if analyzed is not None:
                current = {track.track_uid for track in analyzed.analysis.tracks}
                # A churn candidate is not an identity error or verified fragmentation.
                lost = self._review_tracks - current
                added = current - self._review_tracks
                if lost and added:
                    evidence["fragmentations"] = float(min(len(lost), len(added)))
                evidence["unusual_scene"] = analyzed.analysis.traffic.unknown_count > 0
                self._review_tracks = current
            try:
                self.improvement_writer.submit(
                    "decision",
                    observation,
                    self._observation_sequence,
                    [self.settings.vision.source_id] if self.settings.mode == "vision" else [],
                    self.settings.vision_model_version,
                    self.settings.controller_policy_version,
                    self.hardware_state.model_dump(mode="json"),
                    ack.model_dump(mode="json") if ack else None,
                    evidence,
                )
            except Exception:
                logger.exception("Improvement journal unavailable; control remains authoritative")
        delivery = getattr(self.hardware, "delivery", None)
        ack = getattr(delivery, "hardware_acknowledged", None)
        self.health = self.health.model_copy(
            update={
                "hardware": ComponentHealth(
                    status="fault" if error else "healthy" if ack else "degraded",
                    last_success_at=ack.acknowledged_at if ack else None,
                    detail=error or (None if ack else "hardware_ack_unavailable"),
                ),
                "last_hardware_ack_at": ack.acknowledged_at
                if ack
                else self.health.last_hardware_ack_at,
                "physical_production_ready": False,
            }
        )

    def degradation_reason(self, observation: AnalysisSnapshot | None) -> str | None:
        """Why the controller is degraded while still safe.

        Returns ``None`` only when vision health, analytics health and every direction's
        evidence are all usable. Degradation never latches FAULT and never enables a
        learned policy; it only makes the reduced-capability state operator-visible.
        """
        if self.health.vision.status != "healthy":
            return "vision_degraded"
        if self.health.analytics.status != "healthy":
            return "analytics_degraded"
        if observation is None or any(
            not metric.observation.reliable for metric in observation.traffic.directions.values()
        ):
            return "observation_degraded"
        return None

    def recovery_checks(self) -> RecoveryChecks:
        status = self.pipeline.status()
        beats = self.pipeline.heartbeat_timestamps()
        analysis = self.pipeline.latest_analysis
        now = time.time()
        observation = self.pipeline.observation()
        calibration_ok = bool(
            observation is not None
            and all(
                metric.observation.calibration_status == "valid"
                for metric in observation.traffic.directions.values()
            )
        )
        ack_at = self.health.last_hardware_ack_at
        return RecoveryChecks(
            source=self.pipeline.source.connected and status.state == "running",
            calibration=calibration_ok,
            freshness=bool(
                analysis is not None
                and now - beats.get("frame", 0) <= self.settings.vision.stale_seconds
            ),
            analytics=bool(
                analysis is not None
                and now - beats.get("analytics_update", 0)
                <= self.settings.vision.health.stage_timeout_seconds
            ),
            controller=self.task is not None and not self.task.done() and self.failure is None,
            hardware=bool(
                self.health.hardware.status == "healthy"
                and ack_at is not None
                and 0 <= now - ack_at <= self.settings.hardware_timeout_seconds
                and self.hardware_state.physically_confirmed is not None
            ),
        )

    def begin_recovery(self) -> None:
        self.recovery.begin_recovery()

    def qualify_recovery(self) -> RecoveryChecks:
        checks = self.recovery_checks()
        self.recovery.qualify(checks)
        return checks

    def confirm_recovery(self) -> None:
        if self.engine.safety.faulted:
            raise RuntimeError("Latched safety requires supervised runtime/device rearm")
        if not self.ready:
            raise RuntimeError(
                "A live, fresh control runtime is required for recovery confirmation"
            )
        if not self.recovery_checks().passed:
            raise RuntimeError("Recovery evidence expired or hardware is unconfirmed")
        self.recovery.confirm_operator()
        self.policy_mode = PolicyMode.DISABLED

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.settings.tick_seconds)
                now = time.monotonic() - self._started
                status = self.pipeline.status()
                observed = self.pipeline.latest_analysis
                beats = self.pipeline.heartbeat_timestamps()
                wall_now = time.time()
                self.health = self.health.model_copy(
                    update={
                        "vision": ComponentHealth(
                            status="healthy"
                            if status.state == "running" and self.pipeline.source.connected
                            else "degraded",
                            last_success_at=beats.get("frame"),
                        ),
                        "analytics": ComponentHealth(
                            status="healthy"
                            if beats.get("analytics_update", 0)
                            and wall_now - beats["analytics_update"]
                            <= self.settings.vision.health.stage_timeout_seconds
                            else "degraded",
                            last_success_at=beats.get("analytics_update"),
                        ),
                        "last_frame_at": beats.get("frame"),
                        "last_inference_at": beats.get("inference"),
                        "last_analytics_update_at": beats.get("analytics_update"),
                    }
                )
                if status.state in ("failed", "ended", "stopped"):
                    self.failure = f"vision_{status.state}"
                    self.recovery.fault(self.failure)
                    break
                if observed is None:
                    if now > self.settings.vision.startup_timeout_seconds:
                        self.failure = "vision_startup_timeout"
                        break
                    continue
                if time.monotonic() - observed.captured_at > self.settings.vision.stale_seconds:
                    self.failure = "vision_stale"
                    break
                measurement = self.pipeline.observation()
                assert measurement is not None
                degradation = self.degradation_reason(measurement)
                if degradation is None:
                    self.recovery.restore()
                else:
                    self.recovery.degrade(degradation)
                core_state = self.pipeline.analyzer.to_core_state(measurement)
                self._record_policy_outcome(core_state)
                self.engine.advance(core_state, now)
                if self.engine.safety.faulted:
                    self.recovery.fault("perception_fault_latched")
                requested = SignalState(
                    phase=self.engine.decision.phase,
                    stage=SignalStage.GREEN,
                    elapsed_seconds=0,
                )
                approved = self.engine.safety.snapshot(now)
                self._schedule_policy_audit(
                    core_state, requested.phase.value, self.engine.safety.green_seconds
                )
                await self._apply(approved, requested)
                self.health = self.health.model_copy(
                    update={
                        "controller": ComponentHealth(status="healthy", last_success_at=wall_now),
                        "last_control_tick_at": wall_now,
                    }
                )
                self._publish()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Vision control loop failed")
            self.failure = "vision_control_failure"
            self.recovery.fault(self.failure)
        finally:
            if self.failure is not None and self.recovery.state.value not in ("FAULT", "SAFE"):
                self.recovery.fault(self.failure)
            try:
                await self._all_red()
                if self.recovery.state.value == "FAULT":
                    self.recovery.mark_safe()
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
            enter_safe = getattr(self.hardware, "enter_safe", None)
            if enter_safe is not None:
                async with asyncio.timeout(self.settings.hardware_timeout_seconds):
                    await enter_safe()
                self._record_hardware(signal, signal)
            else:
                await self._apply(signal)
        except Exception:
            self._record_hardware(signal, signal, "safe_request_failed")
            logger.exception("All-red request failed; device watchdog is required")
        try:
            await asyncio.to_thread(self.pipeline.terminal_frame, signal)
        except Exception:
            logger.exception("Could not render terminal vision frame")
        self._publish()

    async def stream(self) -> AsyncGenerator[VisionTelemetry, None]:
        queue: asyncio.Queue[VisionTelemetry] = asyncio.Queue(maxsize=1)
        self._subscribers.add(queue)
        queue.put_nowait(self._snapshot())
        try:
            while True:
                await queue.get()
                yield self._snapshot()
        finally:
            self._subscribers.discard(queue)

    async def stop(self) -> None:
        try:
            if self.task is not None:
                self.task.cancel()
                with suppress(asyncio.CancelledError):
                    await self.task
                if self._policy_task is not None:
                    self._policy_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await self._policy_task
        finally:
            await self._all_red()
            try:
                await asyncio.to_thread(self.pipeline.stop)
            finally:
                async with asyncio.timeout(self.settings.hardware_timeout_seconds):
                    await self.hardware.close()
                await asyncio.to_thread(self.improvement_writer.close)
                self._policy_worker.close()

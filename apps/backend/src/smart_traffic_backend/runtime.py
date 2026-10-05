import asyncio
import logging
import threading
import time
from collections.abc import AsyncGenerator
from contextlib import suppress

from traffic_core.models import SignalStage, SignalState, Telemetry, TrafficState
from traffic_core.ports import HardwareController
from traffic_simulator.engine import SimulationEngine

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
    RecoveryStateMachine,
    SafetyStatus,
    capture_hardware_state,
)

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
        self.policy_mode = PolicyMode.DISABLED
        self.policy_proposer: LearnedPolicyProposer | None = None
        self._policy_worker = PolicyWorker()
        self.decision_journal = DecisionJournal()
        self.improvement_log = ImprovementLog(settings.improvement_log_path)
        self.improvement_writer = ImprovementWriter(self.improvement_log)
        self._audit_observation: TrafficState | None = None
        self._observation_sequence = 0
        self._latest_decision: DecisionAudit | None = None
        self._policy_task: asyncio.Task[None] | None = None
        self.recovery = RecoveryStateMachine()
        self.hardware_state = HardwareStateStages()
        self.health = OperationalHealth()

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

    async def _apply(self, signal: SignalState, requested: SignalState | None = None) -> None:
        try:
            async with asyncio.timeout(self.settings.hardware_timeout_seconds):
                await self.hardware.apply(signal)
        except Exception as error:
            self._record_hardware(requested or signal, signal, type(error).__name__)
            self.health = self.health.model_copy(
                update={
                    "hardware": ComponentHealth(status="fault", detail=type(error).__name__),
                }
            )
            raise
        self._record_hardware(requested or signal, signal)

    def _record_hardware(
        self, requested: SignalState, approved: SignalState, error: str | None = None
    ) -> None:
        self.hardware_state = capture_hardware_state(self.hardware, requested, approved, error)
        delivery = getattr(self.hardware, "delivery", None)
        ack = getattr(delivery, "hardware_acknowledged", None)
        observation = self._audit_observation
        if observation is not None:
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
        self,
        traffic: TrafficState,
        baseline_phase: str,
        baseline_green_seconds: float,
        safety_response: str,
    ) -> None:
        self._audit_observation = traffic
        self._observation_sequence += 1
        observation_sequence = self._observation_sequence
        mode = self.policy_mode
        proposer = self.policy_proposer
        if mode in (PolicyMode.DISABLED, PolicyMode.ACTIVE):
            return
        if (
            self._policy_task is not None and not self._policy_task.done()
        ) or self._policy_worker.busy:
            record = DecisionAudit(
                observation_sequence=observation_sequence,
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
                observation_sequence=observation_sequence,
                actual_baseline_action={
                    "phase": baseline_phase,
                    "green_seconds": baseline_green_seconds,
                },
                ai_proposed_action=(candidate.model_dump(mode="json") if candidate else None),
                safety_response=(
                    "baseline_retained_shadow_mode"
                    if mode == PolicyMode.SHADOW
                    else safety_response
                ),
                policy_error=error,
            )
            self.decision_journal.append(record)
            self._update_latest_decision(record)

        self._policy_task = asyncio.create_task(evaluate(), name="policy-audit")

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

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.settings.tick_seconds)
                if self.last_tick is not None and (
                    time.monotonic() - self.last_tick >= self.settings.stale_after_seconds
                ):
                    raise RuntimeError("Telemetry clock became stale")
                snapshot = self.engine.step(self.settings.tick_seconds)
                self._record_policy_outcome(snapshot.traffic)
                requested = SignalState(
                    phase=snapshot.decision.phase,
                    stage=SignalStage.GREEN,
                    elapsed_seconds=0,
                )
                self._schedule_policy_audit(
                    snapshot.traffic,
                    snapshot.decision.phase.value,
                    snapshot.green_target_seconds,
                    "baseline_retained",
                )
                await self._apply(snapshot.signals, requested)
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
                now = time.time()
                self.health = self.health.model_copy(
                    update={
                        "controller": ComponentHealth(status="healthy", last_success_at=now),
                        "last_control_tick_at": now,
                    }
                )
                self._publish()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Traffic runtime failed; requesting all red")
            self.failure = "runtime_failure"
            self.recovery.fault(self.failure)
            await self._all_red()
            self.recovery.mark_safe()

    def _publish(self) -> None:
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(self.latest)

    async def stream(self) -> AsyncGenerator[Telemetry, None]:
        queue: asyncio.Queue[Telemetry] = asyncio.Queue(maxsize=1)
        self.subscribers.add(queue)
        queue.put_nowait(self.latest)
        try:
            while True:
                yield await queue.get()
        finally:
            self.subscribers.discard(queue)

    async def _all_red(self) -> None:
        signal = self.engine.safety.fail_safe(self.engine.simulator.now)
        self.latest = self.engine.snapshot()
        self._publish()
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
            logger.exception("All-red command failed; device watchdog must stop hardware")

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        if self._policy_task is not None:
            self._policy_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._policy_task
        await self._all_red()
        async with asyncio.timeout(self.settings.hardware_timeout_seconds):
            await self.hardware.close()
        await asyncio.to_thread(self.improvement_writer.close)
        self._policy_worker.close()

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import cv2
from traffic_core.models import (
    ObservationEvidence,
    ObservationStatus,
    Phase,
    SignalStage,
    SignalState,
    TrafficState,
)

from traffic_vision.analytics import TrafficAnalyzer
from traffic_vision.detector import Detector
from traffic_vision.health import PerceptionHealthMonitor
from traffic_vision.models import AnalysisSnapshot, Detection, DirectionMetrics, PipelineStatus
from traffic_vision.overlay import DebugOverlay
from traffic_vision.settings import VisionSettings
from traffic_vision.sources import Image, VideoFrame, VideoSource

logger = logging.getLogger(__name__)


class LatestSlot[T]:
    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._value: T | None = None
        self._closed = False
        self.dropped = 0

    def put(self, value: T) -> None:
        with self._condition:
            if self._closed:
                raise EOFError
            if self._value is not None:
                self.dropped += 1
            self._value = value
            self._condition.notify()

    def take(self) -> T:
        with self._condition:
            self._condition.wait_for(lambda: self._value is not None or self._closed)
            if self._value is None:
                raise EOFError
            result, self._value = self._value, None
            return result

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()


@dataclass(frozen=True)
class InferredFrame:
    video: VideoFrame
    detections: tuple[Detection, ...]


@dataclass(frozen=True)
class AnalyzedFrame:
    video: VideoFrame
    analysis: AnalysisSnapshot
    core_state: TrafficState

    @property
    def captured_at(self) -> float:
        return self.video.captured_at


@dataclass(frozen=True)
class PublishedFrame:
    analysis: AnalysisSnapshot
    core_state: TrafficState
    jpeg: bytes
    captured_at: float
    published_at: float
    raw_frame: Image


class VisionPipeline:
    """Capture -> inference -> analysis -> publishing, with bounded independent stages."""

    def __init__(
        self,
        source: VideoSource,
        detector: Detector,
        analyzer: TrafficAnalyzer,
        settings: VisionSettings,
        signal_provider: Callable[[], SignalState] | None = None,
    ) -> None:
        self.source, self.detector, self.analyzer, self.settings = (
            source,
            detector,
            analyzer,
            settings,
        )
        self.overlay = DebugOverlay(analyzer.geometry, detector.name)
        self.signal_provider = signal_provider or (
            lambda: SignalState(phase=Phase.NS, stage=SignalStage.ALL_RED, elapsed_seconds=0)
        )
        self._capture: LatestSlot[VideoFrame] = LatestSlot()
        self._inferred: LatestSlot[InferredFrame] = LatestSlot()
        self._analyzed: LatestSlot[AnalyzedFrame] = LatestSlot()
        self._stop = threading.Event()
        self._model_ready = threading.Event()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._latest: PublishedFrame | None = None
        self._latest_analysis: AnalyzedFrame | None = None
        self._latest_capture: VideoFrame | None = None
        self._state = "starting"
        self._error: str | None = None
        self._counts = [0, 0, 0, 0]
        self._stage_times: dict[str, deque[float]] = {
            key: deque(maxlen=120) for key in ("capture", "inference", "analytics")
        }
        self._stage_latency: dict[str, deque[float]] = {
            key: deque(maxlen=300) for key in ("inference", "analytics", "camera_to_analytics")
        }
        self._stale_drops = 0
        self._publication_times: deque[float] = deque(maxlen=30)
        self._latency_ms = 0.0
        self.health_monitor = PerceptionHealthMonitor(settings.health)
        self._heartbeats: dict[str, float] = {}
        self._heartbeat_timestamps: dict[str, float] = {}
        self._connected = False

    @property
    def latest(self) -> PublishedFrame | None:
        with self._lock:
            return self._latest

    @property
    def latest_capture(self) -> VideoFrame | None:
        with self._lock:
            return self._latest_capture

    @property
    def latest_analysis(self) -> AnalyzedFrame | None:
        with self._lock:
            return self._latest_analysis

    def status(self) -> PipelineStatus:
        with self._lock:
            times = self._publication_times
            fps = (
                (len(times) - 1) / (times[-1] - times[0])
                if len(times) > 1 and times[-1] > times[0]
                else 0
            )

            return PipelineStatus.model_validate(
                {
                    "state": self._state,
                    "captured": self._counts[0],
                    "inferred": self._counts[1],
                    "analyzed": self._counts[2],
                    "published": self._counts[3],
                    "dropped_capture": self._capture.dropped
                    + self._stale_drops
                    + getattr(self.source, "dropped_frames", 0),
                    "dropped_inference": self._inferred.dropped,
                    "dropped_analysis": self._analyzed.dropped,
                    "processing_fps": fps,
                    "latency_ms": self._latency_ms,
                    "error": self._error,
                }
            )

    def heartbeat_timestamps(self) -> dict[str, float]:
        with self._lock:
            return dict(self._heartbeat_timestamps)

    def performance(self) -> dict[str, object]:
        import numpy as np

        with self._lock:
            now = time.monotonic()
            rates = {}
            for key, stamps in self._stage_times.items():
                recent = [stamp for stamp in stamps if now - stamp <= 5]
                rates[key + "_fps"] = (
                    (len(recent) - 1) / (recent[-1] - recent[0]) if len(recent) > 1 else 0.0
                )
            latency = {
                key: {"p50": float(np.median(values)), "p95": float(np.percentile(values, 95))}
                if values
                else None
                for key, values in self._stage_latency.items()
            }
        return {"rates": rates, "latency_ms": latency}

    def observation(self, now: float | None = None) -> AnalysisSnapshot | None:
        """Refresh evidence even when workers no longer produce frames."""
        latest = self.latest_analysis
        if latest is None:
            return None
        now = time.monotonic() if now is None else now
        status = self.status().state
        with self._lock:
            beats = dict(self._heartbeats)
        age = max(0, now - latest.captured_at)
        directions = {}
        for key, metric in latest.analysis.traffic.directions.items():
            evidence = metric.observation
            checks = dict(evidence.camera_health)
            checks.update(
                {
                    "camera_connected": self._connected and self.source.connected,
                    "frame_fresh": age
                    <= min(self.settings.stale_seconds, self.settings.health.stale_seconds),
                    "inference_alive": now - beats.get("inference", float("-inf"))
                    <= self.settings.health.stage_timeout_seconds,
                    "analytics_alive": now - beats.get("analytics", float("-inf"))
                    <= self.settings.health.stage_timeout_seconds,
                }
            )
            reasons = list(evidence.failure_reasons)
            state = evidence.observation_status
            alignment_stale = self.analyzer.geometry.camera_calibration is not None and age > 0.5
            if alignment_stale:
                checks["calibration_fresh"] = False
                reasons.append("alignment_evidence_stale")
                state = ObservationStatus.DEGRADED
            if not checks["frame_fresh"]:
                state = ObservationStatus.STALE
                reasons.append("frame_stale")
            if status in ("failed", "ended", "stopped") or not checks["camera_connected"]:
                state = ObservationStatus.FAILED
                reasons.append("camera_or_pipeline_stopped")
            if not checks["inference_alive"] or not checks["analytics_alive"]:
                state = ObservationStatus.FAILED
                reasons.append("perception_stage_timeout")
            refreshed = ObservationEvidence.model_validate(
                {
                    **evidence.model_dump(),
                    "observation_status": state,
                    "calibration_status": "suspect"
                    if alignment_stale
                    else evidence.calibration_status,
                    "measurement_age_ms": age * 1000,
                    "camera_health": checks,
                    "failure_reasons": tuple(dict.fromkeys(reasons)),
                    "measurement_confidence": evidence.measurement_confidence if not reasons else 0,
                    "uncertainty": evidence.uncertainty.model_copy(
                        update={
                            "observation_confidence": evidence.uncertainty.observation_confidence
                            if not reasons
                            else 0
                        }
                    ),
                }
            )
            directions[key] = DirectionMetrics.model_validate(
                {**metric.model_dump(), "observation": refreshed}
            )
        estimation = latest.analysis.traffic.estimation
        if estimation is not None and any(not m.observation.reliable for m in directions.values()):
            estimation = estimation.unavailable(
                "observation_unreliable",
                calibration_status="SUSPECT"
                if age > 0.5 and self.analyzer.geometry.camera_calibration is not None
                else None,
            )
        return latest.analysis.model_copy(
            update={
                "traffic": latest.analysis.traffic.model_copy(
                    update={"directions": directions, "estimation": estimation}
                )
            }
        )

    def start(self) -> None:
        if self._threads:
            raise RuntimeError("A pipeline instance can only start once")
        stages = (self._capture_loop, self._inference_loop, self._analysis_loop, self._publish_loop)
        for stage in stages:
            thread = threading.Thread(
                target=self._guard, args=(stage,), name=stage.__name__, daemon=True
            )
            self._threads.append(thread)
            thread.start()

    def _guard(self, stage: Callable[[], None]) -> None:
        try:
            stage()
        except EOFError:
            pass
        except Exception:
            logger.exception("Vision stage failed: %s", stage.__name__)
            if stage == self._publish_loop:
                return  # Preview failures never cancel measurement or control workers.
            with self._lock:
                self._state = "failed"
                self._error = f"{stage.__name__} failed; see backend log"
            self._cancel()

    def _capture_loop(self) -> None:
        try:
            while self.settings.source not in (
                "builtin_camera",
                "usb_camera",
            ) and not self._model_ready.wait(0.1):
                if self._stop.is_set():
                    return
            if self._stop.is_set():
                return
            self.source.open()
            self._connected = True
            start = time.monotonic()
            media_start: float | None = None
            while not self._stop.is_set():
                frame = self.source.read()
                if frame is None:
                    break
                if media_start is None:
                    media_start = frame.timestamp
                if self.source.paced and self._stop.wait(
                    max(0, frame.timestamp - media_start - (time.monotonic() - start))
                ):
                    break
                with self._lock:
                    self._counts[0] += 1
                    self._stage_times["capture"].append(time.monotonic())
                    self._heartbeats["capture"] = time.monotonic()
                    self._heartbeat_timestamps["frame"] = time.time()
                    self._latest_capture = frame
                self._capture.put(frame)
        finally:
            self._connected = False
            self.source.close()
            self._capture.close()

    def _inference_loop(self) -> None:
        try:
            self.detector.open()
            self._model_ready.set()
            next_inference = 0.0
            while not self._stop.is_set():
                if self.settings.inference_hz is not None and self._stop.wait(
                    max(0, next_inference - time.monotonic())
                ):
                    break
                frame = self._capture.take()
                if time.monotonic() - frame.captured_at > self.settings.max_frame_age_seconds:
                    with self._lock:
                        self._stale_drops += 1
                    continue
                stage_started = time.monotonic()
                detections = self.detector.process(frame)
                if self.settings.inference_hz is not None:
                    next_inference = time.monotonic() + 1 / self.settings.inference_hz
                with self._lock:
                    self._stage_latency["inference"].append(
                        (time.monotonic() - stage_started) * 1000
                    )
                    self._stage_times["inference"].append(time.monotonic())
                    self._counts[1] += 1
                    self._heartbeats["inference"] = time.monotonic()
                    self._heartbeat_timestamps["inference"] = time.time()
                self._inferred.put(InferredFrame(frame, detections))
        finally:
            self.detector.close()
            self._inferred.close()

    def _analysis_loop(self) -> None:
        try:
            while not self._stop.is_set():
                result = self._inferred.take()
                frame = result.video
                height, width = frame.frame.shape[:2]
                stage_started = time.monotonic()
                analysis = self.analyzer.process(
                    result.detections,
                    source_id=frame.source_id,
                    timestamp=frame.timestamp,
                    frame_id=frame.frame_id,
                    width=width,
                    height=height,
                    stream_epoch=frame.stream_epoch,
                    health=self.health_monitor.inspect(
                        frame, self.analyzer.geometry, time.monotonic()
                    ),
                )
                analyzed = AnalyzedFrame(frame, analysis, self.analyzer.to_core_state(analysis))
                with self._lock:
                    self._stage_latency["analytics"].append(
                        (time.monotonic() - stage_started) * 1000
                    )
                    self._stage_latency["camera_to_analytics"].append(
                        (time.monotonic() - frame.captured_at) * 1000
                    )
                    self._stage_times["analytics"].append(time.monotonic())
                    self._latest_analysis = analyzed
                    self._state = "running"
                    self._counts[2] += 1
                    self._heartbeats["analytics"] = time.monotonic()
                    self._heartbeat_timestamps["analytics_update"] = time.time()
                self._analyzed.put(analyzed)
        finally:
            self._analyzed.close()
            with self._lock:
                if self._state != "failed":
                    self._state = "stopped" if self._stop.is_set() else "ended"

    def _publish_loop(self) -> None:
        try:
            while not self._stop.is_set():
                result = self._analyzed.take()
                with self._lock:
                    self._latency_ms = (time.monotonic() - result.video.captured_at) * 1000
                image: Image = self.overlay.render(
                    result.video.frame, result.analysis, self.signal_provider(), self.status()
                )
                ok, encoded = cv2.imencode(
                    ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self.settings.jpeg_quality]
                )
                if not ok:
                    raise RuntimeError("JPEG encoding failed")
                now = time.monotonic()
                if self._stop.is_set():
                    break
                published = PublishedFrame(
                    result.analysis,
                    result.core_state,
                    encoded.tobytes(),
                    result.video.captured_at,
                    now,
                    result.video.frame,
                )
                with self._lock:
                    if self._stop.is_set():
                        break
                    self._latest = published
                    self._counts[3] += 1
                    self._publication_times.append(now)
        finally:
            pass

    def _cancel(self) -> None:
        self._stop.set()
        self.source.cancel()
        self._capture.close()
        self._inferred.close()
        self._analyzed.close()

    def terminal_frame(self, signals: SignalState) -> None:
        """Retain an all-red terminal image instead of displaying the last green after EOF."""
        self._cancel()
        last = self.latest
        if last is None:
            return
        status = self.status().model_copy(update={"state": "stopped"})
        image = self.overlay.render(last.raw_frame, last.analysis, signals, status)
        ok, encoded = cv2.imencode(
            ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, self.settings.jpeg_quality]
        )
        if not ok:
            raise RuntimeError("Terminal JPEG encoding failed")
        with self._lock:
            self._latest = PublishedFrame(
                last.analysis,
                last.core_state,
                encoded.tobytes(),
                last.captured_at,
                time.monotonic(),
                last.raw_frame,
            )

    def stop(self) -> None:
        self._cancel()
        deadline = time.monotonic() + self.settings.shutdown_timeout_seconds
        for thread in self._threads:
            thread.join(timeout=max(0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in self._threads):
            with self._lock:
                self._state = "failed"
                self._error = "Native capture/inference did not stop within the shutdown deadline"
            raise TimeoutError(self._error)
        with self._lock:
            if self._state not in ("failed", "ended"):
                self._state = "stopped"

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

import cv2
from traffic_core.models import Phase, SignalStage, SignalState, TrafficState

from traffic_vision.analytics import TrafficAnalyzer
from traffic_vision.detector import Detector
from traffic_vision.models import AnalysisSnapshot, Detection, PipelineStatus
from traffic_vision.overlay import DebugOverlay
from traffic_vision.settings import VisionSettings
from traffic_vision.sources import Image, VideoFrame, VideoSource

logger = logging.getLogger(__name__)


class LatestSlot[T]:
    """Capacity one, replacing old work. Closing drains the final item before EOF."""

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
        self._state = "starting"
        self._error: str | None = None
        self._counts = [0, 0, 0, 0]
        self._stale_drops = 0
        self._publication_times: deque[float] = deque(maxlen=30)
        self._latency_ms = 0.0

    @property
    def latest(self) -> PublishedFrame | None:
        with self._lock:
            return self._latest

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
                    "dropped_capture": self._capture.dropped + self._stale_drops,
                    "dropped_inference": self._inferred.dropped,
                    "dropped_analysis": self._analyzed.dropped,
                    "processing_fps": fps,
                    "latency_ms": self._latency_ms,
                    "error": self._error,
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
            with self._lock:
                self._state = "failed"
                self._error = f"{stage.__name__} failed; see backend log"
            self._cancel()

    def _capture_loop(self) -> None:
        try:
            while not self._model_ready.wait(0.1):
                if self._stop.is_set():
                    return
            if self._stop.is_set():
                return
            self.source.open()
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
                self._capture.put(frame)
        finally:
            self.source.close()
            self._capture.close()

    def _inference_loop(self) -> None:
        try:
            self.detector.open()
            self._model_ready.set()
            while not self._stop.is_set():
                frame = self._capture.take()
                if time.monotonic() - frame.captured_at > self.settings.max_frame_age_seconds:
                    with self._lock:
                        self._stale_drops += 1
                    continue
                detections = self.detector.process(frame)
                with self._lock:
                    self._counts[1] += 1
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
                analysis = self.analyzer.process(
                    result.detections,
                    source_id=frame.source_id,
                    timestamp=frame.timestamp,
                    frame_id=frame.frame_id,
                    width=width,
                    height=height,
                    stream_epoch=frame.stream_epoch,
                )
                with self._lock:
                    self._counts[2] += 1
                self._analyzed.put(
                    AnalyzedFrame(frame, analysis, self.analyzer.to_core_state(analysis))
                )
        finally:
            self._analyzed.close()

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
                    self._state = "running"
                    self._counts[3] += 1
                    self._publication_times.append(now)
        finally:
            with self._lock:
                if self._state != "failed":
                    self._state = "stopped" if self._stop.is_set() else "ended"

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

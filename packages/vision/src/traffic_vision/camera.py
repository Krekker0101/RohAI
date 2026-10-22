"""First-class local live camera; native driver runs in a terminable process."""

import multiprocessing as mp
import time
from multiprocessing import shared_memory
from typing import Any

import cv2
import numpy as np

from traffic_vision.settings import VisionSettings
from traffic_vision.sources import Image, VideoFrame, VideoSource

MAX_BYTES = 3840 * 2160 * 3
BACKENDS = {"DSHOW": cv2.CAP_DSHOW, "MSMF": cv2.CAP_MSMF, "ANY": cv2.CAP_ANY}


def transform_frame(image: Image, settings: VisionSettings) -> Image:
    h, w = image.shape[:2]
    x, y, cw, ch = settings.camera_crop
    image = image[
        int(y * h) : max(int(y * h) + 1, int((y + ch) * h)),
        int(x * w) : max(int(x * w) + 1, int((x + cw) * w)),
    ]
    if settings.camera_rotation:
        image = np.asarray(
            cv2.rotate(
                image,
                {
                    90: cv2.ROTATE_90_CLOCKWISE,
                    180: cv2.ROTATE_180,
                    270: cv2.ROTATE_90_COUNTERCLOCKWISE,
                }[settings.camera_rotation],
            ),
            dtype=np.uint8,
        )
    if settings.camera_mirror:
        image = np.asarray(cv2.flip(image, 1), dtype=np.uint8)
    return np.ascontiguousarray(image, dtype=np.uint8)


def error_code(error: OSError) -> str:
    code = getattr(error, "winerror", None) or error.errno
    if code in (5, 13):
        return "CAMERA_PERMISSION_DENIED"
    if code in (16, 32):
        return "CAMERA_IN_USE"
    return "CAMERA_OPEN_FAILED"


class TransformedSource(VideoSource):
    """RTSP/replay/legacy source transforms share the same coordinate convention."""

    def __init__(self, source: VideoSource, settings: VisionSettings) -> None:
        super().__init__(source.source_id)
        self.source, self.settings = source, settings
        self.paced = source.paced

    def open(self) -> None:
        self.source.open()
        self.connected, self.fps = self.source.connected, self.source.fps

    def read(self) -> VideoFrame | None:
        try:
            frame = self.source.read()
        finally:
            self.connected = self.source.connected
        if frame is None:
            return None
        return VideoFrame(
            transform_frame(frame.frame, self.settings),
            frame.timestamp,
            frame.frame_id,
            frame.source_id,
            frame.captured_at,
            frame.stream_epoch,
        )

    def cancel(self) -> None:
        super().cancel()
        self.source.cancel()

    def close(self) -> None:
        self.source.close()
        self.connected = False


def _capture_worker(
    settings_json: str, name: str, header: Any, lock: Any, ready: Any, stop: Any, errors: Any
) -> None:
    settings = VisionSettings.model_validate_json(settings_json)
    memory = shared_memory.SharedMemory(name=name)
    capture = None
    try:
        # DirectShow drivers can retain a camera briefly after the bounded
        # discovery process releases it. Retry the actual capture process so
        # a successful probe reliably becomes a live stream.
        for attempt in range(settings.reconnect_attempts + 1):
            candidate = cv2.VideoCapture(settings.webcam_index, BACKENDS[settings.camera_backend])
            if candidate.isOpened():
                capture = candidate
                break
            candidate.release()
            if attempt < settings.reconnect_attempts:
                stop.wait(settings.reconnect_delay_seconds)
        if capture is None:
            errors.put_nowait("CAMERA_OPEN_FAILED")
            return
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, settings.camera_width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, settings.camera_height)
        capture.set(cv2.CAP_PROP_FPS, settings.camera_fps)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        empty_reads = 0
        while not stop.is_set():
            ok, image = capture.read()
            if not ok or image is None or image.ndim != 3 or image.shape[2] != 3 or not image.size:
                empty_reads += 1
                if empty_reads <= settings.reconnect_attempts and not stop.wait(
                    settings.reconnect_delay_seconds
                ):
                    continue
                errors.put_nowait("FRAME_TIMEOUT")
                return
            empty_reads = 0
            stamp = time.monotonic_ns()
            image = transform_frame(np.asarray(image, dtype=np.uint8), settings)
            if image.nbytes > MAX_BYTES:
                errors.put_nowait("CAMERA_RESOLUTION_UNSUPPORTED")
                return
            h, w = image.shape[:2]
            with lock:
                np.ndarray(image.shape, dtype=np.uint8, buffer=memory.buf)[:] = image
                header[1], header[2], header[3] = h, w, stamp
                header[0] += 1
            ready.set()
    except KeyboardInterrupt:
        # Windows sends console Ctrl+C to spawned workers as well as the server.
        # Exit through cleanup without multiprocessing printing a traceback.
        stop.set()
        errors.put_nowait("CAMERA_CAPTURE_INTERRUPTED")
    except OSError as error:
        errors.put_nowait(error_code(error))
    except Exception:
        errors.put_nowait("CAMERA_OPEN_FAILED")
    finally:
        ready.set()
        if capture is not None:
            capture.release()
        memory.close()


class LocalCameraSource(VideoSource):
    def __init__(self, settings: VisionSettings) -> None:
        super().__init__(settings.source_id)
        self.settings = settings
        self.backend = settings.camera_backend
        self.failure_code: str | None = None
        self._context = mp.get_context("spawn")
        self._memory: shared_memory.SharedMemory | None = None
        self._process: Any = None
        self._header: Any = self._context.Array("q", 4, lock=False)
        self._lock: Any = self._context.Lock()
        self._ready: Any = self._context.Event()
        self._stop: Any = self._context.Event()
        self._errors: Any = self._context.Queue(maxsize=1)
        self._sequence = 0
        self.dropped_frames = 0
        self._started = 0.0
        self.stream_epoch = -1

    def _fail(self, default: str) -> None:
        from queue import Empty

        try:
            self.failure_code = str(self._errors.get(timeout=0.05))
        except Empty:
            self.failure_code = default
        self.connected = False
        raise OSError(self.failure_code)

    def open(self) -> None:
        if self._process is not None:
            raise RuntimeError("Camera capture already open")
        if self.backend == "AUTO":
            raise ValueError("Local camera backend must be selected by bounded discovery")
        # Recreate primitives after termination: a dead driver can leave an acquired lock.
        self._errors.close()
        self._errors.join_thread()
        self._header = self._context.Array("q", 4, lock=False)
        self._lock = self._context.Lock()
        self._ready = self._context.Event()
        self._stop = self._context.Event()
        self._errors = self._context.Queue(maxsize=1)
        self._sequence = 0
        self.failure_code = None
        self.stream_epoch += 1
        self._memory = shared_memory.SharedMemory(create=True, size=MAX_BYTES)
        self._process = self._context.Process(
            target=_capture_worker,
            args=(
                self.settings.model_dump_json(),
                self._memory.name,
                self._header,
                self._lock,
                self._ready,
                self._stop,
                self._errors,
            ),
            daemon=True,
        )
        self._started = time.monotonic()
        try:
            self._process.start()
            deadline = time.monotonic() + 5
            while not self._ready.is_set() and not self.cancelled.is_set():
                if time.monotonic() >= deadline:
                    break
                self._ready.wait(0.05)
            if self.cancelled.is_set():
                self._fail("CAMERA_CAPTURE_INTERRUPTED")
            if not self._ready.is_set() or self._header[0] == 0:
                self._fail("FRAME_TIMEOUT")
            self.connected = True
        except Exception:
            self.close()
            raise

    def read(self) -> VideoFrame | None:
        deadline = time.monotonic() + self.settings.capture_timeout_ms / 1000
        while not self.cancelled.is_set():
            if self._header[0] != self._sequence:
                assert self._memory is not None
                if not self._lock.acquire(timeout=0.1):
                    self._fail("FRAME_TIMEOUT")
                try:
                    sequence, h, w, stamp = self._header[:]
                    image = np.ndarray((h, w, 3), dtype=np.uint8, buffer=self._memory.buf).copy()
                finally:
                    self._lock.release()
                self.dropped_frames += max(0, sequence - self._sequence - 1)
                self._sequence = sequence
                captured = stamp / 1e9
                return VideoFrame(
                    image,
                    captured - self._started,
                    sequence,
                    self.source_id,
                    captured,
                    self.stream_epoch,
                )
            if time.monotonic() >= deadline or not self._process.is_alive():
                self._fail("FRAME_TIMEOUT")
            self.cancelled.wait(0.005)
        return None

    def close(self) -> None:
        self.connected = False
        self._stop.set()
        if self._process is not None:
            if self._process.pid is not None:
                self._process.join(timeout=0.3)
                if self._process.is_alive():
                    self._process.terminate()
                    self._process.join(timeout=1)
            self._process.close()
            self._process = None
        if self._memory is not None:
            self._memory.close()
            self._memory.unlink()
            self._memory = None
        self._errors.close()
        self._errors.join_thread()

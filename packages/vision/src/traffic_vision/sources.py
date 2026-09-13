import math
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from numpy.typing import NDArray

from traffic_vision.settings import VisionSettings

Image = NDArray[np.uint8]


@dataclass(frozen=True)
class VideoFrame:
    frame: Image
    timestamp: float
    frame_id: int
    source_id: str
    captured_at: float
    stream_epoch: int = 0


class VideoSource(ABC):
    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        self.paced = False
        self.fps = 30.0
        self.cancelled = threading.Event()

    def cancel(self) -> None:
        self.cancelled.set()

    @abstractmethod
    def open(self) -> None: ...

    @abstractmethod
    def read(self) -> VideoFrame | None: ...

    @abstractmethod
    def close(self) -> None: ...


class OpenCVSource(VideoSource):
    def __init__(self, source_id: str, location: str | int) -> None:
        super().__init__(source_id)
        self._location = location
        self._capture: cv2.VideoCapture | None = None
        self._started = 0.0
        self._frame_id = 0
        self._epoch = 0
        self._last_timestamp = -1.0

    def _open_capture(self) -> cv2.VideoCapture:
        return cv2.VideoCapture(self._location)

    def open(self) -> None:
        self._capture = self._open_capture()
        if not self._capture.isOpened():
            self.close()
            raise OSError(f"Could not open video source '{self.source_id}'")
        fps = float(self._capture.get(cv2.CAP_PROP_FPS))
        self.fps = fps if math.isfinite(fps) and fps > 0 else 30.0
        self._started = time.monotonic()
        self._capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def read(self) -> VideoFrame | None:
        if self._capture is None:
            raise RuntimeError("Source is not open")
        ok, image = self._capture.read()
        if not ok:
            return self._on_read_failure()
        captured = time.monotonic()
        if self.paced:
            stamp = float(self._capture.get(cv2.CAP_PROP_POS_MSEC)) / 1000
            if not math.isfinite(stamp) or stamp <= self._last_timestamp:
                stamp = self._frame_id / self.fps
        else:
            stamp = captured - self._started
        self._last_timestamp = stamp
        frame = VideoFrame(
            np.asarray(image, dtype=np.uint8),
            stamp,
            self._frame_id,
            self.source_id,
            captured,
            self._epoch,
        )
        self._frame_id += 1
        return frame

    def _on_read_failure(self) -> VideoFrame | None:
        raise OSError(f"Video source '{self.source_id}' disconnected")

    def close(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None


class WebcamSource(OpenCVSource):
    def __init__(self, index: int = 0, source_id: str = "webcam") -> None:
        super().__init__(source_id, index)


class VideoFileSource(OpenCVSource):
    def __init__(self, path: Path, source_id: str = "video") -> None:
        super().__init__(source_id, str(path))
        self.paced = True

    def _on_read_failure(self) -> None:
        # A premature decode failure is distinguished from a normal end where metadata exists.
        assert self._capture is not None
        length = self._capture.get(cv2.CAP_PROP_FRAME_COUNT)
        if length > 0 and self._frame_id + 1 < length:
            raise OSError(f"Decode failure before EOF in source '{self.source_id}'")
        return None


class RTSPSource(OpenCVSource):
    def __init__(
        self,
        uri: str,
        source_id: str,
        timeout_ms: int = 1500,
        retries: int = 3,
        reconnect_delay: float = 0.5,
    ) -> None:
        super().__init__(source_id, uri)
        self.timeout_ms = timeout_ms
        self.retries = retries
        self.reconnect_delay = reconnect_delay

    def _open_capture(self) -> cv2.VideoCapture:
        return cv2.VideoCapture(
            str(self._location),
            cv2.CAP_FFMPEG,
            [
                cv2.CAP_PROP_OPEN_TIMEOUT_MSEC,
                self.timeout_ms,
                cv2.CAP_PROP_READ_TIMEOUT_MSEC,
                self.timeout_ms,
            ],
        )

    def _on_read_failure(self) -> VideoFrame | None:
        for _ in range(self.retries):
            if self.cancelled.wait(self.reconnect_delay):
                return None
            self.close()
            self._capture = self._open_capture()
            if not self._capture.isOpened():
                continue
            ok, image = self._capture.read()
            if ok:
                self._epoch += 1
                captured = time.monotonic()
                frame = VideoFrame(
                    np.asarray(image, dtype=np.uint8),
                    captured - self._started,
                    self._frame_id,
                    self.source_id,
                    captured,
                    self._epoch,
                )
                self._frame_id += 1
                return frame
        raise OSError(f"RTSP source '{self.source_id}' exhausted reconnect attempts")


# Test scene palette: BGR, kept separate from YOLO and never used on natural footage.
SYNTHETIC_COLORS = ((70, 220, 90), (220, 140, 40), (200, 70, 220), (50, 200, 230))


class SyntheticSource(VideoSource):
    def __init__(self, source_id: str = "demo", frames: int = 150, fps: float = 15) -> None:
        super().__init__(source_id)
        self.total_frames = frames
        self.fps = fps
        self.paced = True
        self._index = 0
        self._opened = False

    def open(self) -> None:
        self._index = 0
        self._opened = True

    def read(self) -> VideoFrame | None:
        if not self._opened:
            raise RuntimeError("Source is not open")
        if self._index >= self.total_frames:
            return None
        image = np.full((720, 1280, 3), 22, dtype=np.uint8)
        cv2.rectangle(image, (470, 0), (810, 720), (45, 49, 55), -1)
        cv2.rectangle(image, (0, 250), (1280, 470), (45, 49, 55), -1)
        for offset in range(0, 1280, 70):
            cv2.line(image, (offset, 360), (offset + 30, 360), (100, 100, 100), 2)
        seconds = self._index / self.fps
        # Approach, stop for a visible waiting interval, then depart.
        shift = min(seconds, 2) * 35 + max(0, seconds - 7) * 60
        boxes = (
            (560, 40 + shift, 44, 64),
            (690, 620 - shift, 44, 64),
            (1080 - shift, 290, 70, 40),
            (80 + shift, 400, 70, 40),
        )
        for color, (x, y, w, h) in zip(SYNTHETIC_COLORS, boxes, strict=True):
            cv2.rectangle(image, (int(x), int(y)), (int(x + w), int(y + h)), color, -1)
            cv2.rectangle(
                image, (int(x + 7), int(y + 8)), (int(x + w - 7), int(y + 18)), (35, 40, 45), -1
            )
        frame = VideoFrame(image, seconds, self._index, self.source_id, time.monotonic())
        self._index += 1
        return frame

    def close(self) -> None:
        self._opened = False


def create_source(settings: VisionSettings) -> VideoSource:
    if settings.source == "file":
        return VideoFileSource(Path(settings.uri), settings.source_id)
    if settings.source == "webcam":
        return WebcamSource(settings.webcam_index, settings.source_id)
    if settings.source == "rtsp":
        return RTSPSource(
            settings.uri,
            settings.source_id,
            settings.capture_timeout_ms,
            settings.reconnect_attempts,
            settings.reconnect_delay_seconds,
        )
    return SyntheticSource(settings.source_id, settings.synthetic_frames, settings.synthetic_fps)

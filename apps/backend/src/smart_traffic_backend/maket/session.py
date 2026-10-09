"""Bounded recording consumer; video/write work never runs on the control loop."""

import hashlib
import json
import queue
import threading
import time
import uuid
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import cv2

from smart_traffic_backend.maket.config import MaketConfig


class SessionRecorder:
    def __init__(self, config: MaketConfig) -> None:
        self.config = config
        self.id = uuid.uuid4().hex
        self.path = config.session_directory / self.id
        config.session_directory.mkdir(parents=True, exist_ok=True)
        sessions = [
            p
            for p in config.session_directory.iterdir()
            if p.is_dir() and len(p.name) == 32 and all(c in "0123456789abcdef" for c in p.name)
        ]
        self.enabled = len(sessions) < config.max_sessions
        if self.enabled:
            self.path.mkdir(parents=True, exist_ok=False)
        recorded_config = config.model_dump(mode="json")
        if config.camera.source == "rtsp":
            parsed = urlsplit(config.camera.uri)
            host = parsed.hostname or "redacted"
            if ":" in host:
                host = f"[{host}]"
            if parsed.port:
                host += f":{parsed.port}"
            recorded_config["camera"]["uri"] = urlunsplit(
                (parsed.scheme, host, parsed.path, "", "")
            )
        manifest = json.dumps(
            {
                "id": self.id,
                "schema_version": 2,
                "mode": "PHYSICAL_MAKET"
                if config.output_mode == "PHYSICAL_OUTPUT"
                else "REAL_VISION",
                "created_at": time.time(),
                "config": recorded_config,
                "configuration_sha256": hashlib.sha256(
                    json.dumps(recorded_config, sort_keys=True).encode()
                ).hexdigest(),
                "camera_id": config.camera.source_id,
                "privacy": {
                    "video_default": False,
                    "retention_days": config.retention_days,
                    "storage": "LOCAL_ONLY",
                    "automatic_deletion": False,
                },
                "ground_truth": False,
            }
        )
        if self.enabled:
            (self.path / "session.json").write_text(manifest, encoding="utf-8")
        self.queue: queue.Queue[tuple[int, float, float, str, Any]] = queue.Queue(maxsize=32)
        self._sequence = 0
        self._sequence_lock = threading.Lock()
        self.video_frames = 0
        self.events_written = 0
        self.stop_event = threading.Event()
        self.dropped = 0
        self.error: str | None = None if self.enabled else "SESSION_STORAGE_FULL"
        self.record_video = False
        self.started = time.monotonic()
        self.thread = threading.Thread(
            target=self._run if self.enabled else lambda: None, daemon=True, name="maket-recording"
        )
        self.thread.start()

    def submit(self, kind: str, payload: Any) -> None:
        with self._sequence_lock:
            self._sequence += 1
            if not self.enabled or self.error is not None:
                self.dropped += 1
                return
            try:
                self.queue.put_nowait(
                    (self._sequence, time.time(), time.monotonic(), kind, payload)
                )
            except queue.Full:
                self.dropped += 1

    def submit_frame(self, frame: Any, metadata: dict[str, Any]) -> None:
        if not self.record_video:
            return
        if self.queue.qsize() >= 2:
            with self._sequence_lock:
                self._sequence += 1
                self.dropped += 1
            return
        self.submit("frame", {"image": frame, "metadata": metadata})

    def _run(self) -> None:
        writer = None
        video_size: tuple[int, int] | None = None
        try:
            with (self.path / "events.jsonl").open("a", encoding="utf-8") as events:
                while not self.stop_event.is_set() or not self.queue.empty():
                    try:
                        sequence, timestamp, monotonic, kind, payload = self.queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    size = sum(p.stat().st_size for p in self.path.iterdir() if p.is_file())
                    if size >= self.config.max_session_bytes or (
                        time.monotonic() - self.started > self.config.max_recording_seconds
                    ):
                        self.error = "retention_limit_recording_stopped"
                        self.record_video = False
                        self.dropped += 1
                        continue
                    if kind == "frame":
                        if writer is None:
                            h, w = payload["image"].shape[:2]
                            video_size = (w, h)
                            writer = cv2.VideoWriter(
                                str(self.path / "video.avi"),
                                cv2.VideoWriter.fourcc(*"MJPG"),
                                self.config.preview_hz,
                                (w, h),
                            )
                            if not writer.isOpened():
                                raise OSError("Video writer unavailable")
                        h, w = payload["image"].shape[:2]
                        if video_size != (w, h):
                            self.error = "video_resolution_changed_recording_stopped"
                            self.record_video = False
                            self.dropped += 1
                            continue
                        writer.write(payload["image"])
                        payload = {**payload["metadata"], "recorded_video_index": self.video_frames}
                        self.video_frames += 1
                        kind = "recorded_frame"
                    events.write(
                        json.dumps(
                            {
                                "sequence": sequence,
                                "timestamp": timestamp,
                                "monotonic": monotonic,
                                "kind": kind,
                                "payload": payload,
                            }
                        )
                        + "\n"
                    )
                    self.events_written += 1
                    events.flush()
        except Exception as error:
            self.error = str(error)
        finally:
            if writer is not None:
                writer.release()

    def close(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)
        if not self.enabled:
            return
        if self.thread.is_alive():
            self.error = "recording_shutdown_timeout"
            raise TimeoutError(self.error)
        manifest_path = self.path / "session.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(
            closed_at=time.time(),
            dropped_events=self.dropped,
            events_written=self.events_written,
            video_frames=self.video_frames,
            error=self.error,
            complete=self.error is None and self.dropped == 0,
        )
        from smart_traffic_backend.maket.calibration import atomic_write

        atomic_write(manifest_path, json.dumps(manifest, indent=2).encode())

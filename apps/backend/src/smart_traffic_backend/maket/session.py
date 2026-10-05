"""Bounded recording consumer; video/write work never runs on the control loop."""

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
        self.path.mkdir(parents=True, exist_ok=False)
        sessions = sorted(config.session_directory.iterdir(), key=lambda p: p.stat().st_mtime)
        # No recursive deletion: only own flat UUID session files with explicit names.
        for directory in sessions[: -config.max_sessions]:
            if (
                directory.is_dir()
                and not directory.is_symlink()
                and directory.resolve().is_relative_to(config.session_directory.resolve())
                and len(directory.name) == 32
                and all(c in "0123456789abcdef" for c in directory.name)
            ):
                for name in ("events.jsonl", "video.avi", "session.json"):
                    (directory / name).unlink(missing_ok=True)
                if not any(directory.iterdir()):
                    directory.rmdir()
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
        (self.path / "session.json").write_text(
            json.dumps(
                {
                    "id": self.id,
                    "mode": "PHYSICAL_MAKET",
                    "created_at": time.time(),
                    "config": recorded_config,
                    "ground_truth": False,
                }
            ),
            encoding="utf-8",
        )
        self.queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=32)
        self.stop_event = threading.Event()
        self.dropped = 0
        self.error: str | None = None
        self.record_video = False
        self.started = time.monotonic()
        self.thread = threading.Thread(target=self._run, daemon=True, name="maket-recording")
        self.thread.start()

    def submit(self, kind: str, payload: Any) -> None:
        try:
            self.queue.put_nowait((kind, payload))
        except queue.Full:
            self.dropped += 1

    def _run(self) -> None:
        writer = None
        try:
            with (self.path / "events.jsonl").open("a", encoding="utf-8") as events:
                while not self.stop_event.is_set() or not self.queue.empty():
                    try:
                        kind, payload = self.queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    size = sum(p.stat().st_size for p in self.path.iterdir() if p.is_file())
                    if size >= self.config.max_session_bytes or (
                        time.monotonic() - self.started > self.config.max_recording_seconds
                    ):
                        self.error = "retention_limit_recording_stopped"
                        self.dropped += 1
                        continue
                    if kind == "frame":
                        if not self.record_video:
                            continue
                        if writer is None:
                            h, w = payload.shape[:2]
                            writer = cv2.VideoWriter(
                                str(self.path / "video.avi"),
                                cv2.VideoWriter.fourcc(*"MJPG"),
                                self.config.preview_hz,
                                (w, h),
                            )
                            if not writer.isOpened():
                                raise OSError("Video writer unavailable")
                        writer.write(payload)
                    else:
                        events.write(
                            json.dumps({"timestamp": time.time(), "kind": kind, "payload": payload})
                            + "\n"
                        )
                        events.flush()
        except Exception as error:
            self.error = str(error)
        finally:
            if writer is not None:
                writer.release()

    def close(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)

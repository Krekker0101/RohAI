import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from smart_traffic_backend.config import Settings
from smart_traffic_backend.main import create_app
from smart_traffic_backend.video_analysis import (
    VideoJobs,
    publish_preview,
    replace_with_retry,
    write_json,
)
from smart_traffic_backend.video_worker import analyze, run_video_job
from traffic_core.models import ObjectKind
from traffic_vision.contracts import BoundingBox
from traffic_vision.models import Detection
from traffic_vision.sources import VideoFrame


def settings(tmp_path: Path, **changes: Any) -> Settings:
    return Settings(
        video_jobs_directory=tmp_path / "jobs",
        operator_token=SecretStr("video-test-secret-1234"),
        improvement_log_path=tmp_path / "decisions.sqlite3",
        ai_artifact_directory=tmp_path / "ai",
        **changes,
    )


def video(path: Path, frames: int = 20) -> None:
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter.fourcc(*"MJPG"), 8, (96, 64))
    assert writer.isOpened()
    try:
        for index in range(frames):
            image = np.full((64, 96, 3), 80, dtype=np.uint8)
            cv2.rectangle(image, (20 + index, 20), (40 + index, 40), (220, 40, 40), -1)
            writer.write(image)
    finally:
        writer.release()


class FixtureDetector:
    name = "SOFTWARE_FIXTURE_NOT_REAL_AI"

    def __init__(self) -> None:
        self.frames: list[int] = []
        self.closed = False

    def open(self) -> None:
        pass

    def process(self, frame: VideoFrame) -> tuple[Detection, ...]:
        self.frames.append(frame.frame_id)
        return (
            Detection(
                kind=ObjectKind.CAR,
                confidence=0.8,
                timestamp=frame.timestamp,
                bbox=BoundingBox(x1=20, y1=20, x2=40, y2=40),
                track_id=1,
            ),
        )

    def close(self) -> None:
        self.closed = True


def test_offline_analyzes_every_frame_and_preserves_unknown_road_metrics(tmp_path: Path) -> None:
    config = settings(tmp_path)
    manager = VideoJobs(config)
    row = manager.reserve("owned-video.avi")
    directory = manager.directory(row["id"])
    video(directory / row["input_name"])
    detector = FixtureDetector()
    analyze(directory, config, detector)
    assert detector.frames == list(range(20)) and detector.closed
    state = manager.read(row["id"])
    assert state["status"] == "completed" and state["progress_percent"] == 100
    assert state["processed_frames"] == 20 and state["unique_tracks"] == 1
    assert state["stationary_candidates"] == 1
    report = json.loads((directory / "report.json").read_text())
    assert report["dropped_frames"] == 0 and report["physical_execution"] is False
    assert report["road_delay_seconds"] is None and report["queue_mae"] is None
    assert len((directory / "frames.jsonl").read_text().splitlines()) == 20
    assert (directory / "preview.jpg").stat().st_size > 0


def test_invalid_media_fails_before_model_initialization(tmp_path: Path) -> None:
    config = settings(tmp_path)
    manager = VideoJobs(config)
    row = manager.reserve("broken.mp4")
    directory = manager.directory(row["id"])
    (directory / row["input_name"]).write_bytes(b"not a video")
    run_video_job(directory, config)
    assert manager.read(row["id"])["error"] == "VIDEO_METADATA_INVALID"
    assert not (directory / "report.json").exists()


def test_limits_restart_delete_and_path_validation(tmp_path: Path) -> None:
    config = settings(tmp_path, video_max_jobs=1)
    manager = VideoJobs(config)
    with pytest.raises(HTTPException) as invalid:
        manager.reserve("model.pkl")
    assert invalid.value.status_code == 415
    row = manager.reserve("../video.avi")
    assert manager.directory(row["id"]).parent == manager.root
    with pytest.raises(HTTPException) as limit:
        manager.reserve("second.avi")
    assert limit.value.status_code == 409
    with pytest.raises(HTTPException):
        manager.directory("../../outside")

    async def restart() -> None:
        await manager.start()
        await manager.close()

    asyncio.run(restart())
    assert manager.read(row["id"])["error"] == "SERVER_RESTARTED"
    directory = manager.directory(row["id"])
    extra = directory / "unknown.txt"
    extra.write_text("preserve")
    with pytest.raises(HTTPException):
        manager.delete(row["id"])
    extra.unlink()
    manager.delete(row["id"])
    assert not directory.exists()


def test_upload_api_auth_limits_and_actual_failed_worker(tmp_path: Path) -> None:
    config = settings(tmp_path, video_max_upload_mb=1)
    headers = {"X-Operator-Token": "video-test-secret-1234", "Content-Type": "video/mp4"}
    with TestClient(create_app(config)) as client:
        assert client.get("/api/v1/video/jobs").status_code == 401
        assert (
            client.post(
                "/api/v1/video/jobs?filename=x.mp4", content=b"", headers=headers
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/v1/video/jobs?filename=x.exe", content=b"x", headers=headers
            ).status_code
            == 415
        )
        assert (
            client.post(
                "/api/v1/video/jobs?filename=x.mp4",
                content=b"x",
                headers={**headers, "content-length": str(2 * 1024 * 1024)},
            ).status_code
            == 413
        )
        result = client.post(
            "/api/v1/video/jobs?filename=broken.mp4", content=b"broken", headers=headers
        )
        assert result.status_code == 202
        identifier = result.json()["id"]
        deadline = time.monotonic() + 20
        while True:
            state = client.get(f"/api/v1/video/jobs/{identifier}", headers=headers).json()
            if state["status"] == "failed":
                break
            assert time.monotonic() < deadline
            time.sleep(0.1)
        assert state["error"] == "VIDEO_METADATA_INVALID"
        assert (
            client.get(f"/api/v1/video/jobs/{identifier}/report", headers=headers).status_code
            == 409
        )
        assert client.get("/health/ready").status_code == 200
        assert client.delete(f"/api/v1/video/jobs/{identifier}", headers=headers).status_code == 204


def test_duration_rejects_before_inference(tmp_path: Path) -> None:
    config = settings(tmp_path, video_max_frames=5)
    manager = VideoJobs(config)
    row = manager.reserve("long.avi")
    directory = manager.directory(row["id"])
    video(directory / row["input_name"])
    detector = FixtureDetector()
    with pytest.raises(ValueError, match="VIDEO_DURATION_LIMIT"):
        analyze(directory, config, detector)
    assert not detector.frames and detector.closed


def test_terminal_job_does_not_become_cancelled(tmp_path: Path) -> None:
    manager = VideoJobs(settings(tmp_path))
    row = manager.reserve("complete.mp4")
    row.update(status="completed", stage="completed")
    write_json(manager.directory(row["id"]) / "status.json", row)
    assert manager.cancel(row["id"])["status"] == "completed"


class OwnedProcess:
    def __init__(self) -> None:
        self.alive = True
        self.closed = False

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.alive = False

    def kill(self) -> None:
        self.alive = False

    def join(self, timeout: float) -> None:
        pass

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("reason", ["cancel", "timeout", "corrupt", "shutdown"])
def test_worker_is_reaped_on_all_stop_paths(tmp_path: Path, reason: str) -> None:
    manager = VideoJobs(settings(tmp_path))
    row = manager.reserve("recording.avi")
    row.update(status="analyzing", stage="analyzing")
    if reason == "timeout":
        row["heartbeat"] = time.monotonic() - 121
    status = manager.directory(row["id"]) / "status.json"
    write_json(status, row)
    if reason == "corrupt":
        status.write_text("broken", encoding="utf-8")
    process = OwnedProcess()
    manager.process, manager.active_id = process, row["id"]
    if reason == "cancel":
        assert manager.cancel(row["id"])["status"] == "cancelled"
    elif reason == "shutdown":
        asyncio.run(manager.close())
        assert manager.read(row["id"])["status"] == "cancelled"
    else:
        manager.refresh()
        if reason == "timeout":
            assert manager.read(row["id"])["error"] == "WORKER_TIMEOUT"
    assert not process.alive and process.closed
    assert manager.active_id is None and manager.process is None


def test_local_video_api_rejects_remote_clients_cross_site_and_simple_uploads(
    tmp_path: Path,
) -> None:
    config = settings(tmp_path).model_copy(update={"operator_token": None})
    with TestClient(create_app(config), client=("192.0.2.1", 50000)) as remote:
        assert remote.get("/api/v1/video/jobs").status_code == 403
    with TestClient(create_app(config), client=("127.0.0.1", 50000)) as local:
        assert local.get("/api/v1/video/jobs").status_code == 200
        assert (
            local.get("/api/v1/video/jobs", headers={"Origin": "https://other.example"}).status_code
            == 403
        )
        assert (
            local.get("/api/v1/video/jobs", headers={"Origin": "http://testserver"}).status_code
            == 200
        )
        assert (
            local.post(
                "/api/v1/video/jobs?filename=x.mp4",
                content=b"x",
                headers={"Content-Type": "text/plain"},
            ).status_code
            == 415
        )


def test_missing_frames_do_not_accumulate_stationary_time(tmp_path: Path) -> None:
    class Occluded(FixtureDetector):
        def process(self, frame: VideoFrame) -> tuple[Detection, ...]:
            result = super().process(frame)
            return () if 4 <= frame.frame_id < 12 else result

    config = settings(tmp_path)
    manager = VideoJobs(config)
    row = manager.reserve("occlusion.avi")
    directory = manager.directory(row["id"])
    video(directory / row["input_name"])
    analyze(directory, config, Occluded())
    assert manager.read(row["id"])["stationary_candidates"] == 0


def test_atomic_replace_retries_transient_windows_reader_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    temporary, target = tmp_path / "preview.tmp", tmp_path / "preview.jpg"
    temporary.write_bytes(b"new")
    target.write_bytes(b"old")
    original = Path.replace
    attempts = 0

    def replace(path: Path, destination: Path) -> Path:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise PermissionError("Windows sharing violation")
        return original(path, destination)

    monkeypatch.setattr(Path, "replace", replace)
    replace_with_retry(temporary, target)
    assert attempts == 3 and target.read_bytes() == b"new"


def test_inference_completes_when_preview_remains_locked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = settings(tmp_path)
    manager = VideoJobs(config)
    row = manager.reserve("locked.avi")
    directory = manager.directory(row["id"])
    video(directory / row["input_name"])
    (directory / "preview.jpg").write_bytes(b"previous-complete-preview")
    original = Path.replace

    def replace(path: Path, destination: Path) -> Path:
        if destination.name == "preview.jpg":
            raise PermissionError("Windows sharing violation")
        return original(path, destination)

    monkeypatch.setattr(Path, "replace", replace)
    detector = FixtureDetector()
    analyze(directory, config, detector)
    state = manager.read(row["id"])
    assert state["status"] == "completed" and state["processed_frames"] == 20
    assert detector.frames == list(range(20)) and detector.closed
    assert state["preview_skipped_updates"] > 0 and state["preview_version"] == 0
    assert (directory / "preview.jpg").read_bytes() == b"previous-complete-preview"


@pytest.mark.skipif(os.name != "nt", reason="Windows file sharing semantics only")
def test_actual_windows_open_reader_does_not_abort_preview_updates(tmp_path: Path) -> None:
    preview = tmp_path / "preview.jpg"
    preview.write_bytes(b"old")
    with preview.open("rb") as reader:
        assert not publish_preview(tmp_path, b"new")
        assert reader.read() == b"old"
    assert publish_preview(tmp_path, b"new")
    assert preview.read_bytes() == b"new"


def test_preview_endpoint_does_not_keep_a_windows_file_handle_open(tmp_path: Path) -> None:
    config = settings(tmp_path)
    headers = {"X-Operator-Token": "video-test-secret-1234"}
    manager = VideoJobs(config)
    row = manager.reserve("preview.avi")
    directory = manager.directory(row["id"])
    preview = directory / "preview.jpg"
    preview.write_bytes(b"old-image")
    with TestClient(create_app(config)) as client:
        with client.stream(
            "GET", f"/api/v1/video/jobs/{row['id']}/preview", headers=headers
        ) as response:
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
            assert publish_preview(directory, b"next-image")
            assert b"".join(response.iter_bytes()) == b"old-image"

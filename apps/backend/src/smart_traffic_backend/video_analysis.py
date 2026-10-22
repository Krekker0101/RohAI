"""Bounded offline video jobs. Worker lifetime is independent of traffic control."""

import asyncio
import json
import multiprocessing as mp
import re
import time
import uuid
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from smart_traffic_backend.config import Settings

TERMINAL = {"completed", "failed", "cancelled"}
JOB_ID = re.compile(r"^[a-f0-9]{32}$")
EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def replace_with_retry(temporary: Path, destination: Path) -> None:
    # Windows readers/antivirus may briefly deny replacement of an open file.
    for attempt in range(6):
        try:
            temporary.replace(destination)
            return
        except PermissionError:
            if attempt == 5:
                raise
            time.sleep(0.01 * 2**attempt)


def publish_preview(directory: Path, content: bytes) -> bool:
    temporary = directory / "preview.tmp"
    try:
        temporary.write_bytes(content)
        replace_with_retry(temporary, directory / "preview.jpg")
    except PermissionError:
        # The previous complete image remains valid. Inference must continue.
        return False
    return True


def write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    replace_with_retry(temporary, path)


def launch_worker(directory: str, settings_json: str) -> None:
    # Optional native dependencies are imported only in this terminable process.
    from smart_traffic_backend.video_worker import run_video_job

    run_video_job(Path(directory), Settings.model_validate_json(settings_json))


class VideoJobs:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.root = settings.video_jobs_directory.resolve()
        self.lock = asyncio.Lock()
        self.process: Any = None
        self.active_id: str | None = None
        self.task: asyncio.Task[None] | None = None
        self.stopping = asyncio.Event()
        self.upload_task: asyncio.Task[Any] | None = None

    def directory(self, identifier: str) -> Path:
        if not JOB_ID.fullmatch(identifier):
            raise HTTPException(404, "Video job not found")
        path = self.root / identifier
        if path.is_symlink() or not path.is_dir() or not path.resolve().is_relative_to(self.root):
            raise HTTPException(404, "Video job not found")
        return path

    def read(self, identifier: str) -> dict[str, Any]:
        path = self.directory(identifier) / "status.json"
        try:
            if path.is_symlink() or path.stat().st_size > 1024 * 1024:
                raise ValueError("Invalid job status")
            result: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            return result
        except (OSError, ValueError) as exc:
            raise HTTPException(503, "Video job status unavailable") from exc

    def listings(self) -> list[dict[str, Any]]:
        if not self.root.exists():
            return []
        rows = []
        for path in self.root.iterdir():
            if path.is_dir() and JOB_ID.fullmatch(path.name):
                with suppress(HTTPException):
                    rows.append(self.read(path.name))
        return sorted(rows, key=lambda row: row.get("created_at", ""), reverse=True)

    def refresh(self) -> None:
        if self.active_id is None:
            return
        identifier = self.active_id
        try:
            row = self.read(identifier)
        except HTTPException:
            self._stop_process()
            self.active_id = None
            return
        if row["status"] in TERMINAL:
            if self.process is not None:
                self.process.join(timeout=0.2)
            self._stop_process()
            self.active_id = None
            return
        if self.process is not None and self.process.is_alive():
            timed_out = time.monotonic() - row.get("heartbeat", 0) > 120
            expired = time.monotonic() - row["started_monotonic"] > row.get(
                "runtime_limit_seconds", 7200
            )
            if not timed_out and not expired:
                return
            self._stop_process()
            row.update(status="failed", stage="failed", error="WORKER_TIMEOUT")
            write_json(self.directory(identifier) / "status.json", row)
        else:
            self._stop_process()
            if row["status"] not in TERMINAL:
                row.update(status="failed", stage="failed", error="WORKER_EXITED")
                write_json(self.directory(identifier) / "status.json", row)
        self.active_id = None

    def _stop_process(self) -> None:
        if self.process is None:
            return
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=2)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=2)
        self.process.close()
        self.process = None

    async def start(self) -> None:
        # A restart never silently resumes a native worker or claims it completed.
        for row in await asyncio.to_thread(self.listings):
            if row["status"] not in TERMINAL:
                row.update(status="failed", stage="failed", error="SERVER_RESTARTED")
                await asyncio.to_thread(write_json, self.directory(row["id"]) / "status.json", row)
        self.task = asyncio.create_task(self._monitor(), name="offline-video-monitor")

    async def _monitor(self) -> None:
        while not self.stopping.is_set():
            if not self.lock.locked():
                async with self.lock:
                    with suppress(HTTPException, OSError, ValueError):
                        await asyncio.to_thread(self.refresh)
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=1)
            except TimeoutError:
                pass

    async def close(self) -> None:
        self.stopping.set()
        if self.task is not None:
            await self.task
        if self.upload_task is not None:
            self.upload_task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self.upload_task
        async with self.lock:
            if self.active_id is not None:
                try:
                    await asyncio.to_thread(self.cancel, self.active_id)
                finally:
                    await asyncio.to_thread(self._stop_process)
                    self.active_id = None

    def cancel(self, identifier: str) -> dict[str, Any]:
        self.refresh()
        row = self.read(identifier)
        if identifier == self.active_id:
            self._stop_process()
            self.active_id = None
            row.update(status="cancelled", stage="cancelled", error=None)
            write_json(self.directory(identifier) / "status.json", row)
        return row

    def reserve(
        self,
        filename: str,
        profile: Literal["standard", "aerial", "aerial_precise"] = "standard",
        roads: bool = False,
    ) -> dict[str, Any]:
        self.refresh()
        if self.active_id is not None:
            raise HTTPException(409, "Another video is being analyzed; finish or cancel it first")
        extension = Path(filename).suffix.lower()
        if extension not in EXTENSIONS | IMAGE_EXTENSIONS:
            raise HTTPException(
                415, "Supported formats: JPG, JPEG, PNG, BMP, WEBP, MP4, MOV, AVI, MKV, WEBM, M4V"
            )
        self.root.mkdir(parents=True, exist_ok=True)
        if (
            sum(p.is_dir() and bool(JOB_ID.fullmatch(p.name)) for p in self.root.iterdir())
            >= self.settings.video_max_jobs
        ):
            raise HTTPException(409, "Video history limit reached; delete a finished job first")
        used = sum(
            p.stat().st_size for p in self.root.glob("*/*") if p.is_file() and not p.is_symlink()
        )
        if used + self.settings.video_max_upload_mb * 1024 * 1024 > 4 * 1024**3:
            raise HTTPException(507, "Video storage budget exhausted")
        identifier = uuid.uuid4().hex
        directory = self.root / identifier
        directory.mkdir()
        row: dict[str, Any] = {
            "id": identifier,
            "filename": filename,
            "profile": profile,
            "road_segmentation_requested": roads,
            "runtime_limit_seconds": 7200 if profile == "standard" else 21600,
            "input_name": "source" + extension,
            "media_kind": "image" if extension in IMAGE_EXTENSIONS else "video",
            "created_at": datetime.now(UTC).isoformat(),
            "status": "uploading",
            "stage": "uploading",
            "processed_frames": 0,
            "total_frames": None,
            "progress_percent": None,
            "media_seconds": 0,
            "duration_seconds": None,
            "fps": None,
            "objects": [],
            "counts": {},
            "unique_tracks": 0,
            "explanations": [],
            "timeline": [],
            "error": None,
            "preview_version": 0,
            "physical_execution": False,
            "heartbeat": time.monotonic(),
            "started_monotonic": time.monotonic(),
        }
        write_json(directory / "status.json", row)
        return row

    def launch(self, row: dict[str, Any]) -> None:
        directory = self.directory(row["id"])
        row.update(
            status="analyzing",
            stage="validating",
            heartbeat=time.monotonic(),
            started_monotonic=time.monotonic(),
        )
        write_json(directory / "status.json", row)
        context = mp.get_context("spawn")
        worker_settings = self.settings.model_copy(
            update={
                "operator_token": None,
                "operations_viewer_token": None,
                "operations_enabled": False,
            }
        )
        self.process = context.Process(
            target=launch_worker,
            args=(str(directory), worker_settings.model_dump_json()),
            daemon=True,
        )
        try:
            self.process.start()
            self.active_id = row["id"]
        except Exception:
            self.process.close()
            self.process = None
            row.update(status="failed", stage="failed", error="WORKER_START_FAILED")
            write_json(directory / "status.json", row)
            raise

    def delete(self, identifier: str) -> None:
        row = self.read(identifier)
        if row["status"] not in TERMINAL or identifier == self.active_id:
            raise HTTPException(409, "Cancel the active video before deleting it")
        directory = self.directory(identifier)
        allowed = {
            "status.json",
            "status.tmp",
            "preview.jpg",
            "preview.tmp",
            "report.json",
            "report.tmp",
            "frames.jsonl",
            "worker-error.txt",
            row["input_name"],
        }
        files = list(directory.iterdir())
        if any(p.name not in allowed or p.is_symlink() or not p.is_file() for p in files):
            raise HTTPException(409, "Unexpected job files; deletion refused")
        for path in files:
            path.unlink()
        directory.rmdir()


def get_video_jobs(request: Request) -> VideoJobs:
    jobs: VideoJobs = request.app.state.video_jobs
    return jobs


JobsDep = Annotated[VideoJobs, Depends(get_video_jobs)]


async def disk_call(function: Callable[..., Any], *arguments: Any) -> Any:
    task = asyncio.create_task(asyncio.to_thread(function, *arguments))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def video_router(access: Callable[..., Any], prefix: str = "/api/v1/video") -> APIRouter:
    router = APIRouter(
        prefix=prefix, tags=["offline-video"], dependencies=[Depends(access)]
    )

    @router.get("/capabilities")
    async def capabilities(jobs: JobsDep) -> dict[str, Any]:
        import importlib.util

        available = all(
            importlib.util.find_spec(name) is not None for name in ("cv2", "torch", "ultralytics")
        )
        return {
            "max_upload_mb": jobs.settings.video_max_upload_mb,
            "max_duration_seconds": jobs.settings.video_max_duration_seconds,
            "max_frames": jobs.settings.video_max_frames,
            "max_image_pixels": 3840 * 2160,
            "formats": sorted(EXTENSIONS | IMAGE_EXTENSIONS),
            "vision_dependencies_available": available,
            "model_available": jobs.settings.vision.model_path.is_file()
            or jobs.settings.vision.model_package is not None
            or jobs.settings.vision.model_registry is not None,
            "aerial_model_available": jobs.settings.video_aerial_model_path.is_file()
            and jobs.settings.video_aerial_manifest_path.is_file(),
            "road_segmentation_available": importlib.util.find_spec("transformers") is not None
            and (jobs.settings.video_road_model_directory / "model.safetensors").is_file()
            and jobs.settings.video_road_manifest_path.is_file(),
            "physical_execution": False,
        }

    @router.get("/jobs")
    async def listing(jobs: JobsDep) -> list[dict[str, Any]]:
        return await asyncio.to_thread(jobs.listings)

    @router.post("/jobs", status_code=202)
    async def upload(
        request: Request,
        jobs: JobsDep,
        filename: str = Query(min_length=1, max_length=200),
        profile: Literal["standard", "aerial", "aerial_precise"] = Query(default="standard"),
        roads: bool = Query(default=False),
    ) -> dict[str, Any]:
        media_type = request.headers.get("content-type", "").split(";", 1)[0].lower()
        if (
            media_type != "application/octet-stream"
            and not media_type.startswith("video/")
            and not media_type.startswith("image/")
        ):
            raise HTTPException(
                415,
                "Upload raw image/video bytes using image/*, video/*, or application/octet-stream",
            )
        if jobs.lock.locked():
            raise HTTPException(409, "Video service busy")
        limit = jobs.settings.video_max_upload_mb * 1024 * 1024
        try:
            declared = int(request.headers.get("content-length", "0"))
        except ValueError as exc:
            raise HTTPException(400, "Invalid content length") from exc
        if declared > limit or declared < 0:
            raise HTTPException(413, "Video exceeds upload size limit")
        async with jobs.lock:
            row: dict[str, Any] = await disk_call(jobs.reserve, filename, profile, roads)
            directory = jobs.directory(row["id"])
            path = directory / row["input_name"]
            size = 0
            jobs.upload_task = asyncio.current_task()
            try:
                stream = await disk_call(path.open, "xb")
                try:
                    async with asyncio.timeout(300):
                        async for chunk in request.stream():
                            size += len(chunk)
                            if size > limit:
                                raise HTTPException(413, "Video exceeds upload size limit")
                            await disk_call(stream.write, chunk)
                finally:
                    await disk_call(stream.close)
                if size == 0:
                    raise HTTPException(400, "Video is empty")
                row["uploaded_bytes"] = size
                await disk_call(jobs.launch, row)
            except BaseException as exc:
                if jobs.active_id == row["id"]:
                    await disk_call(jobs.cancel, row["id"])
                path.unlink(missing_ok=True)
                row.update(status="failed", stage="failed", error="UPLOAD_FAILED")
                await asyncio.to_thread(write_json, directory / "status.json", row)
                if isinstance(exc, TimeoutError):
                    raise HTTPException(408, "Video upload timed out") from exc
                raise
            finally:
                jobs.upload_task = None
        return row

    @router.get("/jobs/{identifier}")
    async def status(identifier: str, jobs: JobsDep) -> dict[str, Any]:
        return await asyncio.to_thread(jobs.read, identifier)

    @router.post("/jobs/{identifier}/cancel")
    async def cancel(identifier: str, jobs: JobsDep) -> dict[str, Any]:
        async with jobs.lock:
            return await asyncio.to_thread(jobs.cancel, identifier)

    @router.delete("/jobs/{identifier}", status_code=204)
    async def delete(identifier: str, jobs: JobsDep) -> Response:
        async with jobs.lock:
            await asyncio.to_thread(jobs.refresh)
            await asyncio.to_thread(jobs.delete, identifier)
        return Response(status_code=204)

    @router.get("/jobs/{identifier}/preview")
    async def preview(identifier: str, jobs: JobsDep) -> Response:
        path = jobs.directory(identifier) / "preview.jpg"
        if not path.is_file() or path.is_symlink():
            raise HTTPException(404, "No analyzed frame yet")
        try:
            # Close the file before awaiting a slow client's response body. FileResponse
            # holds the Windows handle during transfer and prevents atomic replacement.
            content = await asyncio.to_thread(path.read_bytes)
        except OSError as exc:
            raise HTTPException(503, "Analyzed frame temporarily unavailable") from exc
        return Response(content, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @router.get("/jobs/{identifier}/report")
    async def report(identifier: str, jobs: JobsDep) -> FileResponse:
        row = await asyncio.to_thread(jobs.read, identifier)
        if row["status"] != "completed":
            raise HTTPException(409, "Report is available after successful analysis")
        path = jobs.directory(identifier) / "report.json"
        if not path.is_file() or path.is_symlink():
            raise HTTPException(503, "Report unavailable")
        filename = (
            "rohai-image-report.json"
            if row.get("media_kind") == "image"
            else "rohai-video-report.json"
        )
        return FileResponse(path, media_type="application/json", filename=filename)

    return router

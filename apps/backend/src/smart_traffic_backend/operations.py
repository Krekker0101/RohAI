"""Observation-only pilot analytics. Disk work never runs in the control loop."""

import asyncio
import csv
import io
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from smart_traffic_backend.runtime import Runtime
from smart_traffic_backend.vision_runtime import VisionRuntime


class ObservationStore:
    def __init__(self, path: Path, retention_days: int = 30, site_id: str = "local") -> None:
        self.path = path
        self.retention_days = retention_days
        self.site_id = site_id

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS site (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS samples (
                    session TEXT NOT NULL, sequence INTEGER NOT NULL,
                    measured_at TEXT NOT NULL, domain TEXT NOT NULL,
                    queue REAL, wait REAL, fps REAL, latency REAL,
                    drops INTEGER, ready INTEGER NOT NULL, reason TEXT NOT NULL,
                    PRIMARY KEY(session, measured_at)
                );
                CREATE INDEX IF NOT EXISTS samples_time ON samples(measured_at);
            """)
            connection.execute("INSERT OR IGNORE INTO site VALUES (1, ?)", (self.site_id,))
            site = connection.execute("SELECT id FROM site WHERE singleton=1").fetchone()
            if site is None or site[0] != self.site_id:
                raise ValueError("Observation database belongs to a different site")

    def append(self, sample: dict[str, Any]) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO samples VALUES "
                "(:session,:sequence,:measured_at,:domain,:queue,:wait,:fps,:latency,"
                ":drops,:ready,:reason)",
                sample,
            )

    def prune(self) -> None:
        cutoff = (datetime.now(UTC) - timedelta(days=self.retention_days)).isoformat()
        with self.connect() as connection:
            connection.execute("DELETE FROM samples WHERE measured_at < ?", (cutoff,))

    def report(self, since: datetime, until: datetime) -> dict[str, Any]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT domain, COUNT(*) AS samples, AVG(queue) AS mean_queue_objects, "
                "MAX(queue) AS peak_queue_objects, AVG(wait) AS mean_observed_wait_seconds, "
                "AVG(fps) AS mean_fps, MAX(latency) AS peak_reported_latency_ms, "
                "SUM(ready) AS ready_samples, COUNT(queue) AS measured_queue_samples "
                "FROM samples WHERE measured_at >= ? AND measured_at < ? GROUP BY domain",
                (since.isoformat(), until.isoformat()),
            ).fetchall()
            alerts = connection.execute(
                "SELECT domain, reason, COUNT(*) AS samples FROM samples "
                "WHERE measured_at >= ? AND measured_at < ? AND ready=0 "
                "GROUP BY domain,reason",
                (since.isoformat(), until.isoformat()),
            ).fetchall()
        return {
            "schema_version": "1.0",
            "site_id": self.site_id,
            "since": since.isoformat(),
            "until": until.isoformat(),
            "domains": [dict(row) for row in rows],
            "alerts": [dict(row) for row in alerts],
            "physical_execution": False,
            "methodology": "One-second snapshots; sample-weighted means. Vision wait is current "
            "queue observation; simulation wait is cumulative wait per arrival. "
            "Neither is completed-trip road delay. Domains never pooled. "
            "Unavailable observations remain null; gaps are not imputed.",
        }

    def backup(self) -> str:
        directory = self.path.parent / "backups"
        directory.mkdir(exist_ok=True)
        if len(list(directory.glob("observations-*.sqlite3"))) >= 10:
            raise ValueError("Backup limit reached; archive existing backups before creating more")
        name = f"observations-{uuid.uuid4().hex}.sqlite3"
        target = directory / name
        try:
            destination = sqlite3.connect(target)
            try:
                with self.connect() as source:
                    source.backup(destination)
            finally:
                destination.close()
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return name


class PilotOperations:
    def __init__(self, runtime: Runtime | VisionRuntime, store: ObservationStore) -> None:
        self.runtime, self.store = runtime, store
        self.session = uuid.uuid4().hex
        self.error: str | None = None
        self.task: asyncio.Task[None] | None = None
        self.started = time.monotonic()
        self.last_sample: dict[str, Any] | None = None
        self.stopping = asyncio.Event()
        self.initialized = False

    def sample(self) -> dict[str, Any]:
        runtime = self.runtime
        latest = runtime.latest
        ready = runtime.ready
        queue: float | None = None
        wait: float | None = None
        fps: float | None = None
        latency: float | None = None
        drops: int | None = None
        if isinstance(runtime, Runtime):
            domain = "SIMULATION"
            approaches = runtime.latest.traffic.approaches
            queue = float(sum(a.queue_length or 0 for a in approaches))
            wait = runtime.latest.kpi.mean_wait_per_arrival_seconds
        else:
            domain = {"file": "RECORDED_VIDEO", "synthetic": "SYNTHETIC"}.get(
                runtime.settings.vision.source, "LIVE_CAMERA"
            )
            vision = runtime.latest
            pipeline = vision.pipeline
            fps, latency = pipeline.processing_fps, pipeline.latency_ms
            drops = (
                pipeline.dropped_capture + pipeline.dropped_inference + pipeline.dropped_analysis
            )
            if vision.traffic is not None:
                directions = list(vision.traffic.directions.values())
                valid = set(vision.traffic.directions) == {
                    "north",
                    "south",
                    "east",
                    "west",
                } and all(d.observation.reliable for d in directions)
                if valid:
                    queue = float(sum(d.queue_count or 0 for d in directions))
                    weight = sum(d.queue_count or 0 for d in directions)
                    wait = (
                        sum(
                            (d.average_wait_seconds or 0) * (d.queue_count or 0) for d in directions
                        )
                        / weight
                        if weight
                        else 0.0
                    )
                ready = ready and valid
            else:
                ready = False
        return {
            "session": self.session,
            "sequence": latest.sequence,
            "measured_at": datetime.now(UTC).isoformat(),
            "domain": domain,
            "queue": queue if ready else None,
            "wait": wait if ready else None,
            "fps": fps,
            "latency": latency,
            "drops": drops,
            "ready": int(ready),
            "reason": runtime.failure or ("observation_unavailable" if not ready else "healthy"),
        }

    async def start(self) -> None:
        try:
            await asyncio.to_thread(self.store.initialize)
        except (OSError, sqlite3.Error, ValueError) as exc:
            self.error = type(exc).__name__
            return
        self.initialized = True
        self.task = asyncio.create_task(self._run(), name="pilot-observation-writer")

    async def _run(self) -> None:
        ticks = 0
        while not self.stopping.is_set():
            try:
                sample = self.sample()
                await asyncio.to_thread(self.store.append, sample)
                if ticks % 3600 == 0:
                    await asyncio.to_thread(self.store.prune)
                self.last_sample, self.error = sample, None
            except (OSError, sqlite3.Error, ValueError) as exc:
                self.error = type(exc).__name__
            ticks += 1
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=1)
            except TimeoutError:
                pass

    async def close(self) -> None:
        if self.task is not None:
            self.stopping.set()
            await self.task


def get_operations(request: Request) -> PilotOperations:
    service: PilotOperations = request.app.state.operations
    return service


OperationsDep = Annotated[PilotOperations, Depends(get_operations)]


def operations_router(operator: Any, reader: Any) -> APIRouter:
    router = APIRouter(
        prefix="/api/v1/operations", tags=["operations"], dependencies=[Depends(reader)]
    )
    report_lock = asyncio.Lock()

    @router.get("/status")
    async def status(service: OperationsDep) -> dict[str, Any]:
        return {
            "enabled": True,
            "error": service.error,
            "observation": service.last_sample,
            "retention_days": service.store.retention_days,
            "physical_execution": False,
        }

    @router.get("/report")
    async def report(
        service: OperationsDep,
        days: int = Query(default=1, ge=1, le=30),
        format: str = Query(default="json", pattern="^(json|csv)$"),
    ) -> Any:
        if not service.initialized:
            raise HTTPException(503, "Observation store unavailable")
        if report_lock.locked():
            raise HTTPException(409, "Report query already running")
        until = datetime.now(UTC)
        try:
            async with report_lock:
                result = await asyncio.to_thread(
                    service.store.report, until - timedelta(days=days), until
                )
        except (OSError, sqlite3.Error) as exc:
            raise HTTPException(503, "Observation store unavailable") from exc
        if format == "json":
            return result
        buffer = io.StringIO()
        columns = [
            "domain",
            "samples",
            "mean_queue_objects",
            "peak_queue_objects",
            "mean_observed_wait_seconds",
            "mean_fps",
            "peak_reported_latency_ms",
            "ready_samples",
            "measured_queue_samples",
        ]
        writer = csv.DictWriter(buffer, fieldnames=columns)
        writer.writeheader()
        writer.writerows(result["domains"])
        return Response(
            buffer.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="rohai-report.csv"'},
        )

    @router.get("/metrics")
    async def metrics(service: OperationsDep) -> Response:
        sample = service.sample()
        values = {
            "ready": sample["ready"],
            "storage_healthy": int(service.error is None),
            "uptime_seconds": time.monotonic() - service.started,
            "queue_objects": sample["queue"],
            "processing_fps": sample["fps"],
            "latency_ms": sample["latency"],
            "capture_pipeline_drops": sample["drops"],
        }
        lines = []
        for key, value in values.items():
            if value is not None:
                lines.extend([f"# TYPE rohai_{key} gauge", f"rohai_{key} {value}"])
        return Response("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")

    backup_lock = asyncio.Lock()

    @router.post("/backup", dependencies=[Depends(operator)])
    async def backup(service: OperationsDep) -> dict[str, str]:
        if not service.initialized:
            raise HTTPException(503, "Observation store unavailable")
        if backup_lock.locked():
            raise HTTPException(409, "Backup already running")
        async with backup_lock:
            try:
                name = await asyncio.to_thread(service.store.backup)
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            except (OSError, sqlite3.Error) as exc:
                raise HTTPException(503, "Backup failed") from exc
        return {"filename": name, "scope": "observation_store_only"}

    return router

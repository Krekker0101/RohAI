import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from smart_traffic_backend.config import Settings
from smart_traffic_backend.main import create_app
from smart_traffic_backend.operations import ObservationStore


def test_operations_requires_authentication() -> None:
    with pytest.raises(ValidationError, match="operator token"):
        Settings(operations_enabled=True)


def test_report_preserves_gaps_domains_and_backup(tmp_path: Path) -> None:
    store = ObservationStore(tmp_path / "observations.sqlite3")
    store.initialize()
    now = datetime.now(UTC)
    base = dict(
        session="s",
        sequence=1,
        measured_at=now.isoformat(),
        domain="LIVE_CAMERA",
        queue=None,
        wait=None,
        fps=2.0,
        latency=100.0,
        drops=5,
        ready=0,
        reason="stale",
    )
    store.append(base)
    store.append({**base, "measured_at": (now + timedelta(seconds=1)).isoformat()})
    store.append(
        {
            **base,
            "session": "sim",
            "domain": "SIMULATION",
            "queue": 10,
            "wait": 20,
            "ready": 1,
            "reason": "healthy",
        }
    )
    report = store.report(now - timedelta(seconds=1), now + timedelta(seconds=2))
    domains = {d["domain"]: d for d in report["domains"]}
    assert domains["LIVE_CAMERA"]["samples"] == 2
    assert domains["LIVE_CAMERA"]["mean_queue_objects"] is None
    assert domains["SIMULATION"]["mean_queue_objects"] == 10
    assert report["alerts"][0]["samples"] == 2
    backup = tmp_path / "backups" / store.backup()
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert connection.execute("SELECT COUNT(*) FROM samples").fetchone() == (3,)
    store.append({**base, "session": "old", "measured_at": (now - timedelta(days=31)).isoformat()})
    store.prune()
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM samples").fetchone()[0] == 3


def test_operations_api_and_failed_storage_do_not_break_control(tmp_path: Path) -> None:
    secret = "pilot-test-secret-1234"
    settings = Settings(
        operations_enabled=True,
        operator_token=SecretStr(secret),
        operations_database=tmp_path / "observations.sqlite3",
        tick_seconds=0.05,
        operations_viewer_token=SecretStr("viewer-test-secret-5678"),
    )
    headers = {"X-Operator-Token": secret}
    with TestClient(create_app(settings)) as client:
        viewer = {"X-Operator-Token": "viewer-test-secret-5678"}
        assert client.get("/api/v1/operations/status", headers=viewer).status_code == 200
        assert client.post("/api/v1/operations/backup", headers=viewer).status_code == 401
        assert (
            client.post(
                "/api/v1/emergency", headers=viewer, json={"direction": "north"}
            ).status_code
            == 401
        )
        assert client.get("/api/v1/operations/report").status_code == 401
        deadline = time.monotonic() + 3
        while not client.get("/api/v1/operations/status", headers=headers).json()["observation"]:
            assert time.monotonic() < deadline
            time.sleep(0.01)
        report = client.get("/api/v1/operations/report", headers=headers).json()
        assert report["domains"][0]["domain"] == "SIMULATION"
        assert report["physical_execution"] is False
        assert "rohai_ready 1" in client.get("/api/v1/operations/metrics", headers=headers).text
        assert client.get("/api/v1/operations/report?format=csv", headers=headers).text.startswith(
            "domain,samples"
        )
        assert client.post("/api/v1/operations/backup", headers=headers).status_code == 200
        assert client.get("/api/v1/operations/report?days=31", headers=headers).status_code == 422
    corrupt = tmp_path / "corrupt.sqlite3"
    corrupt.write_bytes(b"broken")
    settings = settings.model_copy(update={"operations_database": corrupt})
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/v1/operations/status", headers=headers).json()["error"]
        assert client.get("/api/v1/operations/report", headers=headers).status_code == 503


def test_different_site_cannot_read_or_backup_an_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "site-A.sqlite3"
    ObservationStore(path, site_id="site-A").initialize()
    secret = "pilot-test-secret-1234"
    settings = Settings(
        operations_enabled=True,
        operations_site_id="site-B",
        operations_database=path,
        operator_token=SecretStr(secret),
    )
    headers = {"X-Operator-Token": secret}
    with TestClient(create_app(settings)) as client:
        assert client.get("/health/ready").status_code == 200
        assert client.get("/api/v1/operations/report", headers=headers).status_code == 503
        assert client.post("/api/v1/operations/backup", headers=headers).status_code == 503

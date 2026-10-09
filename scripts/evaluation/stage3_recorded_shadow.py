"""Actual recorded-video observational integration; no camera or physical hardware."""

import argparse
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr
from smart_traffic_backend.config import Settings
from smart_traffic_backend.main import create_app
from traffic_vision.settings import VisionSettings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path("artifacts/rohai-ai-2.0/stage3")
    config = Settings(
        mode="vision",
        ai_artifact_directory=root,
        operator_token=SecretStr("recorded-local-research-session"),
        improvement_log_path=root / "recorded-improvement.sqlite3",
        vision=VisionSettings(
            source="file",
            source_id="intel-car",
            uri="recordings/car-detection.mp4",
            geometry_path=Path("configs/cameras/intel-car.json"),
            device="cpu",
            torch_threads=2,
        ),
    )
    headers = {"X-Operator-Token": "recorded-local-research-session"}
    records = []
    with TestClient(create_app(config)) as client:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if client.get("/api/v1/ai").json()["overview"]["runtime_ready"]:
                break
            time.sleep(0.1)
        before = client.get("/api/v1/system").json()
        snapshot = client.get("/api/v1/ai").json()
        approved = [m for m in snapshot["registry"]["models"] if m["status"] == "APPROVED"]
        if not approved:
            raise RuntimeError("No approved research actor")
        key = approved[0]["model_id"] + "@" + approved[0]["version"]
        response = client.post(
            "/api/v1/ai/shadow",
            headers=headers,
            json={
                "model_key": key,
                "reason": "Actual recorded-video observational integration acceptance",
                "geometry": {"lanes_per_approach": 2, "lane_length_m": 90, "headway_s": 2},
            },
        )
        if response.status_code != 200:
            raise RuntimeError(response.text)
        started = time.monotonic()
        while time.monotonic() - started < 8:
            time.sleep(0.25)
            snapshot = client.get("/api/v1/ai").json()
            records = snapshot["shadow"]["records"]
        after = client.get("/api/v1/system").json()
        stopped = client.post("/api/v1/ai/shadow/stop", headers=headers, json={}).json()
        rollback = client.post(
            "/api/v1/ai/rollback",
            headers=headers,
            json={"reason": "Recorded acceptance complete; restore prior selection"},
        ).json()
        assert records and all("error" not in r for r in records), records
        assert before["policy"] == after["policy"] == "adaptive"
        assert all(
            r["counterfactual_delay"] is None and not r["physical_execution"] for r in records
        )
        report = {
            "status": "PASS",
            "source": "RECORDED VIDEO",
            "frames_observed": len(records),
            "field_geometry": "EXPLICIT_ENGINEERING_ASSUMPTION; not calibrated field evidence",
            "field_accuracy": None,
            "physical_hardware": "NOT USED",
            "live_camera": "NOT VERIFIED",
            "before": before,
            "after": after,
            "records": records,
            "shadow": snapshot["shadow"],
            "stop": stopped,
            "rollback": rollback,
        }
    with (args.output or root / "recorded-shadow.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
    print(
        json.dumps(
            {
                "status": "PASS",
                "source": "RECORDED VIDEO",
                "decisions": len(records),
                "physical_execution": False,
            }
        )
    )


if __name__ == "__main__":
    main()

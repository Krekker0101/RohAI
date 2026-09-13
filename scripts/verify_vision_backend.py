"""Exercise the real four-stage YOLO pipeline through HTTP/WS and EOF shutdown."""

import argparse
import json
import logging
import time
from pathlib import Path

from fastapi.testclient import TestClient
from smart_traffic_backend.config import Settings
from smart_traffic_backend.hardware import MockHardwareController
from smart_traffic_backend.main import create_app
from smart_traffic_backend.vision_runtime import VisionTelemetry
from traffic_core.models import SignalStage
from traffic_vision.geometry import load_geometry
from traffic_vision.settings import VisionSettings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="recordings/car-detection.mp4")
    parser.add_argument("--geometry", type=Path, default=Path("configs/cameras/intel-car.json"))
    parser.add_argument("--timeout", type=float, default=150)
    args = parser.parse_args()
    logging.getLogger("httpx").setLevel(logging.WARNING)
    geometry = load_geometry(args.geometry)
    hardware = MockHardwareController()
    app = create_app(
        Settings(
            mode="vision",
            tick_seconds=0.05,
            vision=VisionSettings(
                source="file",
                source_id=geometry.source_id,
                uri=args.input,
                geometry_path=args.geometry,
            ),
        ),
        hardware,
    )
    deadline = time.monotonic() + args.timeout
    latencies = []
    with TestClient(app) as client:
        while client.get("/health/ready").status_code != 200:
            assert time.monotonic() < deadline, "Vision startup timed out"
            assert client.get("/health/live").status_code == 200
            time.sleep(0.05)
        assert client.get("/api/v1/vision/frame.jpg").content.startswith(b"\xff\xd8")
        with client.websocket_connect("/ws/telemetry") as stream:
            event = VisionTelemetry.model_validate(stream.receive_json())
            assert event.traffic is not None
        while True:
            started = time.monotonic()
            assert client.get("/health/live").status_code == 200
            latencies.append((time.monotonic() - started) * 1000)
            state = VisionTelemetry.model_validate(client.get("/api/v1/state").json())
            if state.failure is not None:
                assert state.failure == "vision_ended", state.failure
                assert state.signals.stage == SignalStage.ALL_RED
                assert client.get("/health/ready").status_code == 503
                break
            assert time.monotonic() < deadline, "Video did not reach EOF before deadline"
            time.sleep(0.1)
        report = {
            "pipeline": client.get("/api/v1/vision/status").json(),
            "final_telemetry": state.model_dump(mode="json"),
            "health_requests": len(latencies),
            "max_health_latency_ms": max(latencies),
        }
    assert hardware.closed and hardware.last_signal.stage == SignalStage.ALL_RED
    report["hardware_closed"] = hardware.closed
    path = Path("recordings/vision-backend.report.json")
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(path),
                "pipeline": report["pipeline"],
                "max_health_latency_ms": max(latencies),
                "hardware_closed": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

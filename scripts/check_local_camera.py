import argparse
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from traffic_vision.camera import LocalCameraSource
from traffic_vision.settings import VisionSettings


def check(index: int, seconds: int, backend: Literal["DSHOW", "MSMF"] = "DSHOW") -> dict[str, Any]:
    if not 0 <= index <= 10 or not 1 <= seconds <= 30:
        raise ValueError("Use camera index 0..10 and duration 1..30 seconds")
    if backend not in {"DSHOW", "MSMF"}:
        raise ValueError("Select DSHOW or MSMF backend explicitly")
    settings = VisionSettings(
        source="builtin_camera",
        webcam_index=index,
        source_id="laptop-capture-check",
        camera_width=640,
        camera_height=480,
        camera_backend=backend,
    )
    source = LocalCameraSource(settings)
    frames = 0
    age = []
    resolution: list[int] | None = None
    opened: float | None = None
    report: dict[str, Any] = {
        "scope": "LOCAL_CAPTURE_ONLY",
        "measured_at_utc": datetime.now(UTC).isoformat(),
        "camera_index": index,
        "frames_saved": False,
        "physical_execution": False,
        "road_accuracy": "NOT_VERIFIED",
        "full_vision_qualification": "NOT_VERIFIED",
        "status": "NOT_VERIFIED",
    }
    try:
        source.open()
        opened = time.monotonic()
        while time.monotonic() - opened < seconds:
            frame = source.read()
            if frame is None:
                break
            frames += 1
            resolution = list(frame.frame.shape[:2][::-1])
            age.append(max(0, time.monotonic() - frame.captured_at) * 1000)
        duration = time.monotonic() - opened
        report.update(
            status="MEASURED" if frames >= 2 else "FAIL",
            frames=frames,
            seconds=duration,
            capture_fps=frames / duration,
            resolution=resolution,
            median_receive_age_ms=statistics.median(age) if age else None,
            dropped_frames=source.dropped_frames,
            backend=source.backend,
        )
    except (OSError, RuntimeError) as exc:
        report.update(status="FAIL", error=source.failure_code or str(exc))
    finally:
        source.close()
        report["source_closed"] = not source.connected
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=int, default=0)
    parser.add_argument("--seconds", type=int, default=5)
    parser.add_argument("--backend", choices=("DSHOW", "MSMF"), default="DSHOW")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = check(args.index, args.seconds, args.backend)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    print(json.dumps(result))


if __name__ == "__main__":
    main()

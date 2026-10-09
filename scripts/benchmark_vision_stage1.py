"""Bounded nonphysical pipeline measurement; fixture and recording origins stay explicit."""

import argparse
import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path

from traffic_vision.analytics import TrafficAnalyzer
from traffic_vision.detector import SyntheticDetector, YOLODetector
from traffic_vision.geometry import load_geometry
from traffic_vision.pipeline import VisionPipeline
from traffic_vision.settings import VisionSettings
from traffic_vision.sources import SyntheticSource, VideoFileSource


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("fixture", "recorded"), default="fixture")
    parser.add_argument("--seconds", type=float, default=8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 60:
        parser.error("Measurement duration must be 1..60 seconds")
    recording = Path("recordings/car-detection.mp4")
    settings = VisionSettings(
        source="file" if args.mode == "recorded" else "synthetic",
        source_id="intel-car" if args.mode == "recorded" else "demo",
        uri=str(recording) if args.mode == "recorded" else "",
        geometry_path=Path(
            "configs/cameras/intel-car.json"
            if args.mode == "recorded"
            else "configs/cameras/demo.json"
        ),
        device="cpu",
        torch_threads=2,
    )
    source = (
        VideoFileSource(recording, settings.source_id)
        if args.mode == "recorded"
        else SyntheticSource(frames=10000, fps=30)
    )
    detector = YOLODetector(settings) if args.mode == "recorded" else SyntheticDetector()
    pipeline = VisionPipeline(
        source,
        detector,
        TrafficAnalyzer(load_geometry(settings.geometry_path), settings.analytics),
        settings,
    )
    started = time.monotonic()
    pipeline.start()
    try:
        deadline = started + settings.startup_timeout_seconds
        while pipeline.status().analyzed == 0 and pipeline.status().state not in {
            "failed",
            "ended",
        }:
            if time.monotonic() >= deadline:
                raise TimeoutError("No processed frames within startup deadline")
            time.sleep(0.02)
        measured_at = time.monotonic()
        process = None
        try:
            import psutil  # type: ignore[import-untyped]

            process = psutil.Process()
        except ImportError:
            pass
        cpu_start = process.cpu_times() if process else None
        memory_samples: list[int] = []
        while (
            time.monotonic() - measured_at < args.seconds and pipeline.status().state == "running"
        ):
            time.sleep(0.05)
            if process:
                memory_samples.append(int(process.memory_info().rss))
        measured_seconds = time.monotonic() - measured_at
        cpu_end = process.cpu_times() if process else None
        result = {
            "actual_torch_threads": __import__("torch").get_num_threads()
            if args.mode == "recorded"
            else None,
            "measured_at_utc": datetime.now(UTC).isoformat(),
            "origin": args.mode,
            "physical_camera": False,
            "ground_truth": "UNAVAILABLE",
            "queue_mae": None,
            "count_error": None,
            "model": detector.name,
            "input": str(recording) if args.mode == "recorded" else "generated controlled fixtures",
            "python": platform.python_version(),
            "platform": platform.platform(),
            "device": "cpu",
            "torch_threads": settings.torch_threads,
            "source_fps": source.fps,
            "cpu_percent_convention": "100 percent equals one logical CPU fully occupied",
            "elapsed_seconds": time.monotonic() - started,
            "measurement_seconds": measured_seconds,
            "peak_observed_rss_bytes": max(memory_samples) if memory_samples else None,
            "process_cpu_percent": (
                (cpu_end.user + cpu_end.system - cpu_start.user - cpu_start.system)
                / measured_seconds
                * 100
            )
            if cpu_end and cpu_start
            else None,
            "status": pipeline.status().model_dump(mode="json"),
            "performance": pipeline.performance(),
        }
    finally:
        pipeline.stop()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

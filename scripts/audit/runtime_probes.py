"""Unlabelled detector smoke/performance and blocking policy isolation probe."""

import asyncio
import json
import time
from pathlib import Path

import cv2
import numpy as np
from smart_traffic_backend.config import Settings
from smart_traffic_backend.hardware import MockHardwareController
from smart_traffic_backend.runtime import Runtime
from smart_traffic_backend.safety_architecture import PolicyCandidate, PolicyMode
from traffic_core.models import Phase
from traffic_vision.detector import YOLODetector
from traffic_vision.settings import VisionSettings
from traffic_vision.sources import VideoFrame


async def blocking_probe(root: Path) -> dict[str, object]:
    class BlockingPolicy:
        async def propose(self, observation: object) -> PolicyCandidate:
            time.sleep(0.2)  # noqa: ASYNC251 - deliberate fault injection
            return PolicyCandidate(phase=Phase.EW, green_seconds=15)

    runtime = Runtime(
        Settings(
            hardware_timeout_seconds=0.02, improvement_log_path=root / "blocking_probe.sqlite3"
        ),
        MockHardwareController(),
    )
    runtime.configure_policy(PolicyMode.SHADOW, BlockingPolicy())
    start = time.perf_counter()
    runtime._schedule_policy_audit(runtime.latest.traffic, Phase.NS.value, 15, "baseline")
    await asyncio.sleep(0.01)
    elapsed = time.perf_counter() - start
    if runtime._policy_task is not None:
        await runtime._policy_task
    error = runtime.decision_journal.snapshot()[-1].policy_error
    runtime.improvement_writer.close()
    return {
        "configured_timeout_s": 0.02,
        "event_loop_delay_s": elapsed,
        "policy_error": error,
        "physical_actuation": False,
    }


def main() -> None:
    root = Path("artifacts/final-audit/20261004")
    detector = YOLODetector(VisionSettings(device="cpu"))
    start = time.perf_counter()
    detector.open()
    load_s = time.perf_counter() - start
    records = []
    capture = cv2.VideoCapture("recordings/car-detection.mp4")
    try:
        for index in range(18):
            if index < 3:
                image, condition = np.zeros((720, 1280, 3), dtype=np.uint8), "black"
            elif index < 6:
                image, condition = np.full((720, 1280, 3), 3, dtype=np.uint8), "night-flat"
            else:
                ok, image = capture.read()
                if not ok:
                    break
                condition = "unlabelled-demo-replay"
            start = time.perf_counter()
            detections = detector.process(
                VideoFrame(image, index / 10, index, "audit", time.monotonic())
            )
            records.append(
                {
                    "condition": condition,
                    "latency_ms": (time.perf_counter() - start) * 1000,
                    "detections": len(detections),
                    "ground_truth": "UNAVAILABLE",
                }
            )
    finally:
        capture.release()
        detector.close()
    result = {
        "scope": "CPU smoke only, not detection accuracy",
        "load_and_warmup_s": load_s,
        "frames": records,
        "blocking_policy": asyncio.run(blocking_probe(root)),
    }
    (root / "runtime_probes.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({"frames": len(records), "blocking": result["blocking_policy"]}))


if __name__ == "__main__":
    main()

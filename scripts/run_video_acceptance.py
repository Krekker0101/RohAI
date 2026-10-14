"""Real YOLO + browser acceptance on a short recorded clip, without camera or ESP32."""

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import cv2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source", type=Path, default=Path("recordings/person-bicycle-car-detection.mp4")
    )
    parser.add_argument("--output", type=Path, default=Path("artifacts/video-analysis"))
    parser.add_argument("--frames", type=int, default=24)
    parser.add_argument("--roads", action="store_true")
    parser.add_argument(
        "--profile", choices=("standard", "aerial", "aerial_precise"), default="standard"
    )
    parser.add_argument("--start-frame", type=int, default=48)
    args = parser.parse_args()
    if not 1 <= args.frames <= 240:
        parser.error("Acceptance clip must contain 1..240 frames")
    if args.start_frame < 0:
        parser.error("Start frame cannot be negative")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    clip = output / "acceptance-clip.avi"
    capture = cv2.VideoCapture(str(args.source))
    writer = None
    count = 0
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not capture.isOpened() or fps <= 0:
            raise ValueError("Cannot read source video")
        for _ in range(args.start_frame):
            if not capture.grab():
                raise ValueError("Source shorter than requested start frame")
        while count < args.frames:
            ok, frame = capture.read()
            if not ok:
                break
            if writer is None:
                height, width = frame.shape[:2]
                writer = cv2.VideoWriter(
                    str(clip), cv2.VideoWriter.fourcc(*"MJPG"), fps, (width, height)
                )
                if not writer.isOpened():
                    raise ValueError("Cannot create acceptance clip")
            writer.write(frame)
            count += 1
    finally:
        capture.release()
        if writer is not None:
            writer.release()
    if not count:
        raise ValueError("Source contains no decoded frames")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ)
    env.update(
        {
            "STA_VIDEO_JOBS_DIRECTORY": str(output / "jobs"),
            "STA_AI_ARTIFACT_DIRECTORY": str(output / "ai"),
            "STA_IMPROVEMENT_LOG_PATH": str(output / "decisions.sqlite3"),
            "ROHAI_VIDEO_BASE_URL": base,
            "ROHAI_VIDEO_ACCEPTANCE_FILE": str(clip),
            "ROHAI_VIDEO_ARTIFACTS": str(output),
            "ROHAI_VIDEO_PROFILE": args.profile,
            "ROHAI_VIDEO_ROADS": "1" if args.roads else "",
        }
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(path: str) -> Any:
        with opener.open(base + path, timeout=10) as response:
            return json.load(response)

    with (output / "server.log").open("wb") as log:
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "smart_traffic_backend.video_cli",
                "--no-browser",
                "--port",
                str(port),
            ],
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + 45
            while True:
                if server.poll() is not None:
                    raise RuntimeError("Server exited; inspect server.log")
                try:
                    if get("/health/ready").get("status") == "ready":
                        break
                except (OSError, urllib.error.URLError):
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError("Server readiness timeout")
                time.sleep(0.2)
            browser = subprocess.run(
                [
                    "npm.cmd" if os.name == "nt" else "npm",
                    "exec",
                    "playwright",
                    "test",
                    "video-analysis.spec.ts",
                ],
                cwd="apps/dashboard",
                env=env,
                timeout=240,
            )
            if browser.returncode:
                return browser.returncode
            jobs = get("/api/v1/video/jobs")
            completed = [row for row in jobs if row["status"] == "completed"]
            if not completed:
                raise AssertionError("No completed real video job")
            row = completed[0]
            report = get(f"/api/v1/video/jobs/{row['id']}/report")
            assert report["processed_frames"] == count == report["total_frames"]
            assert report["model_weights_sha256"] and report["detection_observations"] > 0
            assert report["physical_execution"] is False and report["dropped_frames"] == 0
            if args.roads:
                assert report["road_segmentation"]["enabled"]
                assert report["road_segmentation"]["revision"]
            evidence = {
                "result": "PASS",
                "source": str(args.source),
                "source_start_frame": args.start_frame,
                "clip_frames": count,
                "report": report,
                "accuracy_against_human_labels": "NOT MEASURED",
            }
            (output / "acceptance.json").write_text(
                json.dumps(evidence, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(
                f"PASS: {count} frames, real {report['model']}, "
                f"{report['detection_observations']} observations; {output}"
            )
            return 0
        finally:
            if server.poll() is None:
                if os.name == "nt":
                    server.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    server.terminate()
                try:
                    server.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())

"""Prepare silent body loops and mouth tracking from explicitly supplied Pilot clips."""

import argparse
import hashlib
import importlib
import json
import math
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def prepare(
    source: Path, target: Path, name: str, mouth: tuple[int, int], ffmpeg: str
) -> dict[str, Any]:
    probe = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(source)], capture_output=True, text=True, check=False
    )
    capture = cv2.VideoCapture(str(source))
    fps = float(capture.get(cv2.CAP_PROP_FPS))
    ok, initial = capture.read()
    if not ok or fps <= 0:
        raise ValueError(f"Cannot decode animation: {source.name}")
    height, width = initial.shape[:2]
    feature_mask = np.zeros((height, width), np.uint8)
    feature_mask[90:215, 170:380] = 255
    original = cv2.goodFeaturesToTrack(
        cv2.cvtColor(initial, cv2.COLOR_BGR2GRAY), 100, 0.01, 7, mask=feature_mask
    )
    if original is None:
        raise ValueError("No face landmarks for mouth tracking")
    points = original.copy()
    previous = cv2.cvtColor(initial, cv2.COLOR_BGR2GRAY)
    transform = np.eye(2, 3, dtype=np.float32)
    frames: list[Any] = []
    track: list[dict[str, float]] = []
    capture.set(cv2.CAP_PROP_POS_FRAMES, 0)
    # Exported end cards start after the character segment; keep 3.84 seconds.
    for index in range(min(96, round(fps * 3.84))):
        ok, frame = capture.read()
        if not ok:
            break
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        if index:
            flow: Any = cv2.calcOpticalFlowPyrLK
            current, status, _ = flow(previous, gray, points, None)
            if current is None or status is None:
                raise ValueError("Face tracking interrupted")
            valid = status.ravel() == 1
            original, points = original[valid], current[valid]
            if len(points) < 6:
                raise ValueError("Insufficient tracked face landmarks")
            estimated, _ = cv2.estimateAffinePartial2D(original, points, method=cv2.RANSAC)
            if estimated is not None:
                transform = estimated.astype(np.float32)
        previous = gray
        center = transform @ np.array([mouth[0], mouth[1], 1], np.float32)
        scale = float(np.clip(np.linalg.norm(transform[:, 0]), 0.8, 1.25))
        angle = math.degrees(math.atan2(float(transform[1, 0]), float(transform[0, 0])))
        cx, cy = float(center[0]), float(center[1])
        mask = np.zeros((height, width), np.uint8)
        x0, x1 = max(0, round(cx - 55)), min(width, round(cx + 55))
        y0, y1 = max(0, round(cy - 32)), min(height, round(cy + 32))
        crop = frame[y0:y1, x0:x1]
        blue, green, red = cv2.split(crop)
        lip: Any = (
            (red.astype(float) > green.astype(float) * 1.15)
            & (red < 245)
            & (green < 125)
            & (blue < 115)
        ).astype(np.uint8) * 255
        lip = cv2.morphologyEx(lip, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        _, labels, stats, centers = cv2.connectedComponentsWithStats(lip)
        candidates = []
        for label, (_x, _y, w, h, area) in enumerate(stats[1:], 1):
            if 18 <= w <= 85 and 2 <= h <= 36 and area >= 18 and w / h > 1.2:
                px, py = centers[label] + [x0, y0]
                score = abs(px - cx) / 40 + abs(py - cy) / 30 + abs(w - 45) / 45
                candidates.append((score, label))
        if candidates:
            _, label = min(candidates)
            x, y, w, h, _ = stats[label]
            local = (labels == label).astype(np.uint8) * 255
            dark = (red < 105) & (green < 90) & (blue < 90) & (local > 0)
            px, py = centers[label] + [x0, y0]
            if w < 30 and py < cy - 4:
                # The only dark feature can be the nose when the lips close.
                cx, cy = float(px - 4), float(py + 22)
                cv2.ellipse(mask, (round(cx), round(cy)), (29, 11), angle, 0, 360, 255, -1)
            else:
                if w >= 40 and h >= 18 and int(dark.sum()) < 100:
                    local[: y + round(h * 0.4)] = 0
                contours, _ = cv2.findContours(local, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(local, contours, -1, 255, -1)
                local = cv2.dilate(local, np.ones((5, 5), np.uint8))
                bx, by, bw, bh = cv2.boundingRect(local)
                cx, cy = float(bx + bw / 2 + x0), float(by + bh / 2 + y0)
                cv2.ellipse(
                    mask,
                    (round(cx), round(cy)),
                    (round(bw / 2 + 2), round(bh / 2 + 2)),
                    0,
                    0,
                    360,
                    255,
                    -1,
                )
        else:
            cv2.ellipse(
                mask, (round(cx), round(cy)), (round(29 * scale), 10), angle, 0, 360, 255, -1
            )
        neutral = cv2.inpaint(frame, mask, 7, cv2.INPAINT_TELEA)
        frames.append(neutral)
        track.append(
            {
                "x": round(cx / width, 5),
                "y": round(cy / height, 5),
                "width": round(58 * scale / width, 5),
                "rotation": round(angle, 3),
            }
        )
    capture.release()
    if len(frames) < 30:
        raise ValueError("Animation is too short")
    # Ping-pong prevents a jump from the final pose back to the first pose.
    frames += frames[-2:0:-1]
    track += track[-2:0:-1]
    output = target / f"{name}.mp4"
    process = subprocess.Popen(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{width}x{height}",
            "-r",
            str(fps),
            "-i",
            "pipe:0",
            "-map",
            "0:v:0",
            "-an",
            "-c:v",
            "libx264",
            "-crf",
            "21",
            "-preset",
            "medium",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(output),
        ],
        stdin=subprocess.PIPE,
    )
    assert process.stdin is not None
    for frame in frames:
        process.stdin.write(frame.tobytes())
    process.stdin.close()
    if process.wait() != 0:
        raise ValueError("Animation encoding failed")
    verification = subprocess.run(
        [ffmpeg, "-hide_banner", "-i", str(output)], capture_output=True, text=True, check=False
    )
    if re.search(r"Stream #.*Audio:", verification.stderr):
        raise ValueError("Audio track found in prepared animation")
    cv2.imwrite(str(target / f"{name}.jpg"), frames[0])
    return {
        "src": f"{name}.mp4",
        "poster": f"{name}.jpg",
        "fps": fps,
        "width": width,
        "height": height,
        "mouth": track,
        "audio_tracks": 0,
        "source_audio_tracks": len(re.findall(r"Stream #.*Audio:", probe.stderr)),
        "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listening", type=Path, required=True)
    parser.add_argument("--thinking", type=Path, required=True)
    parser.add_argument("--explaining", type=Path, required=True)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--output", type=Path, default=Path("apps/dashboard/public/pilot"))
    args = parser.parse_args()
    ffmpeg = args.ffmpeg or shutil.which("ffmpeg")
    if not ffmpeg:
        ffmpeg = importlib.import_module("imageio_ffmpeg").get_ffmpeg_exe()
    args.output.mkdir(parents=True, exist_ok=True)
    result = {}
    for name, mouth in (
        ("listening", (291, 224)),
        ("thinking", (245, 236)),
        ("explaining", (259, 239)),
    ):
        result[name] = prepare(getattr(args, name), args.output, name, mouth, ffmpeg)
        print(f"Prepared {name}: {len(result[name]['mouth'])} silent frames", flush=True)
    (args.output / "animations.json").write_text(
        json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )


if __name__ == "__main__":
    main()

"""Inspect available assets without promoting prediction logs to ground truth."""

import json
from pathlib import Path
from typing import Any

from traffic_vision.dataset.files import digest_file


def inventory(root: Path) -> dict[str, Any]:
    import cv2

    videos: list[dict[str, Any]] = []
    for path in sorted((root / "recordings").glob("*.mp4")):
        capture = cv2.VideoCapture(str(path))
        try:
            frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
            fps = capture.get(cv2.CAP_PROP_FPS)
            videos.append(
                {
                    "path": str(path.relative_to(root)).replace("\\", "/"),
                    "sha256": digest_file(path),
                    "width": int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
                    "height": int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                    "fps": fps,
                    "frames": frames,
                    "duration_seconds": frames / fps if fps else None,
                    "independent_ground_truth": False,
                    "role": "existing_demo_or_rendered_output; provenance review required",
                }
            )
        finally:
            capture.release()
    logs: list[dict[str, Any]] = []
    for path in sorted((root / "recordings").glob("*.jsonl")):
        with path.open(encoding="utf-8") as stream:
            first = stream.readline()
        try:
            data = json.loads(first)
            kind = (
                "system_predictions_not_ground_truth"
                if isinstance(data, dict) and "traffic" in data and "detections" in data
                else "unreviewed_log"
            )
        except ValueError:
            kind = "invalid_jsonl_header"
        logs.append(
            {
                "path": str(path.relative_to(root)).replace("\\", "/"),
                "sha256": digest_file(path),
                "role": kind,
            }
        )
    return {
        "format": "rohai-data-inventory-1",
        "videos": videos,
        "logs": logs,
        "independent_labeled_test_release_found": any(
            (root / "datasets" / "releases").glob("*/release.json")
        ),
        "note": "Inventory only. Demo predictions are not ground truth.",
    }

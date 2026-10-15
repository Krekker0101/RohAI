"""Measure sliced aerial detector on existing review frames; never infer accuracy without labels."""

import argparse
import json
import time
from collections import Counter
from pathlib import Path

import cv2
from traffic_vision.aerial import AerialDetector
from traffic_vision.dataset.files import digest_file
from traffic_vision.settings import AerialSettings, VisionSettings
from traffic_vision.sources import VideoFrame


def run(
    review: Path, output: Path, count: int, tile_size: int, batch_size: int, threads: int
) -> dict:
    if count < 1:
        raise ValueError("count must be positive")
    manifest = json.loads((review / "review.json").read_text(encoding="utf-8"))
    settings = VisionSettings(
        model_path=Path("datasets/models/yolo26s.pt"),
        manifest_path=Path("configs/models/yolo26s.json"),
        torch_threads=threads,
    )
    detector = AerialDetector(settings, AerialSettings(tile_size=tile_size, batch_size=batch_size))
    rows = []
    try:
        detector.open()
        for entry in manifest["frames"][:count]:
            path = (review / entry["image"]).resolve()
            if not path.is_relative_to(review.resolve()) or digest_file(path) != entry["sha256"]:
                raise ValueError("Review image path or hash mismatch")
            image = cv2.imread(str(path))
            if image is None or image.shape[:2] != (entry["height"], entry["width"]):
                raise ValueError("Review image decode or dimensions mismatch")
            started = time.perf_counter()
            detections = detector.process(
                VideoFrame(
                    frame=image,
                    timestamp=entry["nominal_timestamp_seconds"],
                    frame_id=entry["frame_index"],
                    source_id="review",
                    captured_at=time.monotonic(),
                )
            )
            rows.append(
                {
                    "frame_index": entry["frame_index"],
                    "tiles": detector.tiles_per_frame,
                    "inference_ms": round((time.perf_counter() - started) * 1000),
                    "candidate_count": len(detections),
                    "classes": dict(Counter(d.kind.value for d in detections)),
                    "detections": [
                        {
                            "class": d.kind.value,
                            "confidence": round(d.confidence, 5),
                            "bbox": [
                                round(d.bbox.x1, 1),
                                round(d.bbox.y1, 1),
                                round(d.bbox.x2, 1),
                                round(d.bbox.y2, 1),
                            ],
                        }
                        for d in detections
                    ],
                }
            )
    finally:
        detector.close()
    report = {
        "source_sha256": manifest["source_sha256"],
        "model": "YOLO26s COCO sliced, CPU",
        "model_sha256": detector.manifest.weights_hash if detector.manifest else None,
        "settings": {
            "tile_size": tile_size,
            "overlap": 0.2,
            "batch_size": batch_size,
            "torch_threads": threads,
        },
        "confidence": detector.aerial.confidence,
        "frames": rows,
        "quality": "UNSCORED_NO_HUMAN_LABELS",
        "timing_scope": "Detector process only; excludes camera, decode, analytics and rendering",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=3)
    parser.add_argument("--tile-size", type=int, default=1024)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    print(
        json.dumps(
            run(
                args.review, args.output, args.count, args.tile_size, args.batch_size, args.threads
            ),
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

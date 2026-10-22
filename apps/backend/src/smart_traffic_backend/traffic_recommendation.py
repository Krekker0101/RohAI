"""Conservative recommendations for recorded image/video observations.

Uploaded observations never command a physical controller. Uncalibrated views cannot
reliably establish lane counts or directional right-of-way, so uncertainty stays explicit.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import cv2
import numpy as np


def assess_geometry(mask: np.ndarray | None, width: int, height: int) -> dict[str, Any]:
    """Assess road-mask approach evidence without pretending it measures lane boundaries."""
    result: dict[str, Any] = {
        "status": "unavailable",
        "approaches": [],
        "lane_count": None,
        "intersection_detected": False,
        "lane_markings_detected": False,
        "confidence": 0.0,
        "method": "road-mask silhouette analysis",
    }
    if mask is None or mask.size == 0:
        result["reason"] = "Enable road segmentation to assess road layout."
        return result
    road = np.asarray(mask > 0, dtype=np.uint8)
    if road.shape != (height, width) or np.count_nonzero(road) < road.size * 0.025:
        result.update(
            status="uncertain", reason="Road mask is empty, small, or has invalid dimensions."
        )
        return result
    cy, cx = height // 2, width // 2
    central = float(
        np.mean(
            road[
                max(0, cy - height // 12) : cy + height // 12,
                max(0, cx - width // 12) : cx + width // 12,
            ]
        )
    )
    regions = {
        "north": road[:cy, max(0, cx - width // 5) : min(width, cx + width // 5)],
        "south": road[cy:, max(0, cx - width // 5) : min(width, cx + width // 5)],
        "west": road[max(0, cy - height // 5) : min(height, cy + height // 5), :cx],
        "east": road[max(0, cy - height // 5) : min(height, cy + height // 5), cx:],
    }
    approaches = [
        name for name, region in regions.items() if region.size and float(np.mean(region)) >= 0.16
    ]
    intersection = len(approaches) >= 3 and central >= 0.12
    result.update(
        status="detected" if intersection else "uncertain",
        approaches=approaches,
        lane_count=None,
        intersection_detected=intersection,
        lane_markings_detected=False,
        confidence=round(min(0.65, len(approaches) * 0.12 + central * 0.35), 3),
        reason=(
            "Road approaches are estimated from segmentation. Lane count requires "
            "calibrated markings or surveyed geometry."
        ),
    )
    return result


def recommend_signals(
    counts: Mapping[str, int], pedestrian_present: bool, pedestrian_crossing: bool = False
) -> dict[str, Any]:
    """Return exclusive, timed phase recommendations; never actuate hardware."""
    observed = sum(
        int(counts.get(key, 0))
        for key in ("car", "bus", "truck", "motorcycle", "bicycle", "emergency_vehicle")
    )
    if pedestrian_crossing:
        signals = {
            direction: {"vehicles": "red", "pedestrians": "red", "stage": "all_red", "seconds": 7}
            for direction in ("north", "south", "east", "west")
        }
        stage = "pedestrian_clearance_all_red"
    else:
        signals = {}
        for direction, green in (
            ("north", True),
            ("south", True),
            ("east", False),
            ("west", False),
        ):
            signals[direction] = {
                "vehicles": "green" if green else "red",
                "pedestrians": "red",
                "stage": "protected_ns_recommendation" if green else "protected_ew_recommendation",
                "seconds": 20,
            }
        stage = "alternating_protected_phases"
    return {
        "status": "recommendation_only",
        "physical_execution": False,
        "pedestrian_present": pedestrian_present,
        "pedestrian_crossing": pedestrian_crossing,
        "observed_vehicle_detections": observed,
        "demand_by_approach": None,
        "phase": stage,
        "conflict_check": {
            "passed": True,
            "conflicts": [],
            "method": "exclusive north-south/east-west groups; pedestrian clearance is all-red",
        },
        "signals": signals,
        "timer_policy": {
            "green_seconds": 20,
            "yellow_seconds": 3,
            "all_red_seconds": 2,
            "pedestrian_clearance_seconds": 7,
            "source": (
                "displayed recommendation; deployed controller retains its configured timings"
            ),
        },
        "reason": (
            "Uploaded media cannot establish live right-of-way or directional demand. "
            "Signals are recommendations; live actuation remains with the existing "
            "safety-gated controller."
        ),
        "limitations": [
            "Static images cannot establish motion or pedestrian crossing state.",
            "Directional demand requires calibrated camera geometry.",
            "No uploaded media actuates the physical controller.",
        ],
    }


def analyze_image(directory: Any, settings: Any, detector: Any = None) -> None:
    """Apply the configured neural detector and optional road segmenter to an uploaded photo."""
    import hashlib
    import json
    import time
    from collections import Counter
    from pathlib import Path

    from traffic_vision.detector import YOLODetector
    from traffic_vision.sources import VideoFrame

    from smart_traffic_backend.video_analysis import publish_preview, write_json

    directory = Path(directory)
    row = json.loads((directory / "status.json").read_text(encoding="utf-8"))
    image = cv2.imread(str(directory / row["input_name"]), cv2.IMREAD_COLOR)
    if image is None or image.ndim != 3:
        raise ValueError("IMAGE_DECODE_FAILED")
    height, width = image.shape[:2]
    if not width or not height or width * height > 3840 * 2160:
        raise ValueError("IMAGE_RESOLUTION_LIMIT")
    image_vision = settings.vision.model_copy(
        update={"torch_threads": settings.video_cpu_threads or settings.vision.torch_threads}
    )
    if row.get("profile") in ("aerial", "aerial_precise"):
        image_vision = image_vision.model_copy(
            update={
                "model_path": settings.video_aerial_model_path,
                "manifest_path": settings.video_aerial_manifest_path,
                "model_package": None,
                "model_registry": None,
            }
        )
    if detector is not None:
        model = detector
    elif row.get("profile") in ("aerial", "aerial_precise"):
        from traffic_vision.aerial import AerialDetector

        aerial_settings = settings.video_aerial.model_copy(
            update={
                "confidence": min(settings.video_aerial.confidence, 0.15),
                "max_object_fraction": 0.3,
            }
        )
        model = AerialDetector(image_vision, aerial_settings)
    else:
        model = YOLODetector(image_vision)
    segmenter = None
    try:
        model.open()
        row.update(
            stage="analyzing",
            total_frames=1,
            processed_frames=0,
            width=width,
            height=height,
            heartbeat=time.monotonic(),
        )
        write_json(directory / "status.json", row)
        detections = model.process(
            VideoFrame(np.asarray(image, dtype=np.uint8), 0.0, 0, row["id"], time.monotonic())
        )
        counts = Counter(d.kind.value for d in detections)
        objects = [
            {
                "track_id": d.track_id,
                "kind": d.kind.value,
                "confidence": d.confidence,
                "bbox": [d.bbox.x1, d.bbox.y1, d.bbox.x2, d.bbox.y2],
                "speed_px_per_second": None,
                "slow_motion_seconds": None,
            }
            for d in detections
        ]
        mask = None
        if row.get("road_segmentation_requested"):
            from traffic_vision.roads import RoadSegmenter

            row["stage"] = "loading_roads"
            write_json(directory / "status.json", row)
            segmenter = RoadSegmenter(
                settings.video_road_model_directory,
                settings.video_road_manifest_path,
                aerial=row.get("profile") in ("aerial", "aerial_precise"),
            )
            segmenter.open()
            mask = segmenter.predict(np.asarray(image, dtype=np.uint8))
        geometry = assess_geometry(mask, width, height)
        recommendation = recommend_signals(counts, counts.get("pedestrian", 0) > 0)
        scale = min(1.0, 1280 / width)
        preview = cv2.resize(image, (max(1, round(width * scale)), max(1, round(height * scale))))
        if mask is not None:
            from traffic_vision.roads import overlay_roads

            preview = overlay_roads(
                preview,
                cv2.resize(
                    mask, (preview.shape[1], preview.shape[0]), interpolation=cv2.INTER_NEAREST
                ),
            )
        for detection in detections:
            x1, y1, x2, y2 = (
                int(v * scale)
                for v in (
                    detection.bbox.x1,
                    detection.bbox.y1,
                    detection.bbox.x2,
                    detection.bbox.y2,
                )
            )
            cv2.rectangle(preview, (x1, y1), (x2, y2), (70, 220, 110), 2)
            cv2.putText(
                preview,
                f"{detection.kind.value} {detection.confidence:.0%}",
                (x1, max(16, y1 - 5)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (70, 220, 110),
                1,
                cv2.LINE_AA,
            )
        ok, encoded = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            raise ValueError("PREVIEW_ENCODING_FAILED")
        publish_preview(directory, encoded.tobytes())
        source = directory / row["input_name"]
        with source.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        report = {
            "schema_version": "1.0",
            "domain": "UPLOADED_IMAGE",
            "filename": row["filename"],
            "input_sha256": digest,
            "model": model.name,
            "width": width,
            "height": height,
            "processed_frames": 1,
            "counts": dict(counts),
            "objects": objects,
            "road_geometry": geometry,
            "signal_recommendation": recommendation,
            "road_segmentation": {
                "enabled": mask is not None,
                "model": segmenter.name if segmenter else None,
            },
            "physical_execution": False,
            "limits": [
                "Lane count requires calibrated geometry and is left unknown otherwise.",
                "Recommendations never actuate hardware.",
            ],
        }
        write_json(directory / "report.json", report)
        row.update(
            status="completed",
            stage="completed",
            progress_percent=100,
            processed_frames=1,
            total_frames=1,
            media_seconds=0,
            duration_seconds=0,
            width=width,
            height=height,
            objects=objects,
            counts=dict(counts),
            unique_tracks=len({d.track_id for d in detections if d.track_id is not None}),
            unique_classes=dict(counts),
            road_geometry=geometry,
            signal_recommendation=recommendation,
            preview_version=1,
            report_ready=True,
            explanations=[
                {"stage": "detect", "text": f"Neural detector found {len(detections)} objects"},
                {"stage": "geometry", "text": geometry["reason"]},
                {"stage": "signals", "text": recommendation["reason"]},
            ],
            timeline=[
                {
                    "media_seconds": 0,
                    "objects": len(detections),
                    "counts": dict(counts),
                    "stationary_candidates": 0,
                }
            ],
            heartbeat=time.monotonic(),
        )
        write_json(directory / "status.json", row)
    finally:
        model.close()
        if segmenter is not None:
            segmenter.close()

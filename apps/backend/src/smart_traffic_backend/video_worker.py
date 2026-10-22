"""Sequential full-frame analysis with measured progress; no camera or actuator access."""

import hashlib
import json
import logging
import math
import os
import time
import traceback
from collections import Counter, deque
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from traffic_vision.detector import Detector, YOLODetector
from traffic_vision.sources import VideoFrame

from smart_traffic_backend.config import Settings
from smart_traffic_backend.traffic_recommendation import (
    analyze_image,
    assess_geometry,
    recommend_signals,
)
from smart_traffic_backend.video_analysis import publish_preview, write_json


def analyze(directory: Path, settings: Settings, detector: Detector | None = None) -> None:
    row: dict[str, Any] = json.loads((directory / "status.json").read_text(encoding="utf-8"))
    if row.get("media_kind") == "image":
        analyze_image(directory, settings, detector)
        return
    source = directory / row["input_name"]
    capture = cv2.VideoCapture(str(source))
    profile = row.get("profile", "standard")
    cpu_threads = settings.video_cpu_threads or (
        min(6, max(1, (os.cpu_count() or 2) // 2))
        if profile in ("aerial", "aerial_precise")
        else settings.vision.torch_threads
    )
    offline_vision = settings.vision.model_copy(update={"torch_threads": cpu_threads})
    aerial_config = settings.video_aerial.model_copy(
        update={
            "rotated_pass": profile == "aerial_precise" or settings.video_aerial.rotated_pass,
            "confidence": min(settings.video_aerial.confidence, 0.15),
            # Keep close vehicles in frame; the default 0.10 cutoff drops cars
            # whose box is taller than a tenth of the short image dimension.
            "max_object_fraction": 0.3,
        }
    )
    if detector is not None:
        model = detector
    elif profile in ("aerial", "aerial_precise"):
        from traffic_vision.aerial import AerialDetector

        vision = offline_vision.model_copy(
            update={
                "model_path": settings.video_aerial_model_path,
                "manifest_path": settings.video_aerial_manifest_path,
                "model_package": None,
                "model_registry": None,
            }
        )
        model = AerialDetector(vision, aerial_config)
    else:
        model = YOLODetector(offline_vision)
    trace = None
    started = time.monotonic()
    seen: set[int] = set()
    unique_classes: Counter[str] = Counter()
    previous: dict[int, tuple[float, float, float, float]] = {}
    trails: dict[int, deque[tuple[int, int]]] = {}
    timeline: list[dict[str, Any]] = []
    detections_total = untracked_total = 0
    max_objects = 0
    inference_ms: list[float] = []
    last_publish = last_timeline = -1.0
    trace_bytes = trace_dropped = 0
    preview_skipped = 0
    road_model = None
    road_mask = None
    road_geometry: dict[str, Any] = {
        "status": "unavailable",
        "lane_count": None,
        "intersection_detected": False,
        "approaches": [],
        "lane_markings_detected": False,
        "reason": "Road segmentation was not enabled.",
    }
    road_last_media = -10.0
    road_inferences = 0
    preview_seconds = road_seconds = 0.0
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        width, height = (
            int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
            int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
        )
        if not capture.isOpened() or total <= 0 or not math.isfinite(fps) or fps <= 0:
            raise ValueError("VIDEO_METADATA_INVALID")
        if width <= 0 or height <= 0 or width * height > 3840 * 2160:
            raise ValueError("VIDEO_RESOLUTION_LIMIT")
        if total > settings.video_max_frames or total / fps > settings.video_max_duration_seconds:
            raise ValueError("VIDEO_DURATION_LIMIT")
        row.update(
            total_frames=total,
            duration_seconds=total / fps,
            source_fps=fps,
            width=width,
            height=height,
            stage="loading_model",
            heartbeat=time.monotonic(),
        )
        write_json(directory / "status.json", row)
        model.open()
        if row.get("road_segmentation_requested"):
            from traffic_vision.roads import RoadSegmenter

            row.update(stage="loading_roads", heartbeat=time.monotonic())
            write_json(directory / "status.json", row)
            road_model = RoadSegmenter(
                settings.video_road_model_directory,
                settings.video_road_manifest_path,
                aerial=profile in ("aerial", "aerial_precise"),
            )
            road_model.open()
        row.update(stage="analyzing", model=model.name, heartbeat=time.monotonic())
        write_json(directory / "status.json", row)
        trace = (directory / "frames.jsonl").open("x", encoding="utf-8")
        frame_index = 0
        last_tile_publish = 0.0

        def tile_progress(done: int, tiles: int) -> None:
            nonlocal last_tile_publish
            now = time.monotonic()
            if now - last_tile_publish < 0.5 and done != tiles:
                return
            row.update(
                current_frame=frame_index + 1,
                tiles_processed=done,
                tiles_per_frame=tiles,
                progress_percent=min(99.9, (frame_index + done / tiles) / total * 100),
                heartbeat=now,
            )
            write_json(directory / "status.json", row)
            last_tile_publish = now

        if hasattr(model, "on_tile_progress"):
            model.on_tile_progress = tile_progress
        while True:
            ok, image = capture.read()
            if not ok:
                if frame_index < total:
                    raise ValueError("VIDEO_DECODE_FAILED")
                break
            if frame_index >= settings.video_max_frames:
                raise ValueError("VIDEO_FRAME_LIMIT")
            if frame_index >= total:
                raise ValueError("VIDEO_METADATA_INVALID")
            if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3:
                raise ValueError("VIDEO_FRAME_INVALID")
            if image.shape[:2] != (height, width):
                raise ValueError("VIDEO_RESOLUTION_CHANGED")
            # Offline time is the original frame index/FPS. Wall processing speed
            # never changes motion estimates or drops frames to catch up with a clock.
            media = frame_index / fps
            packet = VideoFrame(
                np.asarray(image, dtype=np.uint8), media, frame_index, row["id"], time.monotonic()
            )
            inference_start = time.monotonic()
            detections = model.process(packet)
            inference_ms.append((time.monotonic() - inference_start) * 1000)
            counts: Counter[str] = Counter()
            objects: list[dict[str, Any]] = []
            stationary = 0
            for detection in detections:
                counts[detection.kind.value] += 1
                box = detection.bbox
                center = ((box.x1 + box.x2) / 2, (box.y1 + box.y2) / 2)
                identity = detection.track_id
                speed = stopped_seconds = None
                if identity is not None:
                    if identity not in seen:
                        if len(seen) >= settings.vision.analytics.max_session_tracks:
                            raise ValueError("VIDEO_TRACK_LIMIT")
                        seen.add(identity)
                        unique_classes[detection.kind.value] += 1
                    if identity in previous:
                        x, y, stamp, slow_since = previous[identity]
                        elapsed = media - stamp
                        if 0 < elapsed <= 1.5 / fps:
                            speed = math.hypot(center[0] - x, center[1] - y) / elapsed
                            # This is an image-motion diagnostic, not a calibrated road queue.
                            if speed > 3:
                                slow_since = media
                            stopped_seconds = max(0.0, media - slow_since)
                            stationary += int(stopped_seconds >= 2)
                        else:
                            slow_since = media
                            trails.pop(identity, None)
                    else:
                        slow_since = media
                    previous[identity] = (*center, media, slow_since)
                    trails.setdefault(identity, deque(maxlen=24)).append(
                        (int(center[0]), int(center[1]))
                    )
                else:
                    untracked_total += 1
                objects.append(
                    {
                        "track_id": identity,
                        "kind": detection.kind.value,
                        "confidence": detection.confidence,
                        "bbox": [box.x1, box.y1, box.x2, box.y2],
                        "speed_px_per_second": speed,
                        "slow_motion_seconds": stopped_seconds,
                    }
                )
            for identity, (_, _, stamp, _) in list(previous.items()):
                if media - stamp > 3:
                    del previous[identity]
                    trails.pop(identity, None)
            detections_total += len(detections)
            max_objects = max(max_objects, len(detections))
            frame_index += 1
            record = {
                "frame": frame_index - 1,
                "media_seconds": media,
                "counts": dict(counts),
                "objects": objects,
            }
            line = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
            size = len(line.encode("utf-8"))
            if trace_bytes + size <= 64 * 1024 * 1024:
                trace.write(line)
                trace_bytes += size
            else:
                trace_dropped += 1
            if media - last_timeline >= 1:
                timeline.append(
                    {
                        "media_seconds": media,
                        "objects": len(detections),
                        "counts": dict(counts),
                        "stationary_candidates": stationary,
                    }
                )
                last_timeline = media
            elapsed_wall = time.monotonic() - started
            row.update(
                processed_frames=frame_index,
                media_seconds=media,
                progress_percent=min(99.9, frame_index / total * 100),
                fps=frame_index / elapsed_wall,
                counts=dict(counts),
                visible_objects=len(detections),
                objects=objects[:100],
                unique_tracks=len(seen),
                unique_classes=dict(unique_classes),
                tiles_per_frame=getattr(model, "tiles_per_frame", 1),
                stationary_candidates=stationary,
                estimated_remaining_seconds=(total - frame_index) * elapsed_wall / frame_index,
                explanations=[
                    {"stage": "decode", "text": f"Decoded frame {frame_index} of {total}"},
                    {"stage": "detect", "text": f"Neural detector found {len(detections)} objects"},
                    {
                        "stage": "track",
                        "text": f"Matched {sum(d.track_id is not None for d in detections)} "
                        "detections to track IDs",
                    },
                    {
                        "stage": "motion",
                        "text": f"{stationary} tracks show low image motion for at least 2 seconds",
                    },
                ],
                timeline=timeline[-120:],
                heartbeat=time.monotonic(),
            )
            if time.monotonic() - last_publish >= 0.5 or frame_index == total:
                preview_start = time.monotonic()
                preview_scale = min(1.0, 1280 / width)
                preview = (
                    np.asarray(
                        cv2.resize(
                            image, (round(width * preview_scale), round(height * preview_scale))
                        ),
                        dtype=np.uint8,
                    )
                    if preview_scale < 1
                    else np.asarray(image.copy(), dtype=np.uint8)
                )
                if road_model is not None:
                    from traffic_vision.roads import overlay_roads

                    # Refresh on original video time, never reuse across a missing/failed model.
                    if road_mask is None or media - road_last_media >= 1 or frame_index == total:
                        road_start = time.monotonic()
                        road_mask = road_model.predict(np.asarray(image, dtype=np.uint8))
                        road_seconds += time.monotonic() - road_start
                        road_last_media = media
                        road_inferences += 1
                    preview_mask = np.asarray(
                        cv2.resize(
                            road_mask,
                            (preview.shape[1], preview.shape[0]),
                            interpolation=cv2.INTER_NEAREST,
                        ),
                        dtype=np.uint8,
                    )
                    preview = overlay_roads(preview, preview_mask)
                    row.update(
                        road_model=road_model.name,
                        road_method=road_model.method,
                        road_mask_media_seconds=road_last_media,
                        road_coverage_percent=float(np.count_nonzero(road_mask))
                        / road_mask.size
                        * 100,
                    )
                    road_geometry = assess_geometry(road_mask, width, height)
                    row["road_geometry"] = road_geometry
                for detection in detections:
                    box = detection.bbox
                    x1, y1, x2, y2 = (
                        int(value * preview_scale) for value in (box.x1, box.y1, box.x2, box.y2)
                    )
                    color = (70, 220, 110) if detection.track_id is not None else (0, 190, 255)
                    cv2.rectangle(preview, (x1, y1), (x2, y2), color, 2)
                    text = (
                        f"{detection.kind.value} #{detection.track_id or '?'} "
                        f"{detection.confidence:.0%}"
                    )
                    cv2.putText(
                        preview,
                        text,
                        (x1, max(16, y1 - 5)),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.45,
                        color,
                        1,
                        cv2.LINE_AA,
                    )
                    if (
                        detection.track_id is not None
                        and detection.track_id in trails
                        and len(trails[detection.track_id]) > 1
                    ):
                        points = (np.asarray(trails[detection.track_id]) * preview_scale).astype(
                            np.int32
                        )
                        cv2.polylines(preview, [points], False, color, 1)
                encoded_ok, encoded = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 80])
                if not encoded_ok:
                    raise ValueError("PREVIEW_ENCODING_FAILED")
                if publish_preview(directory, encoded.tobytes()):
                    row["preview_version"] = frame_index
                else:
                    preview_skipped += 1
                row["preview_skipped_updates"] = preview_skipped
                write_json(directory / "status.json", row)
                preview_seconds += time.monotonic() - preview_start
                last_publish = time.monotonic()
        row.update(stage="finalizing", heartbeat=time.monotonic())
        write_json(directory / "status.json", row)
        trace.close()
        trace = None
        with source.open("rb") as source_stream:
            input_hash = hashlib.file_digest(source_stream, "sha256").hexdigest()
        manifest = getattr(model, "manifest", None)
        report = {
            "schema_version": "1.0",
            "domain": "UPLOADED_RECORDED_VIDEO",
            "road_geometry": road_geometry,
            "signal_recommendation": recommend_signals(
                row.get("unique_classes", {}),
                bool(row.get("unique_classes", {}).get("pedestrian", 0)),
            ),
            "filename": row["filename"],
            "input_sha256": input_hash,
            "model": model.name,
            "road_segmentation": {
                "enabled": road_model is not None,
                "model": road_model.name if road_model else None,
                "revision": road_model.revision if road_model else None,
                "method": road_model.method if road_model else None,
                "inferences": road_inferences,
                "last_mask_media_seconds": road_last_media if road_model else None,
                "fine_tuned_on_uploaded_video": False,
            },
            "profile": profile,
            "aerial_settings": aerial_config.model_dump()
            if profile in ("aerial", "aerial_precise")
            else None,
            "tiles_per_frame": getattr(model, "tiles_per_frame", 1),
            "filtered_large_candidates": getattr(model, "filtered_large", 0),
            "filtered_edge_candidates": getattr(model, "filtered_edges", 0),
            "model_weights_sha256": getattr(manifest, "weights_hash", None),
            "total_frames": total,
            "processed_frames": frame_index,
            "dropped_frames": 0,
            "source_fps": fps,
            "duration_seconds": total / fps,
            "wall_seconds": time.monotonic() - started,
            "unique_tracks": len(seen),
            "unique_classes": dict(unique_classes),
            "detection_observations": detections_total,
            "untracked_observations": untracked_total,
            "peak_visible_objects": max_objects,
            "inference_p50_ms": float(np.percentile(inference_ms, 50)),
            "inference_p95_ms": float(np.percentile(inference_ms, 95)),
            "performance": {
                "detection_seconds": sum(inference_ms) / 1000,
                "road_seconds": road_seconds,
                "preview_seconds_including_roads": preview_seconds,
                "torch_threads": cpu_threads,
                "preview_width": min(width, 1280),
            },
            "timeline": timeline,
            "trace_dropped_records": trace_dropped,
            "preview_skipped_updates": preview_skipped,
            "physical_execution": False,
            "queue_mae": None,
            "road_delay_seconds": None,
            "limits": [
                "Lane count is not measurable from road segmentation alone; "
                "configure surveyed geometry or calibrated lane markings",
                "Recorded-media signal recommendations never actuate hardware",
                "Track count is not a calibrated traffic flow count",
                "Confidence is model confidence, not independently measured accuracy",
                "Low image motion is not a confirmed road queue; camera motion affects it",
                "Frame-index/FPS timestamps are nominal; variable-rate capture time is unverified",
                "No signal actuation or counterfactual traffic benefit measured",
            ],
        }
        write_json(directory / "report.json", report)
        row.update(
            status="completed",
            stage="completed",
            progress_percent=100,
            estimated_remaining_seconds=0,
            report_ready=True,
            road_geometry=road_geometry,
            signal_recommendation=report["signal_recommendation"],
            trace_dropped_records=trace_dropped,
            heartbeat=time.monotonic(),
        )
        write_json(directory / "status.json", row)
    finally:
        capture.release()
        model.close()
        if road_model is not None:
            road_model.close()
        if trace is not None:
            trace.close()


def run_video_job(directory: Path, settings: Settings) -> None:
    try:
        analyze(directory, settings)
    except Exception as exc:
        logging.getLogger(__name__).exception("Offline video job %s failed", directory.name)
        (directory / "worker-error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        row: dict[str, Any] = json.loads((directory / "status.json").read_text(encoding="utf-8"))
        error = (
            str(exc)
            if isinstance(exc, ValueError) and str(exc).startswith(("VIDEO_", "ROAD_", "IMAGE_"))
            else ("MODEL_MISSING" if isinstance(exc, FileNotFoundError) else "ANALYSIS_FAILED")
        )
        row.update(status="failed", stage="failed", error=error, heartbeat=time.monotonic())
        write_json(directory / "status.json", row)

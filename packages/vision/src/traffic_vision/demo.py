"""Headless recorded-video analytics demo: YOLO26 + ByteTrack + geometry + safety overlay."""

import argparse
import json
import logging
import time
from pathlib import Path

import cv2
from traffic_core.models import Policy
from traffic_core.observed_controller import ObservedTrafficController
from traffic_core.settings import Timing

from traffic_vision.analytics import TrafficAnalyzer
from traffic_vision.detector import Detector, SyntheticDetector, YOLODetector
from traffic_vision.geometry import load_geometry
from traffic_vision.models import PipelineStatus
from traffic_vision.overlay import DebugOverlay
from traffic_vision.settings import VisionSettings
from traffic_vision.sources import create_source


def run_demo(
    settings: VisionSettings,
    output: Path,
    frames: int = 120,
    stride: int = 1,
    synthetic_detector: bool = False,
    show: bool = False,
) -> dict[str, object]:
    if frames < 0 or stride < 1:
        raise ValueError("frames must be nonnegative and stride must be positive")
    geometry = load_geometry(settings.geometry_path)
    source = create_source(settings)
    detector: Detector = SyntheticDetector() if synthetic_detector else YOLODetector(settings)
    analyzer = TrafficAnalyzer(geometry, settings.analytics)
    overlay = DebugOverlay(geometry, detector.name + " / REPLAY")
    controller = ObservedTrafficController(Timing(), Policy.ADAPTIVE)
    output.parent.mkdir(parents=True, exist_ok=True)
    writer: cv2.VideoWriter | None = None
    processed = read = detections_total = 0
    peak_queue = 0
    maximum_wait = 0.0
    classes: set[str] = set()
    identities: set[str] = set()
    started = time.monotonic()
    last_analysis = None
    last_packet = None
    last_status = None
    preview_rank = (-1, -1.0, -1)
    try:
        detector.open()
        source.open()
        codec = "MJPG" if output.suffix.lower() == ".avi" else "mp4v"
        writer = cv2.VideoWriter(
            str(output),
            cv2.VideoWriter.fourcc(*codec),
            source.fps / stride,
            (overlay.width, overlay.height),
        )
        if not writer.isOpened():
            raise OSError(f"Could not create output video: {output}")
        started = time.monotonic()
        with output.with_suffix(".jsonl").open("w", encoding="utf-8") as telemetry:
            while frames == 0 or processed < frames:
                packet = source.read()
                if packet is None:
                    break
                read += 1
                if packet.frame_id % stride:
                    continue
                before = time.monotonic()
                detections = detector.process(packet)
                height, width = packet.frame.shape[:2]
                analysis = analyzer.process(
                    detections,
                    source_id=packet.source_id,
                    timestamp=packet.timestamp,
                    frame_id=packet.frame_id,
                    width=width,
                    height=height,
                    stream_epoch=packet.stream_epoch,
                )
                controller.advance(analyzer.to_core_state(analysis), packet.timestamp)
                processed += 1
                detections_total += len(detections)
                classes.update(d.kind.value for d in detections)
                identities.update(t.track_uid for t in analysis.tracks)
                peak_queue = max(
                    peak_queue, sum(m.queue_count for m in analysis.traffic.directions.values())
                )
                maximum_wait = max(
                    maximum_wait, max((t.current_wait_time for t in analysis.tracks), default=0)
                )
                status = PipelineStatus(
                    state="running",
                    captured=read,
                    inferred=processed,
                    analyzed=processed,
                    published=processed,
                    dropped_capture=0,
                    dropped_inference=0,
                    dropped_analysis=0,
                    processing_fps=processed / max(time.monotonic() - started, 1e-6),
                    latency_ms=(time.monotonic() - before) * 1000,
                    error=None,
                )
                image = overlay.render(
                    packet.frame, analysis, controller.safety.snapshot(packet.timestamp), status
                )
                writer.write(image)
                telemetry.write(analysis.model_dump_json() + "\n")
                last_analysis = analysis
                last_packet, last_status = packet, status
                rank = (len(analysis.tracks), maximum_wait, len(detections))
                if rank > preview_rank:
                    if not cv2.imwrite(str(output.with_suffix(".jpg")), image):
                        raise OSError("Could not write demo preview")
                    preview_rank = rank
                if processed % 30 == 0:
                    logging.getLogger(__name__).info(
                        "Processed %s frames, %s tracked IDs", processed, len(identities)
                    )
                if show:
                    cv2.imshow("Smart Traffic AI / Vision", image)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
                        break
        if processed == 0:
            raise RuntimeError("The source produced no processable frames")
        assert last_packet is not None and last_status is not None and last_analysis is not None
        terminal = overlay.render(
            last_packet.frame,
            last_analysis,
            controller.safety.fail_safe(last_packet.timestamp),
            last_status.model_copy(update={"state": "ended"}),
        )
        writer.write(terminal)
    finally:
        source.close()
        detector.close()
        if writer is not None:
            writer.release()
        if show:
            cv2.destroyAllWindows()
    report: dict[str, object] = {
        "detector": detector.name,
        "source_id": settings.source_id,
        "frames_read": read,
        "frames_processed": processed,
        "output_frames": processed + 1,
        "terminal_signal": "all_red",
        "detections": detections_total,
        "unique_track_ids": len(identities),
        "observed_classes": sorted(classes),
        "peak_queue": peak_queue,
        "maximum_observed_wait_seconds": maximum_wait,
        "processing_fps": processed / max(time.monotonic() - started, 1e-6),
        "duration_seconds": last_analysis.traffic.timestamp if last_analysis is not None else 0,
        "traffic": last_analysis.traffic.model_dump(mode="json")
        if last_analysis is not None
        else None,
        "output": str(output),
        "control": "replay safety controller; no hardware output",
    }
    output.with_suffix(".report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("file", "synthetic", "webcam", "rtsp"), default="file")
    parser.add_argument("--input", default="recordings/car-detection.mp4")
    parser.add_argument("--geometry", type=Path)
    parser.add_argument("--model", type=Path, default=Path("datasets/models/yolo26n.pt"))
    parser.add_argument("--output", type=Path, default=Path("recordings/vision-demo.mp4"))
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--show", action="store_true")
    parser.add_argument(
        "--synthetic-detector",
        action="store_true",
        help="Only for generated color-marker footage; never a fallback for YOLO",
    )
    args = parser.parse_args()
    geometry_path = args.geometry or Path(
        "configs/cameras/demo.json"
        if args.source == "synthetic"
        else "configs/cameras/intel-car.json"
    )
    geometry = load_geometry(geometry_path)
    settings = VisionSettings(
        source=args.source,
        uri=args.input,
        source_id=geometry.source_id,
        geometry_path=geometry_path,
        model_path=args.model,
        device=args.device,
        synthetic_frames=max(args.frames * args.stride, 150),
    )
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    report = run_demo(
        settings, args.output, args.frames, args.stride, args.synthetic_detector, args.show
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

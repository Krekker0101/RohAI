import cv2
import numpy as np
from numpy.typing import NDArray
from traffic_core.models import Lamp, SignalState

from traffic_vision.geometry import IntersectionGeometry, Polygon
from traffic_vision.models import AnalysisSnapshot, Lane, PipelineStatus
from traffic_vision.sources import Image

LANE_COLORS = {
    Lane.NORTH: (190, 230, 40),
    Lane.SOUTH: (60, 170, 255),
    Lane.EAST: (255, 165, 80),
    Lane.WEST: (240, 110, 200),
    Lane.UNKNOWN: (210, 220, 235),
}
INK = (235, 240, 247)
MUTED = (142, 159, 180)
SIGNAL_COLORS = {Lamp.GREEN: (110, 235, 80), Lamp.YELLOW: (50, 205, 255), Lamp.RED: (90, 90, 245)}


def text(
    image: Image,
    label: str,
    xy: tuple[int, int],
    size: float = 0.5,
    color: tuple[int, int, int] = INK,
    thickness: int = 1,
    shadow: bool = False,
) -> None:
    if shadow:
        cv2.putText(
            image,
            label,
            xy,
            cv2.FONT_HERSHEY_SIMPLEX,
            size,
            (15, 18, 22),
            thickness + 2,
            cv2.LINE_AA,
        )
    cv2.putText(image, label, xy, cv2.FONT_HERSHEY_SIMPLEX, size, color, thickness, cv2.LINE_AA)


class DebugOverlay:
    width = 1600
    height = 900

    def __init__(self, geometry: IntersectionGeometry, detector_name: str) -> None:
        self.geometry = geometry
        self.detector_name = detector_name

    def render(
        self, image: Image, snapshot: AnalysisSnapshot, signals: SignalState, status: PipelineStatus
    ) -> Image:
        canvas = np.full((self.height, self.width, 3), (20, 15, 11), dtype=np.uint8)
        cv2.rectangle(canvas, (0, 0), (1600, 78), (35, 27, 20), -1)
        cv2.rectangle(canvas, (20, 24), (26, 55), (180, 230, 45), -1)
        text(canvas, "SMART TRAFFIC", (42, 45), 0.86, thickness=2)
        text(canvas, "VISION INTELLIGENCE / " + self.detector_name, (360, 43), 0.48, MUTED)
        text(canvas, f"{status.processing_fps:.1f} FPS", (1220, 32), 0.63, (190, 230, 50), 2)
        text(canvas, f"LATENCY {status.latency_ms:.0f} ms", (1370, 32), 0.43, MUTED)
        text(
            canvas,
            f"SOURCE {snapshot.traffic.source_id[:26]}   FRAME {snapshot.traffic.frame_id:06d}",
            (1220, 57),
            0.39,
            MUTED,
        )
        source_height, source_width = image.shape[:2]
        ratio = min(1160 / source_width, 714 / source_height)
        w, h = round(source_width * ratio), round(source_height * ratio)
        ox, oy = 20 + (1160 - w) // 2, 98 + (714 - h) // 2
        canvas[oy : oy + h, ox : ox + w] = cv2.resize(image, (w, h))

        def polygon_points(polygon: Polygon) -> NDArray[np.int32]:
            return np.asarray(
                [(ox + p.x * w, oy + p.y * h) for p in polygon.points], dtype=np.int32
            )

        tint = canvas.copy()
        for approach in self.geometry.approaches:
            if not approach.enabled:
                continue
            color = LANE_COLORS[Lane(approach.direction.value)]
            incoming = polygon_points(approach.incoming)
            cv2.fillPoly(tint, [incoming], color)
        cv2.addWeighted(tint, 0.12, canvas, 0.88, 0, dst=canvas)
        cv2.polylines(
            canvas, [polygon_points(self.geometry.detection_zone)], True, MUTED, 1, cv2.LINE_AA
        )
        for approach in self.geometry.approaches:
            if not approach.enabled:
                continue
            color = LANE_COLORS[Lane(approach.direction.value)]
            cv2.polylines(canvas, [polygon_points(approach.incoming)], True, color, 2, cv2.LINE_AA)
            cv2.polylines(
                canvas, [polygon_points(approach.queue_zone)], True, (50, 190, 250), 1, cv2.LINE_AA
            )
            start, end = approach.stop_line.start, approach.stop_line.end
            cv2.line(
                canvas,
                (round(ox + start.x * w), round(oy + start.y * h)),
                (round(ox + end.x * w), round(oy + end.y * h)),
                color,
                4,
                cv2.LINE_AA,
            )
            if approach.pedestrian_crossing is not None:
                cv2.polylines(
                    canvas, [polygon_points(approach.pedestrian_crossing)], True, (225, 210, 225), 2
                )
        for track in snapshot.tracks:
            color = (30, 185, 255) if track.queued else LANE_COLORS[track.lane]
            x1, y1 = round(ox + track.bbox.x1 * ratio), round(oy + track.bbox.y1 * ratio)
            x2, y2 = round(ox + track.bbox.x2 * ratio), round(oy + track.bbox.y2 * ratio)
            cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 1, cv2.LINE_AA)
            for x, y, dx, dy in (
                (x1, y1, 1, 1),
                (x2, y1, -1, 1),
                (x1, y2, 1, -1),
                (x2, y2, -1, -1),
            ):
                cv2.line(canvas, (x, y), (x + 10 * dx, y), color, 3)
                cv2.line(canvas, (x, y), (x, y + 10 * dy), color, 3)
            label = f"#{track.track_id} {track.kind.value.upper()} {track.confidence:.0%}"
            label_y = max(oy + 14, y1 - 8)
            label_width = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.38, 1)[0][0]
            label_x = min(x1, max(ox, ox + w - label_width - 4))
            text(canvas, label, (label_x, label_y), 0.38, color, 1, shadow=True)
            speed = (
                "--" if track.speed_px_per_second is None else f"{track.speed_px_per_second:.1f}"
            )
            text(
                canvas,
                f"{track.lane.value[:1].upper()}  {speed}px/s  WAIT {track.current_wait_time:.1f}s",
                (x1, min(oy + h - 5, y2 + 15)),
                0.33,
                color,
                shadow=True,
            )
            points = np.asarray(
                [(ox + p.point.x * w, oy + p.point.y * h) for p in track.trajectory], dtype=np.int32
            )
            if len(points) > 1:
                cv2.polylines(canvas, [points], False, color, 1, cv2.LINE_AA)
        lamps = signals.lamps()
        for index, (direction, metrics) in enumerate(snapshot.traffic.directions.items()):
            y = 96 + index * 177
            cv2.rectangle(canvas, (1202, y), (1578, y + 160), (38, 30, 23), -1)
            color = LANE_COLORS[Lane(direction)]
            cv2.rectangle(canvas, (1202, y), (1206, y + 160), color, -1)
            text(canvas, direction.upper() + " INCOMING", (1220, y + 26), 0.49, color, 2)
            cv2.circle(canvas, (1552, y + 22), 8, SIGNAL_COLORS[lamps[direction]], -1, cv2.LINE_AA)
            if self.geometry.approach(Lane(direction)) is None:
                text(canvas, "NOT IN CAMERA VIEW", (1220, y + 80), 0.55, MUTED)
                text(canvas, "No traffic observation", (1220, y + 110), 0.43, MUTED)
                continue
            text(canvas, str(metrics.queue_count), (1220, y + 74), 1.3, INK, 2)
            text(canvas, "QUEUED", (1286, y + 69), 0.43, MUTED)
            text(canvas, f"SCORE {metrics.traffic_score:.1f}", (1400, y + 63), 0.5, color)
            text(
                canvas,
                f"LENGTH {metrics.estimated_queue_length:.1f}{metrics.queue_length_unit}",
                (1220, y + 98),
                0.43,
            )
            text(canvas, f"WAIT {metrics.average_wait_seconds:.1f}s", (1430, y + 98), 0.43)
            text(
                canvas,
                f"C {metrics.car_count}  B {metrics.bus_count}  T {metrics.truck_count}  "
                f"M {metrics.motorcycle_count}  BI {metrics.bicycle_count}  "
                f"P {metrics.pedestrian_count}",
                (1220, y + 121),
                0.4,
                MUTED,
            )
            cv2.rectangle(canvas, (1220, y + 137), (1555, y + 141), (65, 54, 43), -1)
            cv2.rectangle(
                canvas,
                (1220, y + 137),
                (1220 + round(335 * metrics.queue_confidence), y + 141),
                color,
                -1,
            )
        cv2.rectangle(canvas, (20, 826), (1578, 880), (38, 30, 23), -1)
        stage_lamp = {
            "green": Lamp.GREEN,
            "yellow": Lamp.YELLOW,
        }.get(signals.stage.value, Lamp.RED)
        stage_color = SIGNAL_COLORS[stage_lamp]
        text(
            canvas,
            f"ACTIVE SIGNAL  {signals.phase.value.upper()} / {signals.stage.value.upper()}",
            (38, 859),
            0.55,
            stage_color,
            2,
        )
        dropped = status.dropped_capture + status.dropped_inference + status.dropped_analysis
        text(
            canvas,
            f"T+{snapshot.traffic.timestamp:07.2f}s   "
            f"UNIQUE VEHICLES {snapshot.traffic.unique_vehicle_arrivals}"
            f"   DROPPED {dropped}   {status.state.upper()}",
            (890, 859),
            0.43,
            MUTED,
        )
        return canvas

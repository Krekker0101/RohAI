from __future__ import annotations

import asyncio
import html
import threading
from collections.abc import AsyncGenerator
from contextlib import suppress
from typing import TYPE_CHECKING

from traffic_core.models import ApproachState, Direction, ObjectKind, Policy, Telemetry
from traffic_simulator.engine import SimulationEngine
from traffic_simulator.simulator import Scenario
from traffic_vision.contracts import BoundingBox
from traffic_vision.models import (
    DirectionMetrics,
    Lane,
    PipelineStatus,
    TrackSnapshot,
    VisionTrafficState,
)

from smart_traffic_backend.vision_runtime import VisionTelemetry

if TYPE_CHECKING:
    from smart_traffic_backend.config import Settings


DEMO_SOURCE_ID = "demo"


class DemoRuntime:
    """Presentation-only runtime isolated from real camera and hardware.

    The traffic/signal decisions are produced by the same SimulationEngine,
    TrafficController and SafetyController as the normal backend. Only the
    observations are synthesized so the full dashboard can be demonstrated
    without a camera, model download or road footage.
    """

    def __init__(self, settings: Settings) -> None:
        # Keep demo parameters independent from production settings so a broken or
        # empty real source never changes what the presenter sees.
        timing = settings.timing
        scenario = Scenario(
            seed=2026,
            north_per_minute=30,
            south_per_minute=22,
            east_per_minute=8,
            west_per_minute=6,
            pedestrians_per_minute=7,
            vehicle_headway_seconds=1.6,
            pedestrian_headway_seconds=0.45,
            moderate_queue=4,
            high_queue=10,
        )
        self.engine = SimulationEngine(scenario, timing, Policy.ADAPTIVE)
        self.tick_seconds = max(0.1, min(float(settings.tick_seconds), 0.5))
        self.speed = 2.0
        self.task: asyncio.Task[None] | None = None
        self.failure: str | None = None
        self.latest: VisionTelemetry = self._snapshot(self.engine.snapshot())
        self.subscribers: set[asyncio.Queue[VisionTelemetry]] = set()
        self.comparison_lock = threading.Lock()

    @property
    def ready(self) -> bool:
        return self.task is not None and not self.task.done() and self.failure is None

    @property
    def policy(self) -> Policy:
        return self.engine.controller.policy

    async def start(self) -> None:
        self.task = asyncio.create_task(self._run(), name="presentation-demo")

    async def _run(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.tick_seconds)
                telemetry = self.engine.step(self.tick_seconds * self.speed)
                self.latest = self._snapshot(telemetry)
                self._publish()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.failure = "demo_runtime_failure"
            # Do not re-raise: the real runtime must remain available even if the
            # optional presentation channel has a fault.
            self._publish()

    def set_policy(self, policy: Policy) -> None:
        self.engine.controller.policy = policy
        self.latest = self._snapshot(self.engine.snapshot())
        self._publish()

    def set_emergency(self, direction: Direction, ttl_seconds: float) -> None:
        from traffic_core.models import phase_for

        self.engine.set_emergency(phase_for(direction), ttl_seconds)
        self.latest = self._snapshot(self.engine.snapshot())
        self._publish()

    def clear_emergency(self) -> None:
        self.engine.clear_emergency()
        self.latest = self._snapshot(self.engine.snapshot())
        self._publish()

    def _publish(self) -> None:
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(self.latest)

    async def stream(self) -> AsyncGenerator[VisionTelemetry, None]:
        queue: asyncio.Queue[VisionTelemetry] = asyncio.Queue(maxsize=1)
        self.subscribers.add(queue)
        queue.put_nowait(self.latest)
        try:
            while True:
                yield await queue.get()
        finally:
            self.subscribers.discard(queue)

    async def stop(self) -> None:
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
        self.task = None

    def _snapshot(self, telemetry: Telemetry) -> VisionTelemetry:
        traffic = telemetry.traffic
        pedestrian_counts = [traffic.pedestrians_waiting // 4] * 4
        for i in range(traffic.pedestrians_waiting % 4):
            pedestrian_counts[i] += 1

        directions: dict[str, DirectionMetrics] = {}
        tracks: list[TrackSnapshot] = []
        for lane_index, approach in enumerate(traffic.approaches):
            counts = approach.counts
            directions[approach.direction.value] = DirectionMetrics(
                car_count=counts.cars,
                bus_count=counts.buses,
                truck_count=counts.trucks,
                motorcycle_count=counts.motorcycles,
                bicycle_count=counts.bicycles,
                pedestrian_count=pedestrian_counts[lane_index],
                queue_count=approach.queue_length,
                queue_vehicle_count=approach.queue_length,
                estimated_queue_length=approach.queue_length * 6.2,
                queue_length_unit="m",
                queue_confidence=0.96 if approach.queue_length else 1.0,
                average_wait_seconds=approach.mean_wait_seconds,
                max_wait_seconds=approach.oldest_wait_seconds,
                arrival_rate=approach.arrival_rate_per_minute,
                unique_arrivals=max(
                    0,
                    int(approach.arrival_rate_per_minute * traffic.simulation_time / 60),
                ),
                traffic_score=approach.traffic_score,
                congestion=approach.congestion.value,
            )
            tracks.extend(self._tracks_for(approach, lane_index, traffic.simulation_time))

        # Add a few pedestrian observations so the tracking table is visibly alive.
        for index in range(min(traffic.pedestrians_waiting, 6)):
            tracks.append(
                TrackSnapshot(
                    track_id=9000 + index,
                    track_uid=f"demo-ped-{index}",
                    kind=ObjectKind.PEDESTRIAN,
                    confidence=0.91,
                    bbox=BoundingBox(x1=590 + index * 18, y1=315, x2=604 + index * 18, y2=354),
                    lane=Lane.UNKNOWN,
                    first_seen=max(
                        0.0,
                        traffic.simulation_time - traffic.pedestrian_oldest_wait_seconds,
                    ),
                    last_seen=traffic.simulation_time,
                    trajectory=(),
                    speed_px_per_second=0.0,
                    waiting_started_at=max(
                        0.0,
                        traffic.simulation_time - traffic.pedestrian_oldest_wait_seconds,
                    ),
                    current_wait_time=traffic.pedestrian_oldest_wait_seconds,
                    total_observed_wait_seconds=traffic.pedestrian_oldest_wait_seconds,
                    consecutive_slow_frames=8,
                    queued=True,
                    queue_confidence=0.94,
                )
            )

        frame_id = telemetry.sequence
        vision = VisionTrafficState(
            timestamp=traffic.simulation_time,
            source_id=DEMO_SOURCE_ID,
            stream_epoch=0,
            frame_id=frame_id,
            directions=directions,
            unknown_count=0,
            unique_vehicle_arrivals=telemetry.kpi.arrived,
            unique_pedestrian_arrivals=max(0, int(traffic.simulation_time * 7 / 60)),
        )
        pipeline = PipelineStatus(
            state="running" if self.failure is None else "failed",
            captured=frame_id * 3,
            inferred=frame_id * 3,
            analyzed=frame_id * 3,
            published=frame_id * 3,
            dropped_capture=0,
            dropped_inference=0,
            dropped_analysis=0,
            processing_fps=15.0,
            latency_ms=28.0 + (frame_id % 7) * 1.7,
            error=self.failure,
        )
        return VisionTelemetry(
            sequence=telemetry.sequence,
            mode="demo",
            traffic=vision,
            tracks=tuple(tracks),
            signals=telemetry.signals,
            lamps=telemetry.lamps,
            decision=telemetry.decision,
            green_target_seconds=telemetry.green_target_seconds,
            emergency_phase=telemetry.emergency_phase,
            pipeline=pipeline,
            failure=self.failure,
        )

    @staticmethod
    def _tracks_for(approach: ApproachState, lane_index: int, now: float) -> list[TrackSnapshot]:
        counts = approach.counts
        kinds: list[ObjectKind] = []
        kinds.extend([ObjectKind.CAR] * counts.cars)
        kinds.extend([ObjectKind.BUS] * counts.buses)
        kinds.extend([ObjectKind.TRUCK] * counts.trucks)
        kinds.extend([ObjectKind.MOTORCYCLE] * counts.motorcycles)
        kinds.extend([ObjectKind.BICYCLE] * counts.bicycles)
        lane = Lane(approach.direction.value)
        average_wait = float(approach.mean_wait_seconds)
        maximum_wait = float(approach.oldest_wait_seconds)
        lane_origins = ((575, 50), (690, 610), (1040, 290), (90, 395))
        base_x, base_y = lane_origins[lane_index]
        result: list[TrackSnapshot] = []
        for index, kind in enumerate(kinds[:18]):
            row, column = divmod(index, 2)
            if lane_index == 0:
                x, y = base_x + column * 52, base_y + row * 54
            elif lane_index == 1:
                x, y = base_x - column * 52, base_y - row * 54
            elif lane_index == 2:
                x, y = base_x - row * 72, base_y + column * 48
            else:
                x, y = base_x + row * 72, base_y - column * 48
            width = 34 if kind not in (ObjectKind.BUS, ObjectKind.TRUCK) else 46
            height = 58 if lane_index < 2 else 34
            wait = min(maximum_wait, average_wait + index * 0.6)
            track_id = (lane_index + 1) * 1000 + index
            result.append(
                TrackSnapshot(
                    track_id=track_id,
                    track_uid=f"demo-{lane.value}-{index}",
                    kind=kind,
                    confidence=max(0.82, 0.97 - index * 0.006),
                    bbox=BoundingBox(
                        x1=max(0, x),
                        y1=max(0, y),
                        x2=max(1, x + width),
                        y2=max(1, y + height),
                    ),
                    lane=lane,
                    first_seen=max(0.0, now - wait),
                    last_seen=now,
                    trajectory=(),
                    speed_px_per_second=0.0,
                    waiting_started_at=max(0.0, now - wait),
                    current_wait_time=wait,
                    total_observed_wait_seconds=wait,
                    consecutive_slow_frames=12,
                    queued=True,
                    queue_confidence=0.96,
                )
            )
        return result

    def frame_svg(self) -> bytes:
        snapshot = self.latest
        traffic = snapshot.traffic
        if traffic is None:
            return b"<svg xmlns='http://www.w3.org/2000/svg' width='1280' height='720'></svg>"

        phase = snapshot.signals.phase.value
        stage = snapshot.signals.stage.value
        boxes: list[str] = []
        for track in snapshot.tracks[:36]:
            box = track.bbox
            color = "#66f2a4" if track.queued else "#75b9ff"
            label = html.escape(f"#{track.track_id} {track.kind.value} {track.confidence:.2f}")
            width = box.x2 - box.x1
            height = box.y2 - box.y1
            label_y = max(0, box.y1 - 20)
            text_y = max(14, box.y1 - 6)
            boxes.append(
                "<g>"
                f"<rect x='{box.x1:.0f}' y='{box.y1:.0f}' width='{width:.0f}' "
                f"height='{height:.0f}' fill='none' stroke='{color}' stroke-width='3'/>"
                f"<rect x='{box.x1:.0f}' y='{label_y:.0f}' width='145' height='20' "
                f"fill='{color}' opacity='.92'/>"
                f"<text x='{box.x1 + 5:.0f}' y='{text_y:.0f}' font-family='Arial' "
                f"font-size='12' fill='#0b1813'>{label}</text>"
                "</g>"
            )

        queue_lines: list[str] = []
        y = 102
        for key in ("north", "south", "east", "west"):
            metric = traffic.directions[key]
            queue_lines.append(
                f"<text x='36' y='{y}' font-family='Arial' font-size='18' "
                f"fill='#dbe8df'>{key.upper()}: queue {metric.queue_count} · "
                f"score {metric.traffic_score:.1f}</text>"
            )
            y += 30

        light_summary = " · ".join(
            f"{name[0].upper()}:{lamp.value[0].upper()}"
            for name, lamp in snapshot.lamps.items()
        )
        safe_phase = html.escape(phase)
        safe_stage = html.escape(stage)
        safe_lights = html.escape(light_summary)
        svg = (
            "<svg xmlns='http://www.w3.org/2000/svg' width='1280' height='720' "
            "viewBox='0 0 1280 720'>"
            "<defs><linearGradient id='bg' x1='0' x2='1'>"
            "<stop stop-color='#101915'/><stop offset='1' stop-color='#18231e'/>"
            "</linearGradient></defs>"
            "<rect width='1280' height='720' fill='url(#bg)'/>"
            "<rect x='470' y='0' width='340' height='720' fill='#303a36'/>"
            "<rect x='0' y='250' width='1280' height='220' fill='#303a36'/>"
            "<path d='M640 0V720M0 360H1280' stroke='#85918b' stroke-width='2' "
            "stroke-dasharray='24 22' opacity='.55'/>"
            "<g stroke='#eef3ef' stroke-width='8' opacity='.72'>"
            "<path d='M485 240h310'/><path d='M485 480h310'/>"
            "<path d='M455 265v190'/><path d='M825 265v190'/></g>"
            "<rect x='20' y='20' width='390' height='225' rx='14' fill='#07110d' "
            "opacity='.82'/>"
            "<text x='36' y='58' font-family='Arial' font-size='22' font-weight='700' "
            "fill='#7df2ac'>PRESENTATION DEMO · AI OVERLAY</text>"
            f"{''.join(queue_lines)}"
            "<text x='36' y='224' font-family='Arial' font-size='15' fill='#9fb1a7'>"
            "Synthetic observations · real Decision + Safety logic</text>"
            "<rect x='845' y='20' width='415' height='122' rx='14' fill='#07110d' "
            "opacity='.82'/>"
            "<text x='868' y='55' font-family='Arial' font-size='17' fill='#9fb1a7'>"
            "CONTROL STATE</text>"
            "<text x='868' y='85' font-family='Arial' font-size='24' font-weight='700' "
            f"fill='#ffffff'>{safe_phase} · {safe_stage}</text>"
            "<text x='868' y='116' font-family='Arial' font-size='15' fill='#7df2ac'>"
            f"{safe_lights}</text>"
            f"{''.join(boxes)}"
            "<rect x='20' y='668' width='1240' height='32' rx='8' fill='#07110d' "
            "opacity='.82'/>"
            "<text x='36' y='690' font-family='Arial' font-size='14' fill='#dbe8df'>"
            "DEMO DATA — not a real camera feed. Switch to REAL for YOLO / webcam / RTSP."
            "</text></svg>"
        )
        return svg.encode("utf-8")

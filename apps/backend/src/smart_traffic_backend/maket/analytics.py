"""Maket event accounting at analytics cadence; first-seen IDs are not arrivals."""

from collections import deque
from collections.abc import Callable
from typing import Any

from traffic_vision.analytics import TrafficAnalyzer
from traffic_vision.geometry import IntersectionGeometry
from traffic_vision.models import AnalysisSnapshot
from traffic_vision.settings import AnalyticsSettings

from smart_traffic_backend.maket.config import MaketCalibration
from smart_traffic_backend.maket.metrics import MaketEvents


class MaketAnalyzer(TrafficAnalyzer):
    def __init__(
        self,
        geometry: IntersectionGeometry,
        settings: AnalyticsSettings,
        calibration: MaketCalibration | None,
    ) -> None:
        super().__init__(geometry, settings)
        self.calibration = calibration
        self.events = MaketEvents()
        self.latest_metrics: dict[str, Any] = {}
        self.event_sink: Callable[[dict[str, Any]], None] | None = None
        self.entry_times: dict[str, deque[float]] = {
            a.direction.value: deque(maxlen=10000) for a in geometry.approaches
        }
        self.entry_counts = dict.fromkeys(self.entry_times, 0)
        self.event_epoch: int | None = None
        self.event_started: float | None = None

    def process(self, *args: Any, **kwargs: Any) -> AnalysisSnapshot:
        result = super().process(*args, **kwargs)
        if self.calibration is None:
            return result
        now = result.traffic.timestamp
        if self.event_epoch != result.traffic.stream_epoch:
            self.event_epoch = result.traffic.stream_epoch
            self.event_started = now
            self.entry_counts = dict.fromkeys(self.entry_times, 0)
            for history in self.entry_times.values():
                history.clear()
        self.latest_metrics = self.events.update(result, self.calibration)
        for event in self.latest_metrics["events"]:
            if event["event"] == "entry":
                self.entry_times[event["approach"]].append(now)
                self.entry_counts[event["approach"]] += 1
            if self.event_sink:
                self.event_sink(event)
        window = min(now - (self.event_started or 0), 60)
        directions = {}
        for direction, metric in result.traffic.directions.items():
            stamps = self.entry_times[direction]
            while stamps and now - stamps[0] > 60:
                stamps.popleft()
            data = metric.model_dump()
            if metric.observation.reliable:
                data.update(
                    unique_arrivals=self.entry_counts[direction],
                    arrival_rate=len(stamps) * 60 / window if window > 0 else 0,
                )
            directions[direction] = type(metric).model_validate(data)
        result = result.model_copy(
            update={
                "traffic": result.traffic.model_copy(
                    update={
                        "directions": directions,
                        "unique_vehicle_arrivals": sum(self.entry_counts.values()),
                    }
                )
            }
        )
        self._latest = result
        return result

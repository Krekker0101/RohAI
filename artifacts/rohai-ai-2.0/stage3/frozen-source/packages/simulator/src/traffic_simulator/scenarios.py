"""Small declarative catalog; rates and noise are explicit engineering hypotheses."""

from pathlib import Path

from traffic_core.models import Direction

from traffic_simulator.twin_models import (
    CameraFault,
    DemandPeriod,
    Incident,
    PerceptionProfile,
    ScenarioDefinition,
    TrafficProfile,
)


def catalog(seed: int = 42, duration_s: float = 180) -> dict[str, ScenarioDefinition]:
    def period(
        start: float, end: float, rates: tuple[float, float, float, float], peds: float = 6
    ) -> DemandPeriod:
        return DemandPeriod(
            start_s=start,
            end_s=end,
            vehicles_per_minute=dict(zip(Direction, rates, strict=True)),
            pedestrians_per_minute=peds,
        )

    result = {}
    for name, rates, peds in (
        ("balanced", (18, 18, 18, 18), 6),
        ("NS-heavy", (45, 40, 8, 8), 6),
        ("EW-heavy", (8, 8, 45, 40), 6),
        ("saturated", (120, 120, 120, 120), 12),
        ("pedestrian-heavy", (15, 15, 15, 15), 70),
        ("burst-traffic", (24, 24, 24, 24), 6),
        ("night-degraded-vision", (18, 18, 18, 18), 6),
        ("partial-camera-failure", (18, 18, 18, 18), 6),
        ("camera-outage", (18, 18, 18, 18), 6),
        ("queue-spillback", (90, 90, 25, 25), 6),
        ("rapid-demand-change", (45, 40, 8, 8), 6),
    ):
        periods: tuple[DemandPeriod, ...] = (period(0, duration_s, rates, peds),)
        if name == "rapid-demand-change":
            periods = (
                period(0, duration_s / 2, rates),
                period(duration_s / 2, duration_s, (8, 8, 45, 40)),
            )
        traffic = TrafficProfile(periods=periods)
        perception = PerceptionProfile()
        if name == "burst-traffic":
            traffic = traffic.model_copy(update={"arrival_distribution": "bursty"})
        if name == "queue-spillback":
            traffic = traffic.model_copy(
                update={
                    "lane_length_m": 26,
                    "downstream_capacity": 2,
                    "incidents": (
                        Incident(
                            start_s=duration_s / 4,
                            end_s=duration_s * 0.75,
                            downstream=Direction.SOUTH,
                        ),
                    ),
                }
            )
        if name == "night-degraded-vision":
            perception = PerceptionProfile(
                missed_detection_probability=0.15,
                position_noise_std_m=0.4,
                occlusion_probability=0.04,
                queue_undercount_probability=0.08,
                inference_latency_s=0.3,
                fps=2,
            )
        if name in ("camera-outage", "partial-camera-failure"):
            perception = perception.model_copy(
                update={
                    "faults": (
                        CameraFault(
                            start_s=duration_s / 3,
                            end_s=duration_s * 0.75,
                            mode="outage",
                            directions=(Direction.NORTH,)
                            if name == "partial-camera-failure"
                            else tuple(Direction),
                        ),
                    )
                }
            )
        result[name] = ScenarioDefinition(
            scenario_id=name,
            seed=seed,
            duration_s=duration_s,
            traffic=traffic,
            perception=perception,
        )
    return result


def load_scenario(path: Path) -> ScenarioDefinition:
    return ScenarioDefinition.model_validate_json(path.read_text(encoding="utf-8"))

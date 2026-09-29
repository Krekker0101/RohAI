"""Closed-loop dynamics and observation contracts; no controller/model training."""

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from traffic_core.models import Direction, ObjectKind, Phase, Policy, SignalStage, SignalState
from traffic_core.settings import Timing
from traffic_simulator.environment import (
    DigitalTwinEnvironment,
    FastTrafficEnvironment,
    ObservationControllerPort,
    SumoTrafficEnvironment,
    TrafficEnvironment,
)
from traffic_simulator.scenarios import catalog, load_scenario
from traffic_simulator.simulator import Scenario
from traffic_simulator.twin_dynamics import TwinDynamics, arrival_tape, realize
from traffic_simulator.twin_evaluation import paired_comparison
from traffic_simulator.twin_models import (
    CameraFault,
    DemandPeriod,
    DomainRandomizationProfile,
    GroundTruthState,
    Incident,
    ObservedTrafficState,
    PerceptionProfile,
    ScenarioDefinition,
    TrafficProfile,
    TruthEntity,
)
from traffic_simulator.twin_observation import ObservationModel


def clean(**changes: Any) -> PerceptionProfile:
    return PerceptionProfile.model_validate(
        {
            "missed_detection_probability": 0,
            "false_positives_per_frame": 0,
            "id_switch_probability": 0,
            "occlusion_probability": 0,
            "position_noise_std_m": 0,
            "wait_noise_std_s": 0,
            "queue_undercount_probability": 0,
            "camera_delay_s": 0,
            "inference_latency_s": 0,
            "frame_drop_probability": 0,
            "calibration_scale_error": 0,
            **changes,
        }
    )


def scenario(**traffic_changes: Any) -> ScenarioDefinition:
    traffic = TrafficProfile.model_validate(
        {
            "periods": [
                DemandPeriod(
                    start_s=0,
                    end_s=60,
                    vehicles_per_minute={d: 60 if d == Direction.NORTH else 0 for d in Direction},
                    pedestrians_per_minute=0,
                ).model_dump(mode="json")
            ],
            "arrival_distribution": "regular",
            "lanes_per_approach": 1,
            "turn_probabilities": (0, 1, 0),
            "vehicle_probabilities": (1, 0, 0, 0, 0),
            **traffic_changes,
        }
    )
    return ScenarioDefinition(
        scenario_id="contract-test", seed=77, duration_s=60, traffic=traffic, perception=clean()
    )


def signal(phase: Phase = Phase.NS, stage: SignalStage = SignalStage.GREEN) -> SignalState:
    return SignalState(phase=phase, stage=stage, elapsed_seconds=0)


def truth(
    at: float, *, arrived: float = 0, admitted: float = 0, kind: ObjectKind = ObjectKind.CAR
) -> GroundTruthState:
    return GroundTruthState(
        timestamp_s=at,
        arrived=1,
        queued=(
            TruthEntity(
                entity_id=123456,
                kind=kind,
                approach=Direction.NORTH,
                lane_id="north-0",
                arrived_at_s=arrived,
                admitted_at_s=admitted,
                distance_to_stop_m=10,
                length_m=4.5,
            ),
        ),
    )


def test_same_seed_reproducible_and_policy_actions_change_future() -> None:
    red, green, repeat = (TwinDynamics(scenario()) for _ in range(3))
    assert red.tape_sha256 == green.tape_sha256 == repeat.tape_sha256
    for _ in range(240):
        red.advance(signal(stage=SignalStage.ALL_RED))
        a = green.advance(signal())
        assert a == repeat.advance(signal())
        assert all(red.invariants().values()) and all(green.invariants().values())
    assert red.departed == 0 < green.departed
    assert red.total_wait_s > green.total_wait_s
    assert len(red.ground_truth().upstream) > 0


def test_observation_is_not_latent_state_or_ideal_fast_snapshot() -> None:
    sensor = ObservationModel(clean(), 42)
    sensor.ingest(truth(20, arrived=0, admitted=1))
    observed = sensor.observe(20)
    assert observed.tracks[0].observed_wait_s == 0  # Not twenty seconds of hidden history.
    assert observed.traffic.approaches[0].arrival_rate_per_minute == 0
    assert observed.tracks[0].track_id != "123456"
    assert "arrived_at_s" not in observed.model_dump_json()
    assert "entity_id" not in observed.model_dump_json()
    assert observed.traffic.approaches[0].observation.source_camera_ids != ("simulator",)
    port = ObservationControllerPort(Timing(), Policy.ADAPTIVE)
    with pytest.raises(TypeError):
        port.act(truth(20))  # type: ignore[arg-type]
    fast = FastTrafficEnvironment(Scenario())
    assert isinstance(fast, TrafficEnvironment)
    with pytest.raises(TypeError):
        port.act(fast.simulator.snapshot())  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="Ideal"):
        port.act(ObservedTrafficState(traffic=fast.simulator.snapshot()))


def test_camera_delay_is_causal_and_tracks_do_not_see_future() -> None:
    sensor = ObservationModel(clean(camera_delay_s=0.5, inference_latency_s=0.5), 1)
    sensor.ingest(truth(0))
    assert sensor.observe(0).traffic.approaches[0].queue_length is None
    sensor.ingest(GroundTruthState(timestamp_s=0.5, queued=()))
    seen = sensor.observe(1)
    assert seen.traffic.approaches[0].queue_length == 1
    assert seen.traffic.approaches[0].observation.measurement_age_ms == 1000
    assert sensor.observe(1.5).traffic.approaches[0].queue_length == 0


@pytest.mark.parametrize("mode", ["outage", "freeze", "occlusion", "calibration_invalid"])
def test_camera_faults_degrade_only_affected_approach(mode: Any) -> None:
    sensor = ObservationModel(
        clean(
            faults=(CameraFault(start_s=1, end_s=10, directions=(Direction.NORTH,), mode=mode),),
            freeze_detection_s=0.25,
        ),
        1,
    )
    for step in range(9):
        sensor.ingest(truth(step / 4))
        observed = sensor.observe(step / 4)
    assert not observed.traffic.approaches[0].observation.reliable
    assert observed.traffic.approaches[0].queue_length is None
    assert all(a.observation.reliable for a in observed.traffic.approaches[1:])
    assert observed.traffic.pedestrians_waiting is None


@pytest.mark.parametrize(
    "profile",
    [
        clean(frame_drop_probability=1),
        clean(fps=0.5),
        clean(camera_availability=0),
        clean(inference_latency_s=5),
    ],
)
def test_missing_slow_or_late_frames_are_not_reliable(profile: PerceptionProfile) -> None:
    sensor = ObservationModel(profile, 1)
    for step in range(17):
        sensor.ingest(truth(step / 4))
        observed = sensor.observe(step / 4)
    assert all(not a.observation.reliable for a in observed.traffic.approaches)


def test_detection_errors_id_switches_and_calibration_bias() -> None:
    miss = ObservationModel(clean(missed_detection_probability=1), 1)
    miss.ingest(truth(0))
    assert miss.observe(0).traffic.approaches[0].queue_length == 0
    undercount = ObservationModel(clean(queue_undercount_probability=1), 1)
    undercount.ingest(truth(0))
    assert undercount.observe(0).traffic.approaches[0].queue_length == 0
    fp = ObservationModel(clean(false_positives_per_frame=1), 1)
    fp.ingest(GroundTruthState(timestamp_s=0, queued=()))
    assert all(a.queue_length == 1 for a in fp.observe(0).traffic.approaches)
    switched = ObservationModel(clean(id_switch_probability=1, calibration_scale_error=0.2), 1)
    switched.ingest(truth(0))
    initial = switched.observe(0).tracks[0]
    switched.ingest(truth(0.25))
    subsequent = switched.observe(0.25).tracks[0]
    assert initial.track_id != subsequent.track_id
    assert subsequent.observed_wait_s == 0
    assert subsequent.distance_to_stop_m == 12
    assert switched.diagnostics["id_switches"] == 1
    assert len(switched.recent[(Direction.NORTH, False)]) == 1  # ID switch is not an entry.


def test_temporary_occlusion_recovers_without_perfect_reidentification() -> None:
    sensor = ObservationModel(clean(), 1)
    sensor.ingest(truth(0))
    old = sensor.observe(0).tracks[0].track_id
    sensor.occluded_until[123456] = 0.75
    for at in (0.25, 0.5):
        sensor.ingest(truth(at))
        assert not sensor.observe(at).tracks
    sensor.ingest(truth(0.75))
    assert sensor.observe(0.75).tracks[0].track_id == old
    assert sensor.diagnostics["recovered_tracks"] == 1
    sensor.occluded_until[123456] = 4
    for step in range(4, 17):
        sensor.ingest(truth(step / 4))
        last = sensor.observe(step / 4)
    assert last.tracks[0].track_id != old
    assert sensor.diagnostics["lost_tracks"] == 1


def test_noise_seed_and_randomization_are_reproducible() -> None:
    profile = clean(position_noise_std_m=0.5, wait_noise_std_s=0.2)
    left, right = ObservationModel(profile, 5), ObservationModel(profile, 5)
    for step in range(20):
        left.ingest(truth(step / 4))
        right.ingest(truth(step / 4))
        assert left.observe(step / 4) == right.observe(step / 4)
    randomized = scenario().model_copy(
        update={"randomization": DomainRandomizationProfile(enabled=True)}
    )
    assert realize(randomized) == realize(randomized)
    assert arrival_tape(realize(randomized)) == arrival_tape(realize(randomized))
    assert realize(randomized) != scenario()


def test_multiple_lanes_and_downstream_blocking() -> None:
    one = TwinDynamics(scenario(lanes_per_approach=1))
    two = TwinDynamics(scenario(lanes_per_approach=2))
    blocked = TwinDynamics(
        scenario(incidents=(Incident(start_s=0, end_s=60, downstream=Direction.SOUTH),))
    )
    for _ in range(240):
        one.advance(signal())
        two.advance(signal())
        blocked.advance(signal())
    assert two.departed > one.departed
    assert blocked.departed == 0 and blocked.blocked_service_s > 0
    assert blocked.spillback_s > 0 and all(blocked.invariants().values())


def test_pedestrian_clearance_blocks_vehicle_service() -> None:
    case = scenario()
    periods = (case.traffic.periods[0].model_copy(update={"pedestrians_per_minute": 240}),)
    dynamics = TwinDynamics(
        case.model_copy(update={"traffic": case.traffic.model_copy(update={"periods": periods})})
    )
    for _ in range(12):
        dynamics.advance(signal(Phase.PEDESTRIAN))
    assert dynamics.ground_truth().crossing_pedestrians > 0
    before = dynamics.stop_line_departures
    for _ in range(4):
        dynamics.advance(signal(Phase.NS))
    assert dynamics.stop_line_departures == before
    for _ in range(40):
        dynamics.advance(signal(Phase.NS))
    assert dynamics.stop_line_departures > before
    assert all(dynamics.invariants().values())


def test_pedestrian_and_saturation_regressions_and_fair_comparison() -> None:
    for name in ("pedestrian-heavy", "saturated", "partial-camera-failure"):
        case = catalog(duration_s=60)[name]
        report = paired_comparison(case)
        assert report["same_arrivals"] and report["pass"]
        for value in report["results"].values():
            assert value["ground_truth_kpi"]["arrived_count"] > 0
            assert value["ground_truth_kpi"]["total_wait_s"] >= 0
            if name == "saturated":
                assert value["ground_truth_kpi"]["max_upstream_count"] > 0
            if name == "pedestrian-heavy":
                assert value["ground_truth_kpi"]["pedestrian_departed_count"] > 0
    case = catalog(duration_s=20)["balanced"]
    assert paired_comparison(case) == paired_comparison(case)


def test_failure_reaches_existing_controller_safety_latch() -> None:
    case = scenario().model_copy(
        update={
            "perception": clean(
                faults=(
                    CameraFault(start_s=2, end_s=3, mode="outage", directions=(Direction.NORTH,)),
                )
            )
        }
    )
    env = DigitalTwinEnvironment(case)
    controller = ObservationControllerPort(Timing(), Policy.ADAPTIVE)
    observation = env.observe()
    for _ in range(24):
        action = controller.act(observation)
        observation = env.step(action)
        if env.now > 2.25:
            assert action.stage == SignalStage.ALL_RED


def test_sumo_boundary_applies_action_before_step_without_truth_leak() -> None:
    class MockBridge:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.time = 0.0

        def apply_signal(self, action: SignalState) -> None:
            self.calls.append(action.phase.value)

        def simulation_step(self) -> None:
            self.calls.append("step")
            self.time += 0.25

        def read_ground_truth(self) -> GroundTruthState:
            self.calls.append("truth")
            return truth(self.time)

        def close(self) -> None:
            self.calls.append("close")

    bridge = MockBridge()
    env = SumoTrafficEnvironment(bridge, clean(missed_detection_probability=1), 1)
    assert isinstance(env, TrafficEnvironment)
    observed = env.step(signal())
    assert bridge.calls == ["truth", Phase.NS.value, "step", "truth"]
    assert len(env.ground_truth().queued) == 1 and observed.traffic.approaches[0].queue_length == 0
    env.close()
    assert bridge.calls[-1] == "close"


def test_scenario_schema_and_catalog_roundtrip(tmp_path: Path) -> None:
    cases = catalog()
    assert len(cases) == 11
    for case in cases.values():
        file = tmp_path / (case.scenario_id + ".json")
        file.write_text(case.model_dump_json(), encoding="utf-8")
        assert load_scenario(file) == case
    with pytest.raises(ValidationError):
        PerceptionProfile(provenance="vision_benchmark_fit")
    values = scenario().model_dump(mode="json")
    values["perception"]["fps"] = 30
    with pytest.raises(ValidationError, match="capture"):
        ScenarioDefinition.model_validate(values)


def test_vehicle_types_and_turns_change_discharge_and_routing() -> None:
    cars = TwinDynamics(scenario())
    buses = TwinDynamics(scenario(vehicle_probabilities=(0, 1, 0, 0, 0)))
    turn = TwinDynamics(
        scenario(
            turn_probabilities=(1, 0, 0),
            incidents=(Incident(start_s=0, end_s=60, downstream=Direction.EAST),),
        )
    )
    for _ in range(240):
        cars.advance(signal())
        buses.advance(signal())
        turn.advance(signal())
    assert cars.departed > buses.departed
    assert turn.departed == 0  # A north approach left-turn is routed to the east exit.
    assert all(turn.invariants().values())


def test_declarative_cli_evaluation_and_immutable_report(tmp_path: Path) -> None:
    from traffic_simulator.twin_cli import main

    case = catalog(duration_s=10)["balanced"]
    source = tmp_path / "case.json"
    source.write_text(case.model_dump_json(), encoding="utf-8")
    args = ["--scenario", str(source), "--output", str(tmp_path / "report.json")]
    assert main(args) == 0
    assert main(args) == 2


def test_clock_reversal_and_retroactive_truth_are_rejected() -> None:
    sensor = ObservationModel(clean(), 1)
    sensor.ingest(truth(0))
    sensor.observe(1)
    with pytest.raises(ValueError):
        sensor.ingest(truth(0.5))
    with pytest.raises(ValueError):
        sensor.observe(0.5)


def test_conflict_monitor_is_not_a_constant_success() -> None:
    dynamics = TwinDynamics(scenario())
    dynamics.conflicting_service = 1
    assert not dynamics.invariants()["no_conflicting_service"]

"""Formal control environment validation. No policy fitting or held-out tuning."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from gymnasium.utils.env_checker import check_env
from pydantic import ValidationError
from test_digital_twin import clean, signal
from traffic_core.controller import TrafficController
from traffic_core.models import Direction, ObjectKind, Phase, Policy, SignalStage
from traffic_core.settings import Timing
from traffic_simulator.environment import DigitalTwinEnvironment
from traffic_simulator.learning.contracts import (
    FEATURES,
    INDEX,
    ControlRequest,
    ObservedSupplement,
    RewardSpecification,
    decode_action,
)
from traffic_simulator.learning.environment import LearningControlEnvironment
from traffic_simulator.learning.evaluation import evaluate_policy
from traffic_simulator.learning.observation import ObservationStack, encode
from traffic_simulator.learning.policy import (
    ActuatedController,
    CurrentAdaptiveController,
    FixedTimeController,
    LearnedPolicy,
)
from traffic_simulator.learning.protocol import (
    PolicyRunManifest,
    ScenarioSplits,
    digest,
    environment_from_split,
    freeze_run_manifest,
)
from traffic_simulator.learning.reward import RewardLedger, fairness_report, reward_terms
from traffic_simulator.learning.safety import SafetySupervisor
from traffic_simulator.scenarios import catalog
from traffic_simulator.twin_models import CameraFault, GroundTruthState, ServiceRecord, TruthEntity


def case(duration: float = 30) -> Any:
    return catalog(duration_s=duration)["balanced"].model_copy(update={"perception": clean()})


def test_official_gym_checker_and_deterministic_step() -> None:
    env = LearningControlEnvironment(case())
    check_env(env, skip_render_check=True)

    def rollout() -> list[tuple[Any, ...]]:
        array, info = env.reset(seed=991)
        assert env.observation_space.contains(array)
        results: list[tuple[Any, ...]] = [(array.tolist(), info)]
        for action in (0, 8, 1, 5, 3, 7):
            observation, reward, terminated, truncated, details = env.step(action)
            results.append((observation.tolist(), reward, terminated, truncated, details))
        return results

    assert rollout() == rollout()
    with pytest.raises(ValueError):
        env.step(9)
    with pytest.raises(ValueError):
        env.step(True)
    with pytest.raises(TypeError):
        env.step_request(signal())  # type: ignore[arg-type]
    env.close()
    with pytest.raises(RuntimeError):
        env.step(0)


def test_observation_masks_supplements_and_no_truth_substitution() -> None:
    world = DigitalTwinEnvironment(case())
    shield = SafetySupervisor(Timing())
    for _ in range(16):
        observed = world.step(signal(stage=SignalStage.ALL_RED))
    first = encode(observed, shield)
    assert first.physical("north.departure_rate") is None
    assert first.physical("north.downstream_occupancy") is None
    world.dynamics.downstream[Direction.SOUTH].append(1000)
    assert encode(observed, shield) == first  # Mutating hidden storage cannot fill actor features.
    with pytest.raises(TypeError):
        encode(world.ground_truth(), shield)  # type: ignore[arg-type]
    measurement = ObservedSupplement(
        captured_at_s=4,
        confidence=0.8,
        source="exit-camera",
        departure_rate_per_minute=9,
        downstream_occupancy=0.7,
    )
    second = encode(observed, shield, {Direction.NORTH: measurement})
    assert second.physical("north.departure_rate") == 9
    assert second.physical("north.downstream_occupancy") == 0.7
    old = measurement.model_copy(update={"captured_at_s": 0})
    assert encode(observed, shield, {Direction.NORTH: old}).physical("north.departure_rate") is None
    for name in ("north.departure_rate", "north.downstream_occupancy"):
        assert first.values[INDEX[name]] == 0 and first.valid[INDEX[name]] == 0


def test_stack_padding_and_normalization_clipping() -> None:
    world = DigitalTwinEnvironment(case())
    shield = SafetySupervisor(Timing())
    observed = world.observe()
    fields = list(observed.traffic.approaches)
    fields[0] = fields[0].model_copy(update={"queue_length": 1000})
    observed = observed.model_copy(
        update={"traffic": observed.traffic.model_copy(update={"approaches": tuple(fields)})}
    )
    encoded = encode(observed, shield)
    assert encoded.values[INDEX["north.queue_count"]] == 1
    assert encoded.clipped[INDEX["north.queue_count"]] == 1
    stack = ObservationStack(4)
    result = stack.append(encoded)
    assert result.shape == (4, 3 * len(FEATURES) + 1)
    assert np.all(result[:3] == 0)
    assert result[-1, -1] == 1
    with pytest.raises(ValueError):
        stack.append(encoded)
    for _ in range(4):
        observed = world.step(signal())
        result = stack.append(encode(observed, shield))
    assert np.all(result[:, -1] == 1)


def test_all_nine_requests_are_safe_and_clearances_cannot_be_skipped() -> None:
    timing = Timing()
    world = DigitalTwinEnvironment(case(180))
    shield = SafetySupervisor(timing)
    transitions: list[tuple[float, Phase, SignalStage]] = [(0, Phase.NS, SignalStage.ALL_RED)]
    for tick in range(720):
        # Intentionally contradictory requests every physics tick.
        action = shield.apply(decode_action((tick * 7) % 9), world.observe())
        if (action.phase, action.stage) != transitions[-1][1:]:
            transitions.append((world.now, action.phase, action.stage))
        world.step(action)
    assert len(transitions) > 10
    for before, after in zip(transitions, transitions[1:], strict=False):
        at, phase, stage = before
        end, next_phase, next_stage = after
        if stage == SignalStage.GREEN:
            assert next_stage == SignalStage.YELLOW and next_phase == phase
            assert end - at >= (
                timing.pedestrian_green_seconds
                if phase == Phase.PEDESTRIAN
                else timing.min_green_seconds
            )
            assert end - at <= timing.max_green_seconds + case().step_s
        elif stage == SignalStage.YELLOW:
            assert next_stage == SignalStage.ALL_RED
            assert end - at >= (
                timing.pedestrian_clearance_seconds
                if phase == Phase.PEDESTRIAN
                else timing.yellow_seconds
            )
        else:
            assert next_stage == SignalStage.GREEN
            assert end - at >= timing.all_red_seconds


def test_permanent_ns_request_cannot_starve_other_phase_opportunities() -> None:
    world = DigitalTwinEnvironment(case(300))
    shield = SafetySupervisor(Timing())
    last_seen = dict.fromkeys(Phase, 0.0)
    max_gaps = dict.fromkeys(Phase, 0.0)
    request = ControlRequest(requested_phase=Phase.NS, requested_duration_s=180)
    for _ in range(1200):
        action = shield.apply(request, world.observe())
        if action.stage == SignalStage.GREEN:
            last_seen[action.phase] = world.now
        for phase in Phase:
            max_gaps[phase] = max(max_gaps[phase], world.now - last_seen[phase])
        world.step(action)
    assert all(shield.has_served.values())
    # Bound includes non-skippable current minimum and the two clearance stages.
    assert max(max_gaps.values()) <= shield.service_bound_s + 20
    assert shield.interventions > 0


def test_reward_decomposition_is_exact_and_delay_unit_is_not_double_counted() -> None:
    before = GroundTruthState(timestamp_s=0, queued=())
    after = GroundTruthState(
        timestamp_s=1,
        queued=(),
        total_wait_s=10,
        pedestrian_wait_s=3,
        downstream_delay_s=2,
        departed=4,
    )
    terms = reward_terms(before, after, 1)
    assert terms["total_delay"] == 12 and terms["queue_delay"] == 7
    assert terms["pedestrian_delay"] == 3 and terms["throughput"] == 4
    ledger = RewardLedger(RewardSpecification())
    reward, contributions = ledger.record(terms)
    assert reward == -12 == sum(contributions.values())
    assert ledger.report()["components"]["pedestrian_delay"]["raw"] == 3
    assert ledger.report()["components"]["pedestrian_delay"]["contribution"] == 0


def test_fairness_includes_unserved_tail_and_all_groups() -> None:
    entity = TruthEntity(
        entity_id=2,
        kind=ObjectKind.PEDESTRIAN,
        approach=Direction.WEST,
        lane_id="west-walk",
        arrived_at_s=0,
        distance_to_stop_m=0,
        length_m=0.5,
    )
    truth = GroundTruthState(timestamp_s=1000, queued=(entity,), arrived=2, departed=1)
    completed = [
        ServiceRecord(
            entity_id=1,
            kind=ObjectKind.CAR,
            approach=Direction.NORTH,
            served_at_s=1,
            queue_wait_s=1,
        )
    ]
    report = fairness_report(truth, completed)
    assert report["all"]["completed_total_wait"]["mean"] == 1
    assert report["all"]["all_arrivals_lower_bound"]["maximum"] == 1000
    assert report["pedestrians"]["right_censored_count"] == 1
    assert set(report["by_direction"]) == {d.value for d in Direction}
    for key in ("mean", "median", "p90", "p95", "p99", "maximum"):
        assert report["by_direction"]["west"]["unfinished_wait_lower_bound"][key] == 1000


def test_adaptive_formula_matches_existing_controller() -> None:
    timing = Timing()
    world = DigitalTwinEnvironment(case())
    for _ in range(100):
        observed = world.step(signal(stage=SignalStage.ALL_RED))
    obs = encode(observed, SafetySupervisor(timing))
    baseline = CurrentAdaptiveController(timing)
    existing = TrafficController(timing, Policy.ADAPTIVE)
    for phase in Phase:
        assert baseline.duration(obs, phase) == pytest.approx(
            existing.decide(observed.traffic, phase).green_seconds
        )


@pytest.mark.parametrize(
    "controller", [FixedTimeController, CurrentAdaptiveController, ActuatedController]
)
def test_baselines_use_same_contract_and_report_components(controller: Any) -> None:
    report = evaluate_policy(case(), controller(Timing()))
    assert report["invariants_passed"] and not report["training_started"]
    assert report["reward"]["total_reward"] == pytest.approx(
        sum(row["contribution"] for row in report["reward"]["components"].values())
    )
    assert report["fairness"]["all"]["all_arrivals_lower_bound"]["count"] == report["arrived"]


def test_learned_adapter_inference_only_and_invalid_action_rejected() -> None:
    policy = LearnedPolicy(lambda history: 8, lambda seed: None)
    env = LearningControlEnvironment(case())
    history, _ = env.reset(seed=7)
    request = policy.propose(env.latest, history)
    assert request.requested_phase == Phase.PEDESTRIAN and request.requested_duration_s == 45
    invalid = LearnedPolicy(lambda history: 999, lambda seed: None)
    with pytest.raises(ValueError):
        invalid.propose(env.latest, history)


def splits() -> ScenarioSplits:
    assignments = [
        {
            "split": name,
            "scenario": catalog(seed=seed, duration_s=30)["balanced"]
            .model_copy(update={"scenario_id": f"{name}-{seed}"})
            .model_dump(mode="json"),
        }
        for name, seed in zip(
            ("TRAIN", "VALIDATION", "TEST", "CHALLENGE"), (111, 222, 333, 444), strict=True
        )
    ]
    values = {"version": "rohai-control-splits-v1", "assignments": assignments}
    return ScenarioSplits.model_validate({**values, "manifest_sha256": digest(values)})


def test_split_gates_and_registered_seeds_cannot_be_overridden() -> None:
    manifest = splits()
    for forbidden in ("VALIDATION", "TEST", "CHALLENGE"):
        with pytest.raises(PermissionError):
            manifest.select(forbidden, purpose="training")
    with pytest.raises(PermissionError):
        manifest.select("TEST", purpose="tuning")
    with pytest.raises(PermissionError):
        environment_from_split(manifest, "TRAIN", purpose="training")
    env = environment_from_split(manifest, "TRAIN", purpose="evaluation")
    env.reset(seed=333)
    assert env.world is not None and env.world.scenario.seed == 111
    changed = manifest.model_dump(mode="json")
    changed["assignments"][0]["scenario"]["seed"] = 333
    with pytest.raises(ValidationError):
        ScenarioSplits.model_validate(changed)


def test_policy_run_manifest_requires_review_integrity_and_real_checkpoint(tmp_path: Path) -> None:
    reward = RewardSpecification()
    run = PolicyRunManifest(
        run_id="planned-only",
        seed=1,
        reward=reward,
        reward_sha256=digest(reward.model_dump(mode="json")),
        scenario_split_sha256="a" * 64,
        algorithm_config={"candidate": "not_selected_for_training"},
        git_commit="1234567",
        dirty_tree=True,
        code_sha256="b" * 64,
        dependency_versions={"gymnasium": "1.3.0"},
        started_at=datetime.now(UTC),
    )
    freeze_run_manifest(run, tmp_path / "run.json")
    with pytest.raises(FileExistsError):
        freeze_run_manifest(run, tmp_path / "run.json")
    changed = run.model_dump(mode="json")
    changed["status"] = "RUNNING"
    with pytest.raises(ValidationError, match="reviewed"):
        PolicyRunManifest.model_validate(changed)
    changed["status"] = "PLANNED"
    changed["reward"]["pedestrian_delay"] = 50
    with pytest.raises(ValidationError, match="modified"):
        PolicyRunManifest.model_validate(changed)


def test_episode_horizon_is_truncation_not_terminal_success() -> None:
    env = LearningControlEnvironment(case(5))
    env.reset(seed=5)
    for _ in range(5):
        _, _, terminated, truncated, info = env.step(0)
    assert truncated and not terminated
    assert info["episode_report"]["held_out_improvement_claim"] is False
    with pytest.raises(RuntimeError):
        env.step(0)


def test_camera_fault_preserves_yellow_then_latches_all_red() -> None:
    scenario = case(30).model_copy(
        update={
            "perception": clean(
                faults=(
                    CameraFault(start_s=6, end_s=9, mode="outage", directions=(Direction.NORTH,)),
                )
            )
        }
    )
    world = DigitalTwinEnvironment(scenario)
    shield = SafetySupervisor(Timing())
    request = ControlRequest(requested_phase=Phase.NS, requested_duration_s=45)
    for _ in range(60):
        now = world.now
        output = shield.apply(request, world.observe())
        if 2 <= now < 6:
            assert output.stage == SignalStage.GREEN
        if 6 <= now < 9:
            assert output.stage == SignalStage.YELLOW
        if now >= 9:
            assert output.stage == SignalStage.ALL_RED and shield.machine.faulted
        world.step(output)


def test_downstream_hold_remains_in_fairness_tail() -> None:
    truth = GroundTruthState(timestamp_s=110, queued=(), arrived=1, downstream_vehicles=1)
    record = ServiceRecord(
        entity_id=1,
        kind=ObjectKind.TRUCK,
        approach=Direction.NORTH,
        served_at_s=1,
        queue_wait_s=1,
        downstream_delay_s=100,
        departed=False,
    )
    report = fairness_report(truth, [record])
    assert report["all"]["all_arrivals_lower_bound"]["maximum"] == 101
    assert report["by_class"]["truck"]["right_censored_count"] == 1
    assert report["all"]["completed_total_wait"]["count"] == 0

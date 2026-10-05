import asyncio
import json
from pathlib import Path

import pytest
from smart_traffic_backend.improvement import (
    DriftMonitor,
    ImprovementLog,
    ImprovementWriter,
    hard_case_tags,
)
from traffic_core.models import Policy
from traffic_core.settings import Timing
from traffic_simulator.engine import SimulationEngine
from traffic_simulator.simulator import Scenario
from traffic_vision.dataset.files import digest_file
from traffic_vision.training.improvement_release import FeedbackWorkflow, ImprovementReleaseRegistry


def test_decisions_retention_outcomes_and_proposal_race(tmp_path: Path) -> None:
    log = ImprovementLog(tmp_path / "decisions.sqlite3", capacity=2)
    snapshot = SimulationEngine(Scenario(), Timing(), Policy.ADAPTIVE).snapshot()
    signal = snapshot.signals.model_dump(mode="json")
    stages = {"requested": signal, "safety_approved": signal, "command_sent": signal}
    log.proposal(1, {"phase": "different"}, "policy_timeout")
    for sequence in range(1, 4):
        log.decision(snapshot.traffic, sequence, [], "synthetic", "baseline-v1", stages, None)
    records = log.records()
    assert len(records) == 2
    assert records[0]["subsequent_outcomes"] is not None
    assert records[1]["hardware_acknowledgement"] is None
    assert all(r["label_status"] == "UNVERIFIED_PREDICTION" for r in records)
    assert records[1]["drift"]["quality_regression"] == "UNKNOWN"
    reopened = ImprovementLog(log.path, capacity=2)
    assert len(reopened.records()) == 2
    assert reopened.session_id != log.session_id


def test_early_proposal_is_correlated(tmp_path: Path) -> None:
    log = ImprovementLog(tmp_path / "decisions.sqlite3")
    snapshot = SimulationEngine(Scenario(), Timing(), Policy.ADAPTIVE).snapshot()
    state = snapshot.signals.model_dump(mode="json")
    log.proposal(7, {"phase": "different"}, None)
    log.decision(
        snapshot.traffic,
        7,
        [],
        "synthetic",
        "baseline",
        {"requested": state, "safety_approved": state, "command_sent": state},
        None,
    )
    assert "policy_disagreement" in log.records()[0]["review_tags"]


def test_drift_is_evaluation_trigger_and_bounded() -> None:
    monitor = DriftMonitor(dict.fromkeys(DriftMonitor.categories, 1.0), window=2)
    result = monitor.observe(dict.fromkeys(DriftMonitor.categories, 10.0))
    assert result["evaluation_required"]
    assert result["quality_regression"] == "UNKNOWN"
    for _ in range(10):
        result = monitor.observe(dict.fromkeys(DriftMonitor.categories, 1.0))
    assert result["sample_count"] == 2
    assert not result["evaluation_required"]
    with pytest.raises(ValueError):
        monitor.observe(dict.fromkeys(DriftMonitor.categories, float("nan")))


def test_hard_case_categories() -> None:
    tags = hard_case_tags(
        {
            "confidence": 0.1,
            "fragmentations": 3,
            "queue_delta": 20,
            "model_disagreement": True,
            "unusual_scene": True,
            "night_weather_degradation": True,
            "policy_disagreement": True,
            "safety_rejection": True,
        }
    )
    assert len(tags) == 8


def test_feedback_requires_order_and_human_verification(tmp_path: Path) -> None:
    workflow = FeedbackWorkflow(tmp_path)
    with pytest.raises(ValueError):
        workflow.advance("train", actor="trainer", evidence={"job": "1"})
    workflow.advance("collect", actor="collector", evidence={"events": ["1"]})
    with pytest.raises(ValueError):
        workflow.advance("review", actor="reviewer", evidence={"automatic": True})
    workflow.advance("review", actor="reviewer", evidence={"human_verified": True})


def approved(registry: ImprovementReleaseRegistry, root: Path, name: str) -> str:
    artifact, evaluation = root / f"{name}.bin", root / f"{name}.evaluation.json"
    artifact.write_bytes(name.encode())
    evaluation.write_text(
        json.dumps(
            {
                "status": "MEASURED",
                "independent_test": True,
                "artifact_hash": digest_file(artifact),
                "benchmark_id": "held-out-v1",
                "dataset": "immutable-dataset-hash",
                "code_commit": "a" * 40,
                "gates": {
                    "safety": {
                        "baseline": 0,
                        "candidate": 0,
                        "max_regression": 0,
                        "higher_is_better": False,
                    }
                },
                "scope": "unit fixture, not a physical measurement",
            }
        )
    )
    key = registry.candidate(
        artifact=artifact,
        code_commit="a" * 40,
        dataset="immutable-dataset-hash",
        training_config={"seed": 7},
        evaluation_report=evaluation,
        regression_gates={"safety": True},
        benchmark_id="held-out-v1",
    )
    for stage in ("offline", "shadow", "canary", "approve"):
        registry.qualify(
            key,
            stage,
            actor="operator",
            evidence={"passed": True, "human_verified": True, "report": stage},
        )
    return key


def test_release_reproducibility_and_deterministic_rollback(tmp_path: Path) -> None:
    registry = ImprovementReleaseRegistry(tmp_path / "registry")
    baseline = approved(registry, tmp_path, "baseline")
    candidate = approved(registry, tmp_path, "candidate")
    registry.deploy(candidate, rollback_target=baseline)
    assert registry.lineage(candidate)["training_config"] == {"seed": 7}
    assert registry.reproduce(candidate)["approval"]["actor"] == "operator"
    with pytest.raises(PermissionError):
        registry.deploy(candidate, rollback_target=baseline, mode="ACTIVE")
    assert registry.rollback(actor="operator", reason="regression") == baseline
    assert registry.rollback(actor="operator", reason="retry") == baseline
    state = json.loads((registry.root / "deployment.json").read_text())
    assert state["current"] == baseline
    assert state["mode"] == "SHADOW"
    (registry.root / "bundles" / baseline / "artifact").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="artifact changed"):
        registry.lineage(baseline)


def test_sealed_release_survives_source_deletion(tmp_path: Path) -> None:
    registry = ImprovementReleaseRegistry(tmp_path / "registry")
    key = approved(registry, tmp_path, "sealed")
    (tmp_path / "sealed.bin").unlink()
    (tmp_path / "sealed.evaluation.json").unlink()
    assert registry.reproduce(key)["model_id"] == key


def test_promotion_cannot_skip_shadow_and_canary(tmp_path: Path) -> None:
    registry = ImprovementReleaseRegistry(tmp_path / "registry")
    key = approved(registry, tmp_path, "model")
    with pytest.raises(ValueError):
        registry.qualify(key, "approve", actor="operator", evidence={"passed": True})
    (registry.root / f"{key}.qualification.json").unlink()
    with pytest.raises(ValueError, match="order required"):
        registry.qualify(key, "canary", actor="operator", evidence={"passed": True})
    manifest = registry.root / f"{key}.json"
    manifest.write_text("{}")
    with pytest.raises(ValueError, match="manifest changed"):
        registry.lineage(key)


def test_writer_flush_and_storage_fault_are_isolated(tmp_path: Path) -> None:
    log = ImprovementLog(tmp_path / "decisions.sqlite3")
    writer = ImprovementWriter(log)
    snapshot = SimulationEngine(Scenario(), Timing(), Policy.ADAPTIVE).snapshot()
    state = snapshot.signals.model_dump(mode="json")
    writer.submit(
        "decision",
        snapshot.traffic,
        1,
        [],
        "synthetic",
        "baseline",
        {"requested": state, "safety_approved": state, "command_sent": state},
        None,
    )
    writer.submit("invalid-operation")
    writer.close()
    assert len(log.records()) == 1
    assert writer.errors == 1
    writer.submit("proposal", 1, None, None)
    assert writer.dropped == 1


def test_disabled_runtime_still_persists_control_decisions(tmp_path: Path) -> None:
    from smart_traffic_backend.config import Settings
    from smart_traffic_backend.hardware import MockHardwareController
    from smart_traffic_backend.runtime import Runtime

    async def exercise() -> None:
        runtime = Runtime(
            Settings(tick_seconds=0.05, improvement_log_path=tmp_path / "runtime.sqlite3"),
            MockHardwareController(),
        )
        await runtime.start()
        await asyncio.sleep(0.17)
        await runtime.stop()
        records = runtime.improvement_log.records()
        assert len(records) >= 2
        assert records[0]["subsequent_outcomes"] is not None
        assert records[0]["controller_policy_version"] == "deterministic-baseline-v1"
        assert all(record["hardware_acknowledgement"] is None for record in records)
        assert runtime.improvement_writer.errors == 0

    asyncio.run(exercise())

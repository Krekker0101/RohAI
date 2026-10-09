import io
import json
import multiprocessing as mp
import sqlite3
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from fastapi.testclient import TestClient
from smart_traffic_backend.config import Settings
from smart_traffic_backend.main import create_app
from traffic_core.model_registry import (
    CentralModelRegistry,
    RegisteredModel,
    ReleaseEvidence,
    sha256,
)
from traffic_core.settings import Timing
from traffic_simulator.learning.environment import LearningControlEnvironment
from traffic_simulator.learning.intelligence_contracts import (
    ControlGeometry,
    PolicyMetadata,
    PolicyState,
    SafetyConstraints,
    TrafficObservation,
)
from traffic_simulator.learning.intelligence_engine import IntelligenceEngine
from traffic_simulator.learning.intelligence_policy import fixed_adapter
from traffic_simulator.learning.safe_actor import SHAPES, IsolatedPolicy, NumericPolicy, load_arrays
from traffic_simulator.learning.shadow import ShadowIntelligence
from traffic_simulator.scenarios import catalog


def artifact(tmp_path: Path) -> Path:
    path = tmp_path / "actor.npz"
    arrays: dict[str, Any] = {k: np.zeros(shape, dtype=np.float32) for k, shape in SHAPES.items()}
    arrays["ba"][4] = 1
    np.savez(path, **arrays)
    return path


def model(path: Path, version: str = "1") -> RegisteredModel:
    return RegisteredModel(
        model_id="test-actor",
        version=version,
        kind="CONTROL",
        architecture="PPO-MLP-64x64-Tanh-9",
        parent_model="test-fixture",
        dataset_version="fixture",
        source_code_version="test-code",
        training_config={"fixture": True},
        training_results={"status": "fixture"},
        evaluation_results={},
        artifact_sha256=sha256(path),
        artifact_format="numpy-actor-v1",
        compatibility={
            "registry": "rohai-registry-2",
            "observation": "rohai-observation-1+geometry-3",
        },
        provenance={"fixture": True},
        license_information="Owned test fixture, never a trained field model",
        created_at=datetime.now(UTC),
    )


def approve(
    registry: CentralModelRegistry,
    record: RegisteredModel,
    tmp_path: Path,
    *,
    independent: bool = True,
) -> None:
    evidence_data: dict[str, Any] = {
        "protocol": "rohai-release-3.1",
        "model_key": record.key,
        "artifact_sha256": record.artifact_sha256,
        "source_code_version": record.source_code_version,
        "dataset_version": record.dataset_version,
        "independent": independent,
        "safety_passed": True,
        "invalid_actions": 0,
        "inference_p95_ms": 1,
        "failure_cases_passed": True,
        "fairness_measured": True,
        "starvation_measured": True,
        "strong_baseline_comparison": True,
        "superiority_proven": False,
        "warnings": [],
    }
    evaluation = tmp_path / (record.version + "-evaluation.json")
    evaluation.write_text(json.dumps({"release_evidence": evidence_data}), encoding="utf-8")
    registry.evaluate(
        record.key,
        ReleaseEvidence.model_validate({**evidence_data, "evaluation_sha256": sha256(evaluation)}),
        evaluation,
    )
    registry.transition(record.key, "APPROVED", "Test-only research acceptance")


def test_registry_lifecycle_scope_and_explicit_rollback(tmp_path: Path) -> None:
    path = artifact(tmp_path)
    registry = CentralModelRegistry(tmp_path / "registry")
    first, second = model(path), model(path, "2")
    with pytest.raises(ValueError):
        RegisteredModel.model_validate(
            {**first.model_dump(), "compatibility": {**first.compatibility, "history": "5"}}
        )
    for record in (first, second):
        registry.register(record, path)
        approve(registry, record, tmp_path)
    with pytest.raises(PermissionError):
        registry.select(first.key, "attempt physical", scope="PHYSICAL")
    registry.select(first.key, "First approved research model")
    registry.select(second.key, "Second research model")
    assert registry.rollback("Explicit failure rollback") == first.key
    assert registry.snapshot()["selections"]["RESEARCH_SHADOW"] == first.key
    with pytest.raises(PermissionError):
        registry.transition(first.key, "RETIRED", "Cannot retire selected model")
    assert registry.snapshot()["physical_activation"] is False
    assert len(registry.snapshot()["events"]) == 9


def test_registry_corruption_and_unverified_release_rejected(tmp_path: Path) -> None:
    path = artifact(tmp_path)
    registry = CentralModelRegistry(tmp_path / "registry")
    record = model(path)
    registry.register(record, path)
    with pytest.raises(FileExistsError):
        registry.register(record, path)
    with pytest.raises(PermissionError):
        approve(registry, record, tmp_path, independent=False)
    assert registry.get(record.key).status == "EVALUATED"
    registry.artifact(record.key).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="integrity"):
        registry.artifact(record.key)
    with sqlite3.connect(registry.root / "registry.sqlite3") as db:
        db.execute("UPDATE models SET body='{}'")
    with pytest.raises(ValueError, match="metadata integrity"):
        registry.snapshot()


def test_numeric_loader_rejects_objects_shapes_nan_and_unknown_inventory(tmp_path: Path) -> None:
    path = artifact(tmp_path)
    load_arrays(path, sha256(path))
    for invalid in (
        {"w0": np.array([object()], dtype=object)},
        {k: np.full(s, np.nan, dtype=np.float32) for k, s in SHAPES.items()},
        {k: np.zeros(s, dtype=np.float64) for k, s in SHAPES.items()},
    ):
        np.savez(path, **invalid)
        with pytest.raises(ValueError):
            load_arrays(path, sha256(path))


def test_numpy_header_shape_bomb_rejected_before_allocation(tmp_path: Path) -> None:
    path = tmp_path / "bomb.npz"
    with zipfile.ZipFile(path, "w") as archive:
        for key, shape in SHAPES.items():
            stream = io.BytesIO()
            if key == "w0":
                np.lib.format.write_array_header_1_0(
                    stream, {"descr": "<f4", "fortran_order": False, "shape": (10**15,)}
                )
            else:
                np.save(stream, np.zeros(shape, dtype=np.float32), allow_pickle=False)
            archive.writestr(key + ".npy", stream.getvalue())
    with pytest.raises(ValueError, match="header/shape"):
        load_arrays(path, sha256(path))


def observation_state(
    env: LearningControlEnvironment, sequence: int = 0
) -> tuple[TrafficObservation, PolicyState]:
    observation = TrafficObservation(**env.latest.model_dump())
    row = (*observation.values, *observation.valid, *observation.clipped, 1)
    return observation, PolicyState(sequence=sequence, history=(row,) * 4)


def test_isolated_inference_and_hard_hang_quarantine(tmp_path: Path) -> None:
    path = artifact(tmp_path)
    metadata = PolicyMetadata(
        policy_id="fixture", version="1", learned=True, weights_sha256=sha256(path)
    )
    actor = IsolatedPolicy(path, metadata)
    env = LearningControlEnvironment(catalog(duration_s=10)["balanced"])
    env.reset(seed=7)
    observation, state = observation_state(env)
    try:
        decision = actor.decide(observation, state, SafetyConstraints(inference_timeout_s=1))
        assert decision == NumericPolicy(path, metadata).decide(
            observation, state, SafetyConstraints()
        )
        actor.close()
        context = mp.get_context("spawn")
        actor.channel, child = context.Pipe()
        actor.process = context.Process(target=time.sleep, args=(60,), daemon=True)
        actor.closed = False
        actor.process.start()
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            actor.decide(observation, state, SafetyConstraints(inference_timeout_s=0.01))
        assert time.monotonic() - started < 1
        assert not actor.process.is_alive()
        child.close()
    finally:
        actor.close()
        env.close()


def test_actor_safety_feature_mismatch_and_shadow_thread_forbidden() -> None:
    env = LearningControlEnvironment(catalog(duration_s=10)["balanced"])
    env.reset(seed=8)
    env.step(0)
    engine = IntelligenceEngine(fixed_adapter(SafetyConstraints()), env.supervisor)
    try:
        observation, _ = observation_state(env)
        values = list(observation.values)
        values[0] = 0.9 if values[0] != 0.9 else 0.1
        forged = TrafficObservation(**{**observation.model_dump(), "values": tuple(values)})
        row = (*forged.values, *forged.valid, *forged.clipped, 1)
        assert env.world is not None
        with pytest.raises(ValueError, match="features differ"):
            engine.decide(
                forged,
                PolicyState(sequence=0, history=(row,) * 4),
                env.world.observe(),
                env.world.observe().traffic.simulation_time,
            )
        with pytest.raises(PermissionError, match="terminable"):
            IntelligenceEngine(fixed_adapter(SafetyConstraints()), env.supervisor, mode="SHADOW")
    finally:
        engine.close()
        env.close()


def test_shadow_records_identical_input_and_cannot_mutate_actual_safety(tmp_path: Path) -> None:
    path = artifact(tmp_path)
    actor = IsolatedPolicy(
        path,
        PolicyMetadata(policy_id="fixture", version="1", learned=True, weights_sha256=sha256(path)),
    )
    env = LearningControlEnvironment(catalog(duration_s=10)["balanced"])
    env.reset(seed=9)
    env.step(0)
    before = env.supervisor.machine.snapshot(1)
    shadow = ShadowIntelligence(
        actor, Timing(), tmp_path / "trace.sqlite3", "RECORDED VIDEO", ControlGeometry()
    )
    try:
        assert env.world is not None
        shadow.submit(env.world.observe().traffic, env.supervisor.machine)
        deadline = time.monotonic() + 5
        while not shadow.snapshot()["records"] and time.monotonic() < deadline:
            time.sleep(0.01)
        record = shadow.snapshot()["records"][0]
        assert "error" not in record
        assert record["candidate"]["proposed"] is not None
        if record["candidate"]["fallback_reason"]:
            assert record["candidate_status"] == "FALLBACK"
        assert record["candidate"]["safety_reason"] in (
            "minimum_green",
            "accepted",
            "duration_clamped",
            "perception_unreliable",
            "clearance_locked",
        )
        assert record["counterfactual_delay"] is None
        assert record["physical_execution"] is False
        shadow.submit(env.world.observe().traffic, env.supervisor.machine)
        assert shadow.snapshot()["sampled_out"] == 1
        from traffic_simulator.learning.contracts import FEATURES

        masked = TrafficObservation.model_validate(record["candidate"]["observation"])
        assert (
            masked.valid[
                next(i for i, f in enumerate(FEATURES) if f.name == "north.time_since_service_s")
            ]
            == 0
        )
        assert env.supervisor.machine.snapshot(1) == before
        assert record["baseline"]["facts"][
            "north.queue_count"
        ] == TrafficObservation.model_validate(record["candidate"]["observation"]).physical(
            "north.queue_count"
        )
    finally:
        shadow.close()
        env.close()
    assert not actor.process.is_alive()


def test_ai_endpoint_empty_and_demo_cannot_activate_shadow(tmp_path: Path) -> None:
    with TestClient(create_app(Settings(ai_artifact_directory=tmp_path))) as client:
        response = client.get("/api/v1/ai")
        assert response.status_code == 200
        assert response.json()["training"]["status"] == "EMPTY"
        assert response.json()["registry"]["physical_activation"] is False
        response = client.post(
            "/api/v1/ai/shadow", json={"model_key": "unknown@1", "reason": "test", "geometry": {}}
        )
        assert response.status_code == 409
        assert (
            client.post("/api/v1/ai/rollback", json={"reason": "baseline"}).json()["state"]
            == "STOPPED"
        )


def test_shadow_has_no_hardware_imports() -> None:
    import ast

    import traffic_simulator.learning.shadow as module

    assert module.__file__ is not None
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    modules = [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
    assert not any("hardware" in name or "serial" in name or "backend" in name for name in modules)


def test_corrupt_ai_registry_cannot_block_original_runtime(tmp_path: Path) -> None:
    root = tmp_path / "registry"
    root.mkdir()
    (root / "registry.sqlite3").write_bytes(b"NOT A SQLITE DATABASE")
    with TestClient(create_app(Settings(ai_artifact_directory=tmp_path))) as client:
        assert client.get("/api/v1/system").status_code == 200
        data = client.get("/api/v1/ai").json()
        assert data["registry"]["models"] == []
        assert "Registry unavailable" in data["diagnostics"]["error"]
        assert data["overview"]["baseline"] == "adaptive"


def test_real_detector_thread_budget_survives_backend_setup() -> None:
    import torch
    from traffic_vision.detector import YOLODetector
    from traffic_vision.settings import VisionSettings
    from traffic_vision.sources import VideoFrame

    detector = YOLODetector(VisionSettings(device="cpu", torch_threads=2))
    try:
        detector.open()
        assert torch.get_num_threads() == 2
        detector.process(
            VideoFrame(
                np.zeros((256, 256, 3), dtype=np.uint8),
                0,
                0,
                "thread-budget-fixture",
                time.monotonic(),
            )
        )
        assert torch.get_num_threads() == 2
    finally:
        detector.close()


def test_failed_shadow_selection_releases_both_worker_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import smart_traffic_backend.ai as ai_module
    from smart_traffic_backend.ai import AIService, ShadowSelection

    path = artifact(tmp_path)
    service = AIService(tmp_path / "service", Timing())
    assert service.registry is not None
    record = model(path)
    service.registry.register(record, path)
    approve(service.registry, record, tmp_path)
    created: list[ShadowIntelligence] = []

    def sidecar(*args: Any, **kwargs: Any) -> ShadowIntelligence:
        value = ShadowIntelligence(*args, **kwargs)
        created.append(value)
        return value

    def reject(*args: Any, **kwargs: Any) -> None:
        raise PermissionError("Selection changed after actor startup")

    monkeypatch.setattr(ai_module, "ShadowIntelligence", sidecar)
    monkeypatch.setattr(service.registry, "select", reject)
    with pytest.raises(PermissionError):
        service.start_shadow(
            ShadowSelection(
                model_key=record.key, reason="Test cleanup", geometry=ControlGeometry()
            ),
            "file",
        )
    assert created and created[0].closed
    assert not created[0].worker.is_alive()
    assert not created[0].actor.process.is_alive()
    assert service.shadow is None

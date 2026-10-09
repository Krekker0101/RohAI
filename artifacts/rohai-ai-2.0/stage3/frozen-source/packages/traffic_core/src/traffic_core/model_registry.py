"""Central, transactional research registry. Artifacts are data, never executable uploads."""

import hashlib
import json
import shutil
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from traffic_core.models import Model


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


class RegisteredModel(Model):
    schema_version: Literal["rohai-registry-2"] = "rohai-registry-2"
    model_id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,100}$")
    version: str = Field(pattern=r"^[a-zA-Z0-9_.-]{1,80}$")
    kind: Literal["VISION", "CONTROL"]
    architecture: str = Field(min_length=1)
    parent_model: str = Field(min_length=1)
    dataset_version: str = Field(min_length=1)
    source_code_version: str = Field(min_length=1)
    training_config: dict[str, Any]
    training_results: dict[str, Any]
    evaluation_results: dict[str, Any]
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    artifact_format: Literal["numpy-actor-v1", "archived-trusted-checkpoint"]
    compatibility: dict[str, str]
    provenance: dict[str, Any]
    license_information: str = Field(min_length=1)
    created_at: datetime
    status: Literal["EXPERIMENTAL", "EVALUATED", "APPROVED", "REJECTED", "RETIRED"] = "EXPERIMENTAL"
    release_scope: Literal["NONE", "RESEARCH_SHADOW"] = "NONE"

    @model_validator(mode="after")
    def consistent(self) -> "RegisteredModel":
        if self.created_at.tzinfo is None:
            raise ValueError("Timezone required")
        if not self.training_config or not self.training_results or not self.provenance:
            raise ValueError("Complete provenance and training metadata required")
        if self.compatibility.get("registry") != self.schema_version:
            raise ValueError("Unknown registry compatibility")
        if self.artifact_format == "numpy-actor-v1" and (
            self.kind != "CONTROL"
            or self.architecture != "PPO-MLP-64x64-Tanh-9"
            or self.compatibility.get("observation") != "rohai-observation-1+geometry-3"
        ):
            raise ValueError("Unknown actor architecture/input")
        if self.status == "APPROVED" and (
            self.release_scope != "RESEARCH_SHADOW" or self.artifact_format != "numpy-actor-v1"
        ):
            raise ValueError("No executable vision or physical approval in registry v2")
        return self

    @property
    def key(self) -> str:
        return f"{self.model_id}@{self.version}"


class ReleaseEvidence(Model):
    protocol: Literal["rohai-release-3.1"] = "rohai-release-3.1"
    model_key: str
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_code_version: str
    dataset_version: str
    evaluation_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    independent: bool
    safety_passed: bool
    invalid_actions: int = Field(ge=0)
    inference_p95_ms: float = Field(ge=0)
    failure_cases_passed: bool
    fairness_measured: bool
    starvation_measured: bool
    strong_baseline_comparison: bool
    superiority_proven: bool
    warnings: tuple[str, ...] = ()


def release_gates(model: RegisteredModel, evidence: ReleaseEvidence) -> dict[str, bool]:
    """Research-only gates; 100ms is the existing actor deadline, not a field SLA."""
    return {
        "binding": evidence.model_key == model.key
        and evidence.artifact_sha256 == model.artifact_sha256
        and evidence.source_code_version == model.source_code_version
        and evidence.dataset_version == model.dataset_version,
        "safe_format": model.artifact_format == "numpy-actor-v1",
        "independent": evidence.independent,
        "safety": evidence.safety_passed and evidence.invalid_actions == 0,
        "latency": evidence.inference_p95_ms <= 100,
        "failure_scenarios": evidence.failure_cases_passed,
        "fairness": evidence.fairness_measured,
        "starvation": evidence.starvation_measured,
        "strong_baseline": evidence.strong_baseline_comparison,
    }


class CentralModelRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "artifacts").mkdir(exist_ok=True)
        with self._connect() as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS models (key TEXT PRIMARY KEY, body TEXT, digest TEXT);"
                "CREATE TABLE IF NOT EXISTS selections (scope TEXT PRIMARY KEY, current TEXT, "
                "previous TEXT);"
                "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, time TEXT, body TEXT);"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.root / "registry.sqlite3", timeout=2)

    def _read(self, db: sqlite3.Connection, key: str) -> RegisteredModel:
        row = db.execute("SELECT body,digest FROM models WHERE key=?", (key,)).fetchone()
        if row is None:
            raise KeyError(key)
        if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
            raise ValueError("Registry metadata integrity failure")
        model = RegisteredModel.model_validate_json(row[0])
        if model.key != key:
            raise ValueError("Registry identity mismatch")
        return model

    def _store(self, db: sqlite3.Connection, model: RegisteredModel) -> None:
        body = canonical(model.model_dump(mode="json"))
        db.execute(
            "INSERT OR REPLACE INTO models VALUES (?,?,?)",
            (model.key, body, hashlib.sha256(body.encode()).hexdigest()),
        )

    def _event(self, db: sqlite3.Connection, body: dict[str, Any]) -> None:
        db.execute(
            "INSERT INTO events(time,body) VALUES (?,?)",
            (datetime.now(UTC).isoformat(), canonical(body)),
        )

    def get(self, key: str) -> RegisteredModel:
        with self._connect() as db:
            return self._read(db, key)

    def artifact(self, key: str) -> Path:
        model = self.get(key)
        suffix = ".npz" if model.artifact_format == "numpy-actor-v1" else ".archive"
        path = self.root / "artifacts" / (model.artifact_sha256 + suffix)
        if path.is_symlink() or not path.is_file() or sha256(path) != model.artifact_sha256:
            raise ValueError("Artifact integrity failure")
        return path

    def register(self, model: RegisteredModel, artifact: Path) -> None:
        if model.status != "EXPERIMENTAL" or model.release_scope != "NONE":
            raise ValueError("Registration always starts EXPERIMENTAL")
        if artifact.is_symlink() or artifact.stat().st_size > 100_000_000:
            raise ValueError("Artifact type/size rejected")
        if sha256(artifact) != model.artifact_sha256:
            raise ValueError("Artifact hash mismatch")
        suffix = ".npz" if model.artifact_format == "numpy-actor-v1" else ".archive"
        target = self.root / "artifacts" / (model.artifact_sha256 + suffix)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM models WHERE key=?", (model.key,)).fetchone():
                raise FileExistsError("Immutable model version already exists")
            if not target.exists():
                with target.open("xb") as stream, artifact.open("rb") as source:
                    shutil.copyfileobj(source, stream)
            if target.is_symlink() or sha256(target) != model.artifact_sha256:
                raise ValueError("Copied artifact corrupt")
            self._store(db, model)
            self._event(db, {"action": "register", "model": model.key})

    def evaluate(self, key: str, evidence: ReleaseEvidence, evaluation: Path) -> dict[str, bool]:
        self.artifact(key)
        if sha256(evaluation) != evidence.evaluation_sha256:
            raise ValueError("Evaluation integrity failure")
        report = json.loads(evaluation.read_text(encoding="utf-8"))
        if report.get("release_evidence") != evidence.model_dump(
            mode="json", exclude={"evaluation_sha256"}
        ):
            raise ValueError("Release evidence contradicts frozen evaluation report")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            model = self._read(db, key)
            if model.status != "EXPERIMENTAL":
                raise ValueError("Evaluation transition forbidden")
            gates = release_gates(model, evidence)
            if not gates["binding"]:
                raise ValueError("Evaluation belongs to another model/code/dataset")
            # Persist exact evidence, not a caller-controlled approval flag.
            result = {"evidence": evidence.model_dump(mode="json"), "gates": gates}
            target = self.root / "artifacts" / (evidence.evaluation_sha256 + ".json")
            if not target.exists():
                with target.open("xb") as stream, evaluation.open("rb") as source:
                    shutil.copyfileobj(source, stream)
            if target.is_symlink() or sha256(target) != evidence.evaluation_sha256:
                raise ValueError("Copied evaluation corrupt")
            updated = RegisteredModel.model_validate(
                {**model.model_dump(), "status": "EVALUATED", "evaluation_results": result}
            )
            self._store(db, updated)
            self._event(db, {"action": "evaluate", "model": key, "gates": gates})
            return gates

    def transition(
        self, key: str, status: Literal["APPROVED", "REJECTED", "RETIRED"], reason: str
    ) -> None:
        if not reason.strip():
            raise ValueError("Attributed reason required")
        self.artifact(key)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            model = self._read(db, key)
            allowed = {
                "EXPERIMENTAL": {"REJECTED"},
                "EVALUATED": {"APPROVED", "REJECTED"},
                "APPROVED": {"RETIRED"},
                "REJECTED": {"RETIRED"},
            }
            if status not in allowed.get(model.status, set()):
                raise ValueError("Release transition forbidden")
            if status == "APPROVED":
                evidence = ReleaseEvidence.model_validate(model.evaluation_results["evidence"])
                path = self.root / "artifacts" / (evidence.evaluation_sha256 + ".json")
                if path.is_symlink() or sha256(path) != evidence.evaluation_sha256:
                    raise ValueError("Evaluation changed")
                if not all(release_gates(model, evidence).values()):
                    raise PermissionError("Research SHADOW gates failed")
            if (
                status == "RETIRED"
                and db.execute("SELECT 1 FROM selections WHERE current=?", (key,)).fetchone()
            ):
                raise PermissionError("Deactivate or rollback before retiring selected model")
            updated = RegisteredModel.model_validate(
                {
                    **model.model_dump(),
                    "status": status,
                    "release_scope": "RESEARCH_SHADOW" if status == "APPROVED" else "NONE",
                }
            )
            self._store(db, updated)
            self._event(db, {"action": status, "model": key, "reason": reason})

    def select(self, key: str, reason: str, *, scope: str = "RESEARCH_SHADOW") -> None:
        if scope != "RESEARCH_SHADOW" or not reason.strip():
            raise PermissionError("Explicit research-only selection required")
        self.artifact(key)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            model = self._read(db, key)
            if model.status != "APPROVED" or model.release_scope != scope:
                raise PermissionError("Model not approved for requested scope")
            row = db.execute(
                "SELECT current,previous FROM selections WHERE scope=?", (scope,)
            ).fetchone()
            if row and row[0] == key:
                return
            db.execute(
                "INSERT OR REPLACE INTO selections VALUES (?,?,?)",
                (scope, key, row[0] if row else None),
            )
            self._event(db, {"action": "select", "model": key, "scope": scope, "reason": reason})

    def rollback(self, reason: str) -> str | None:
        if not reason.strip():
            raise ValueError("Rollback reason required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT current,previous FROM selections WHERE scope='RESEARCH_SHADOW'"
            ).fetchone()
            previous = row[1] if row else None
            if previous:
                model = self._read(db, previous)
                if model.status != "APPROVED":
                    raise PermissionError("Previous model retired/rejected")
                self.artifact(previous)
            db.execute(
                "INSERT OR REPLACE INTO selections VALUES ('RESEARCH_SHADOW',?,?)",
                (previous, row[0] if row else None),
            )
            self._event(db, {"action": "rollback", "model": previous, "reason": reason})
            return previous

    def snapshot(self) -> dict[str, Any]:
        with self._connect() as db:
            keys = [r[0] for r in db.execute("SELECT key FROM models ORDER BY key")]
            models = [self._read(db, key).model_dump(mode="json") for key in keys]
            selections = dict(db.execute("SELECT scope,current FROM selections"))
            events = [
                {"id": r[0], "time": r[1], **json.loads(r[2])}
                for r in db.execute("SELECT id,time,body FROM events ORDER BY id DESC LIMIT 100")
            ]
        return {
            "models": models,
            "selections": selections,
            "events": events,
            "physical_activation": False,
        }

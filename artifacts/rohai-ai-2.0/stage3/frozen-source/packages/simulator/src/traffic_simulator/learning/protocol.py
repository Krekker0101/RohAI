"""File-based scenario splits and provenance contracts for future training runs."""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator
from traffic_core.models import Model

from traffic_simulator.learning.contracts import RewardSpecification
from traffic_simulator.learning.environment import LearningControlEnvironment
from traffic_simulator.twin_models import ScenarioDefinition

Split = Literal["TRAIN", "VALIDATION", "TEST", "CHALLENGE"]


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


class ScenarioAssignment(Model):
    split: Split
    scenario: ScenarioDefinition


class ScenarioSplits(Model):
    version: Literal["rohai-control-splits-v1"] = "rohai-control-splits-v1"
    assignments: tuple[ScenarioAssignment, ...] = Field(min_length=4)
    manifest_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def independent(self) -> "ScenarioSplits":
        from traffic_simulator.twin_dynamics import arrival_tape, realize, tape_hash

        if {a.split for a in self.assignments} != {"TRAIN", "VALIDATION", "TEST", "CHALLENGE"}:
            raise ValueError("All four scenario partitions are required")
        names, seeds, hashes = set(), set(), set()
        for entry in self.assignments:
            key = entry.scenario.scenario_id
            tape = tape_hash(arrival_tape(realize(entry.scenario)))
            if key in names or entry.scenario.seed in seeds or tape in hashes:
                raise ValueError("Scenario ID, seed or arrival tape overlap across assignments")
            names.add(key)
            seeds.add(entry.scenario.seed)
            hashes.add(tape)
        if (
            digest(self.model_dump(mode="json", exclude={"manifest_sha256"}))
            != self.manifest_sha256
        ):
            raise ValueError("Scenario split manifest changed")
        return self

    def select(
        self, split: Split, *, purpose: Literal["training", "tuning", "evaluation"]
    ) -> tuple[ScenarioDefinition, ...]:
        if purpose == "training" and split != "TRAIN":
            raise PermissionError("Training accessor forbids VALIDATION/TEST/CHALLENGE")
        if purpose == "tuning" and split not in ("TRAIN", "VALIDATION"):
            raise PermissionError("Reward/hyperparameter tuning forbids TEST/CHALLENGE")
        return tuple(a.scenario for a in self.assignments if a.split == split)


def environment_from_split(
    manifest: ScenarioSplits,
    split: Split,
    index: int = 0,
    *,
    purpose: Literal["training", "tuning", "evaluation"],
    reward: RewardSpecification | None = None,
) -> LearningControlEnvironment:
    cases = manifest.select(split, purpose=purpose)
    if purpose == "training" and (reward is None or not reward.approved_for_training):
        raise PermissionError("Training requires a reviewed and frozen reward specification")
    return LearningControlEnvironment(cases[index], reward=reward, lock_scenario_seed=True)


class PolicyRunManifest(Model):
    run_id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,100}$")
    seed: int = Field(ge=0)
    environment_version: Literal["rohai-control-env-v1"] = "rohai-control-env-v1"
    observation_schema: Literal["rohai-observation-v1"] = "rohai-observation-v1"
    action_schema: Literal["rohai-action-v1"] = "rohai-action-v1"
    reward: RewardSpecification
    reward_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    scenario_split_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    algorithm_config: dict[str, Any]
    git_commit: str = Field(min_length=7)
    dirty_tree: bool
    code_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    dependency_versions: dict[str, str]
    started_at: datetime
    ended_at: datetime | None = None
    checkpoint: str | None = None
    checkpoint_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    evaluation_metrics: dict[str, Any] = Field(default_factory=dict)
    status: Literal["PLANNED", "RUNNING", "COMPLETED", "FAILED"] = "PLANNED"

    @model_validator(mode="after")
    def coherent(self) -> "PolicyRunManifest":
        if digest(self.reward.model_dump(mode="json")) != self.reward_sha256:
            raise ValueError("Reward was modified after run registration")
        if self.started_at.tzinfo is None or (self.ended_at and self.ended_at.tzinfo is None):
            raise ValueError("Run timestamps require timezone")
        if self.status != "PLANNED" and not self.reward.approved_for_training:
            raise ValueError("No training run without reviewed reward")
        if self.status == "COMPLETED" and (
            not self.checkpoint
            or not self.checkpoint_sha256
            or not self.evaluation_metrics
            or self.ended_at is None
        ):
            raise ValueError("Completed runs require checkpoint, evaluation and end timestamp")
        return self


def freeze_run_manifest(manifest: PolicyRunManifest, destination: Path) -> None:
    from traffic_simulator.twin_evaluation import write_report

    if manifest.checkpoint is not None:
        if (
            hashlib.sha256(Path(manifest.checkpoint).read_bytes()).hexdigest()
            != manifest.checkpoint_sha256
        ):
            raise ValueError("Checkpoint hash mismatch")
    write_report(
        destination,
        {**manifest.model_dump(mode="json"), "recorded_at": datetime.now(UTC).isoformat()},
    )

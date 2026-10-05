"""Content-addressed offline release workflow. Does not reload live models."""

import json
import math
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from traffic_vision.dataset.files import canonical_hash, digest_file, write_new_json
from traffic_vision.evaluation.vision import finish_report
from traffic_vision.training.io import atomic_json, exclusive, read_verified

STAGES = (
    "collect",
    "review",
    "annotate",
    "validate",
    "version_dataset",
    "benchmark",
    "train",
    "evaluate",
    "approve",
    "deploy",
    "monitor",
    "rollback",
)


class FeedbackWorkflow:
    def __init__(self, root: Path) -> None:
        self.root = root

    def advance(self, stage: str, *, actor: str, evidence: dict[str, Any]) -> None:

        if stage not in STAGES or not actor.strip() or not evidence:
            raise ValueError("Known stage, accountable actor and evidence required")
        with exclusive(self.root):
            path = self.root / "workflow.json"
            records = read_verified(path)["records"] if path.exists() else []
            expected = STAGES[len(records)] if len(records) < len(STAGES) else None
            if stage != expected:
                raise ValueError(f"Expected stage {expected}")
            if stage in ("review", "annotate", "validate", "approve"):
                if evidence.get("human_verified") is not True:
                    raise ValueError("Explicit human verification required")
            records.append(
                {
                    "stage": stage,
                    "timestamp": datetime.now(UTC).isoformat(),
                    "actor": actor,
                    "evidence": evidence,
                    "evidence_hash": canonical_hash(evidence),
                }
            )
            atomic_json(path, finish_report({"records": records}))


class ImprovementReleaseRegistry:
    """Immutable release manifests plus an atomic deployment pointer.

    Approval is explicit; artifact hashes are integrity checks, not authentication.
    ACTIVE traffic control is intentionally unavailable in this release.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def candidate(
        self,
        *,
        artifact: Path,
        code_commit: str,
        dataset: str,
        training_config: dict[str, Any],
        evaluation_report: Path,
        regression_gates: dict[str, bool],
        benchmark_id: str,
    ) -> str:
        if not all((code_commit.strip(), dataset.strip(), benchmark_id.strip(), training_config)):
            raise ValueError("Complete reproducibility lineage required")
        if not regression_gates or not all(value is True for value in regression_gates.values()):
            raise ValueError("All regression gates must pass")
        evaluation = json.loads(evaluation_report.read_text(encoding="utf-8"))
        artifact_hash = digest_file(artifact)
        if (
            evaluation.get("status") != "MEASURED"
            or evaluation.get("independent_test") is not True
            or evaluation.get("artifact_hash") != artifact_hash
            or evaluation.get("benchmark_id") != benchmark_id
            or evaluation.get("dataset") != dataset
            or evaluation.get("code_commit") != code_commit
        ):
            raise ValueError("Measured independent evaluation bound to candidate required")
        measurements = evaluation.get("gates", {})
        if set(measurements) != set(regression_gates):
            raise ValueError("Evaluation must measure every regression gate")
        for gate in measurements.values():
            values = [gate.get(k) for k in ("baseline", "candidate", "max_regression")]
            if not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
                for v in values
            ):
                raise ValueError("Finite gate measurements required")
            baseline, candidate, margin = values
            if margin < 0 or not isinstance(gate.get("higher_is_better"), bool):
                raise ValueError("Explicit gate direction and nonnegative margin required")
            regression = baseline - candidate if gate["higher_is_better"] else candidate - baseline
            if regression > margin:
                raise ValueError("Measured regression gate failed")
        manifest = {
            "artifact_hash": artifact_hash,
            "artifact_path": str(artifact.resolve()),
            "code_commit": code_commit,
            "dataset": dataset,
            "training_config": training_config,
            "evaluation_hash": digest_file(evaluation_report),
            "evaluation_path": str(evaluation_report.resolve()),
            "regression_gates": regression_gates,
            "benchmark_id": benchmark_id,
        }
        key = canonical_hash(manifest)
        with exclusive(self.root):
            bundle = self.root / "bundles" / key
            bundle.mkdir(parents=True, exist_ok=False)
            for kind, source in (("artifact", artifact), ("evaluation", evaluation_report)):
                target = bundle / kind
                shutil.copyfile(source, target)
                if digest_file(target) != manifest[f"{kind}_hash"]:
                    raise ValueError(f"{kind} changed during sealing")
            write_new_json(self.root / f"{key}.json", manifest)
        return key

    def lineage(self, key: str) -> dict[str, Any]:
        import json

        if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
            raise ValueError("Invalid model ID")
        manifest: dict[str, Any] = json.loads((self.root / f"{key}.json").read_text())
        if canonical_hash(manifest) != key:
            raise ValueError("Candidate manifest changed")
        for kind in ("artifact", "evaluation"):
            if digest_file(self.root / "bundles" / key / kind) != manifest[f"{kind}_hash"]:
                raise ValueError(f"{kind} changed")
        return manifest

    def _approval(self, key: str) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = read_verified(self.root / f"{key}.qualification.json")[
            "records"
        ]
        if (
            [r["stage"] for r in records] != ["offline", "shadow", "canary", "approve"]
            or not all(r["evidence"].get("passed") is True and r["actor"].strip() for r in records)
            or records[-1]["evidence"].get("human_verified") is not True
        ):
            raise ValueError("Complete passing qualification and human approval required")
        return records

    def reproduce(self, key: str) -> dict[str, Any]:
        manifest = self.lineage(key)
        qualification = self._approval(key)
        return {
            "model_id": key,
            "artifact_path": str((self.root / "bundles" / key / "artifact").resolve()),
            "evaluation_path": str((self.root / "bundles" / key / "evaluation").resolve()),
            "lineage": manifest,
            "approval": qualification[-1],
            "qualification": qualification,
        }

    def qualify(self, key: str, stage: str, *, actor: str, evidence: dict[str, Any]) -> None:

        order = ("offline", "shadow", "canary", "approve")
        if not actor.strip() or not evidence or evidence.get("passed") is not True:
            raise ValueError("Passing evidence and accountable actor required")
        with exclusive(self.root):
            self.lineage(key)
            path = self.root / f"{key}.qualification.json"
            records = read_verified(path)["records"] if path.exists() else []
            if len(records) >= len(order) or stage != order[len(records)]:
                raise ValueError("Offline, shadow, controlled canary, approval order required")
            if stage == "approve" and evidence.get("human_verified") is not True:
                raise ValueError("Human approval required")
            records.append(
                {
                    "stage": stage,
                    "actor": actor,
                    "evidence": evidence,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
            atomic_json(path, finish_report({"records": records}))

    def deploy(self, key: str, *, rollback_target: str, mode: str = "SHADOW") -> None:

        if mode not in ("SHADOW", "ADVISORY"):
            raise PermissionError("Physical ACTIVE deployment remains prohibited")
        if rollback_target == key:
            raise ValueError("Distinct rollback target required")
        with exclusive(self.root):
            for model in (key, rollback_target):
                self.lineage(model)
                self._approval(model)
            atomic_json(
                self.root / "deployment.json",
                finish_report(
                    {
                        "current": key,
                        "rollback_target": rollback_target,
                        "mode": mode,
                    }
                ),
            )

    def rollback(self, *, actor: str, reason: str) -> str:

        if not actor.strip() or not reason.strip():
            raise ValueError("Accountable rollback reason required")
        with exclusive(self.root):
            path = self.root / "deployment.json"
            state = read_verified(path)
            target = state["rollback_target"]
            if target is None:
                self.lineage(str(state["current"]))
                self._approval(str(state["current"]))
                return str(state["current"])  # Idempotent retry.
            self.lineage(target)
            self._approval(target)
            atomic_json(
                path,
                finish_report(
                    {
                        "current": target,
                        "rollback_target": None,
                        "mode": "SHADOW",
                        "actor": actor,
                        "reason": reason,
                    }
                ),
            )
            return str(target)

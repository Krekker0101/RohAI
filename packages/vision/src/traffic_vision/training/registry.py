"""Local single-writer registry; approval and production activation are explicit operations."""

import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from traffic_vision.evaluation.vision import finish_report
from traffic_vision.training.evaluate import verify_evaluation
from traffic_vision.training.io import atomic_json, exclusive, read_verified
from traffic_vision.training.package import verify_package

Status = Literal["CANDIDATE", "VALIDATED", "APPROVED", "REJECTED", "DEPRECATED", "ROLLED_BACK"]


class ModelRegistry:
    def __init__(self, root: Path) -> None:
        self.root = root

    def read(self) -> dict[str, Any]:
        path = self.root / "registry.json"
        if not path.exists():
            return {
                "schema": "rohai-registry-1",
                "models": {},
                "current": None,
                "previous_approved": None,
                "events": [],
            }
        state = read_verified(path)
        from pydantic import TypeAdapter

        from traffic_vision.dataset.schema import Digest

        TypeAdapter(list[Digest]).validate_python(list(state["models"]))
        if any(
            key is not None and key not in state["models"]
            for key in (state["current"], state["previous_approved"])
        ):
            raise ValueError("Invalid registry pointer")
        return state

    def _save(self, state: dict[str, Any], action: str, model: str, reason: str) -> None:
        state["events"].append(
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "action": action,
                "model": model,
                "reason": reason,
            }
        )
        atomic_json(self.root / "registry.json", finish_report(state))

    def register(self, package_path: Path) -> str:
        package = verify_package(package_path)
        key = package.package_sha256
        with exclusive(self.root):
            state = self.read()
            if key in state["models"]:
                raise ValueError("Package already registered")
            target = self.root / "packages" / key
            target.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=target.parent, prefix=".register-") as temp:
                staging = Path(temp) / "package"
                shutil.copytree(package_path, staging)
                verify_package(staging, expected_hash=key)
                staging.rename(target)
            state["models"][key] = {"status": "CANDIDATE", "evaluation": None}
            self._save(state, "REGISTER", key, "verified immutable package")
        return key

    def transition(
        self, key: str, status: Status, *, reason: str, evaluation: Path | None = None
    ) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("Registry transition requires an audit reason")
        allowed = {
            "CANDIDATE": {"VALIDATED", "REJECTED"},
            "VALIDATED": {"APPROVED", "REJECTED"},
            "APPROVED": {"DEPRECATED"},
        }
        with exclusive(self.root):
            state = self.read()
            if key not in state["models"]:
                raise ValueError("Unknown model")
            entry = state["models"][key]
            if status not in allowed.get(entry["status"], set()):
                raise ValueError("Invalid registry transition")
            package = self.root / "packages" / key
            verify_package(package, expected_hash=key)
            if status == "VALIDATED":
                if evaluation is None:
                    raise ValueError("Full evaluation directory required")
                result = verify_evaluation(evaluation, package)
                if not result["promotion_allowed"]:
                    raise ValueError("Regression/downstream gates failed")
                target = self.root / "evaluations" / key
                target.parent.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(dir=target.parent, prefix=".evidence-") as temp:
                    staging = Path(temp) / "evaluation"
                    staging.mkdir()
                    for name in ("ticket", "policy", "vision", "analytics", "comparison"):
                        shutil.copyfile(evaluation / f"{name}.json", staging / f"{name}.json")
                    verify_evaluation(staging, package)
                    staging.rename(target)
                entry["evaluation"] = result["report_sha256"]
            elif status == "APPROVED":
                result = verify_evaluation(self.root / "evaluations" / key, package)
                if (
                    not result["promotion_allowed"]
                    or result["report_sha256"] != entry["evaluation"]
                ):
                    raise ValueError("Approval evidence changed")
                state["previous_approved"] = state["current"]
                state["current"] = key
            elif status == "DEPRECATED" and key in (state["current"], state["previous_approved"]):
                raise ValueError("Cannot deprecate production or its rollback target")
            entry["status"] = status
            self._save(state, status, key, reason)
            return state

    def rollback(self, *, reason: str) -> dict[str, Any]:
        if not reason.strip():
            raise ValueError("Rollback requires a reason")
        with exclusive(self.root):
            state = self.read()
            current, previous = state["current"], state["previous_approved"]
            if not current or not previous or state["models"][previous]["status"] != "APPROVED":
                raise ValueError("No previous approved model to roll back to")
            verify_package(self.root / "packages" / previous, expected_hash=previous)
            result = verify_evaluation(
                self.root / "evaluations" / previous, self.root / "packages" / previous
            )
            if not result["promotion_allowed"]:
                raise ValueError("Rollback target no longer verifies")
            state["models"][current]["status"] = "ROLLED_BACK"
            state["current"], state["previous_approved"] = previous, None
            self._save(state, "ROLLBACK", current, reason)
            return state

    def production(self) -> Path:
        state = self.read()
        key = str(state["current"]) if state["current"] else None
        if not key or state["models"][key]["status"] != "APPROVED":
            raise ValueError("No approved production model")
        root = self.root / "packages" / key
        verify_package(root, expected_hash=key)
        result = verify_evaluation(self.root / "evaluations" / key, root)
        if (
            not result["promotion_allowed"]
            or result["report_sha256"] != state["models"][key]["evaluation"]
        ):
            raise ValueError("Production approval evidence no longer verifies")
        return root

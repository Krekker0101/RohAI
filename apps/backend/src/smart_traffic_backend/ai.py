"""Research AI service; no hardware policy changes or actuator access."""

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from pydantic import Field
from traffic_core.model_registry import CentralModelRegistry
from traffic_core.models import Model
from traffic_core.settings import Timing
from traffic_simulator.learning.intelligence_contracts import ControlGeometry, PolicyMetadata
from traffic_simulator.learning.safe_actor import IsolatedPolicy
from traffic_simulator.learning.shadow import ShadowIntelligence


class ShadowSelection(Model):
    model_key: str
    reason: str = Field(min_length=1, max_length=500)
    geometry: ControlGeometry


class RollbackRequest(Model):
    reason: str = Field(min_length=1, max_length=500)


class AIService:
    def __init__(self, root: Path, timing: Timing) -> None:
        self.root, self.timing = root, timing
        self.registry: CentralModelRegistry | None = None
        self.shadow: ShadowIntelligence | None = None
        self.last_shadow: dict[str, Any] | None = None
        self.error: str | None = None
        try:
            self.registry = CentralModelRegistry(root / "registry")
        except (OSError, sqlite3.Error, ValueError) as exc:
            self.error = f"Registry unavailable: {type(exc).__name__}"
        self.lock = asyncio.Lock()

    def _load_report(self, name: str) -> dict[str, Any]:
        path = self.root / name
        if not path.is_file():
            return {"status": "EMPTY"}
        try:
            value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            return value
        except (OSError, ValueError):
            return {"status": "INVALID", "error": "Report cannot be read"}

    def snapshot(self) -> dict[str, Any]:
        registry: dict[str, Any] = {
            "models": [],
            "selections": {},
            "events": [],
            "physical_activation": False,
        }
        if self.registry is not None:
            try:
                registry = self.registry.snapshot()
            except (OSError, sqlite3.Error, ValueError) as exc:
                self.error = f"Registry integrity/read failure: {type(exc).__name__}"
        return {
            "registry": registry,
            "training": self._load_report("training.json"),
            "comparison": self._load_report("summary.json"),
            "shadow": self.shadow.snapshot()
            if self.shadow
            else self.last_shadow or {"state": "EMPTY"},
            "diagnostics": {
                "error": self.error,
                "drift": "NOT VERIFIED",
                "physical_activation": False,
            },
            "readiness": "RESEARCH ONLY: field vision and physical RL release blocked",
        }

    def close(self) -> None:
        if self.shadow:
            self.shadow.close()
            self.last_shadow = self.shadow.snapshot()
            self.shadow = None

    def start_shadow(self, body: ShadowSelection, source: str) -> None:
        if self.registry is None:
            raise PermissionError("Registry unavailable; baseline remains active")
        model = self.registry.get(body.model_key)
        if model.status != "APPROVED" or model.release_scope != "RESEARCH_SHADOW":
            raise PermissionError("Research shadow approval required")
        path = self.registry.artifact(body.model_key)
        actor = IsolatedPolicy(
            path,
            PolicyMetadata(
                policy_id=model.model_id,
                version=model.version,
                learned=True,
                weights_sha256=model.artifact_sha256,
            ),
        )
        sidecar: ShadowIntelligence | None = None
        try:
            sidecar = ShadowIntelligence(
                actor,
                self.timing,
                self.root / "shadow-decisions.sqlite3",
                "LIVE CAMERA" if source == "camera" else "RECORDED VIDEO",
                body.geometry,
            )
            self.registry.select(body.model_key, body.reason)
        except Exception:
            if sidecar is not None:
                sidecar.close()
            else:
                actor.close()
            raise
        self.close()
        self.shadow = sidecar
        self.error = None

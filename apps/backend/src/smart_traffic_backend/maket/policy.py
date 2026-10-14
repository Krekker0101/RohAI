"""Shared observation/mask/action schema, camera inputs only, frozen inference."""

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from traffic_core.models import Decision, TrafficState
from traffic_simulator.learning.contracts import ControlRequest
from traffic_simulator.learning.observation import ObservationStack, encode
from traffic_simulator.learning.policy import (
    ActuatedController,
    CurrentAdaptiveController,
    FixedTimeController,
)
from traffic_simulator.learning.safety import SafetySupervisor
from traffic_simulator.twin_models import ObservedTrafficState


def approved_stand_release(package: Path, manifest: dict[str, Any], digest: str) -> bool:
    """Local reviewed evidence gate. Filesystem writers remain trusted operators."""
    approval = manifest.get("approval", {})
    if (
        not isinstance(approval, dict)
        or approval.get("artifact_sha256") != digest
        or not (approval.get("operator_id") and approval.get("approved_at"))
    ):
        return False
    bindings = approval.get("evidence_sha256", {})
    if not isinstance(bindings, dict) or set(bindings) != {"OFFLINE", "SHADOW", "STAND"}:
        return False
    for stage in ("OFFLINE", "SHADOW", "STAND"):
        path = package / (stage.lower() + ".json")
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != bindings[stage]:
            return False
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict) or (
            report.get("status") != "MEASURED"
            or report.get("stage") != stage
            or report.get("deployment_domain") != "PHYSICAL_MAKET"
            or report.get("artifact_sha256") != digest
            or report.get("independent_evaluation") is not True
            or not isinstance(report.get("samples"), int)
            or report["samples"] <= 0
        ):
            return False
        gates = report.get("gates")
        if not isinstance(gates, list) or not gates:
            return False
        for gate in gates:
            if (
                not isinstance(gate, dict)
                or not gate.get("metric")
                or gate.get("direction") not in ("min", "max")
            ):
                return False
            actual, threshold = gate.get("measured"), gate.get("threshold")
            if any(
                isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v)
                for v in (actual, threshold)
            ):
                return False
            assert isinstance(actual, (int, float)) and isinstance(threshold, (int, float))
            if (gate["direction"] == "max" and actual > threshold) or (
                gate["direction"] == "min" and actual < threshold
            ):
                return False
    return True


class CameraPolicy:
    def __init__(self, supervisor: SafetySupervisor, package: Path | None) -> None:
        self.supervisor = supervisor
        self.name = "ADAPTIVE"
        self.stack = ObservationStack()
        self.fixed = FixedTimeController(supervisor.machine.timing)
        self.adaptive = CurrentAdaptiveController(supervisor.machine.timing)
        self.actuated = ActuatedController(supervisor.machine.timing)
        self.model = None
        self.model_id: str | None = None
        self.active_approved = False
        if package is not None:
            manifest = json.loads((package / "policy.json").read_text(encoding="utf-8"))
            weights = package / "policy.zip"
            digest = hashlib.sha256(weights.read_bytes()).hexdigest()
            if manifest.get("sha256") != digest:
                raise ValueError("Frozen policy hash mismatch")
            if manifest.get("deployment_domain") != "PHYSICAL_MAKET":
                raise ValueError("Learned policy requires explicit maket package")
            if manifest.get("observation_schema") != "ObservationV1":
                raise ValueError("Learned observation schema mismatch")
            self.active_approved = approved_stand_release(package, manifest, digest)
            # Trusted local artifact only: pickle-based PPO packages are executable code.
            from stable_baselines3 import PPO

            self.model = PPO.load(str(weights), device="cpu")
            if self.model.observation_space.shape != self.stack.shape:
                raise ValueError("Learned history shape mismatch")
            if getattr(self.model.action_space, "n", None) != 9:
                raise ValueError("Learned action schema mismatch")
            self.model_id = digest

    async def decide(self, observation: TrafficState) -> Decision:
        from traffic_simulator.learning.contracts import decode_action

        observed = ObservedTrafficState(source="live_camera_observation", traffic=observation)
        frame = encode(observed, self.supervisor)
        history = self.stack.append(frame)
        if self.name == "LEARNED_AI":
            if self.model is None:
                raise ValueError("No maket learned model loaded")
            index, _ = self.model.predict(history, deterministic=True)
            scalar = np.asarray(index).item()
            if not isinstance(scalar, int) or isinstance(scalar, bool):
                raise ValueError("Learned action must be a finite integer")
            request = decode_action(scalar)
        else:
            policies = {"FIXED": self.fixed, "ADAPTIVE": self.adaptive, "ACTUATED": self.actuated}
            if self.name not in policies:
                raise ValueError("Invalid camera policy")
            policy = policies[self.name]
            request = policy.propose(frame, history)
        return Decision(
            phase=request.requested_phase,
            green_seconds=request.requested_duration_s,
            reason="measured_inputs" if self.name != "LEARNED_AI" else "model_output",
        )


def request(decision: Decision) -> ControlRequest:
    return ControlRequest(
        requested_phase=decision.phase, requested_duration_s=decision.green_seconds
    )

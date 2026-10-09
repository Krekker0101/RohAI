"""Allowlisted numeric PPO actor. No SB3 ZIP metadata, cloudpickle or torch at inference."""

import io
import json
import multiprocessing as mp
import queue
import threading
import time
import zipfile
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

import numpy as np
from traffic_core.model_registry import sha256

from traffic_simulator.learning.contracts import FEATURES, decode_action
from traffic_simulator.learning.intelligence_contracts import (
    ControlAction,
    PolicyDecision,
    PolicyMetadata,
    PolicyState,
    SafetyConstraints,
    TrafficObservation,
)

INPUT = 4 * (3 * len(FEATURES) + 4)
SHAPES = {"w0": (64, INPUT), "b0": (64,), "w1": (64, 64), "b1": (64,), "wa": (9, 64), "ba": (9,)}
KEYS = {
    "w0": "mlp_extractor.policy_net.0.weight",
    "b0": "mlp_extractor.policy_net.0.bias",
    "w1": "mlp_extractor.policy_net.2.weight",
    "b1": "mlp_extractor.policy_net.2.bias",
    "wa": "action_net.weight",
    "ba": "action_net.bias",
}


def export_checkpoint(checkpoint: Path, expected_sha256: str, destination: Path) -> None:
    """Explicit conversion of local training tensors; ZIP data/cloudpickle is never read."""
    if checkpoint.is_symlink() or sha256(checkpoint) != expected_sha256:
        raise ValueError("Checkpoint provenance/hash mismatch")
    import torch

    with zipfile.ZipFile(checkpoint) as archive:
        info = archive.getinfo("policy.pth")
        if info.file_size > 10_000_000:
            raise ValueError("Oversized policy")
        tensors = torch.load(io.BytesIO(archive.read(info)), map_location="cpu", weights_only=True)
    arrays = {key: tensors[name].detach().numpy().astype(np.float32) for key, name in KEYS.items()}
    if any(
        arrays[key].shape != shape or not np.isfinite(arrays[key]).all()
        for key, shape in SHAPES.items()
    ):
        raise ValueError("Unsupported actor shape/nonfinite weights")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        np.savez(stream, **arrays)
    load_arrays(destination, sha256(destination))


def load_arrays(path: Path, expected_sha256: str) -> dict[str, Any]:
    if path.is_symlink() or path.stat().st_size > 2_000_000:
        raise ValueError("Unsafe artifact size/type")
    # Hash the same bytes that are decoded; avoid verify-then-open races.
    raw = path.read_bytes()
    import hashlib

    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("Actor integrity failure")
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        if sorted(archive.namelist()) != sorted(k + ".npy" for k in SHAPES):
            raise ValueError("Unexpected actor tensor inventory")
        if any(
            i.file_size > 500_000 or i.compress_type != zipfile.ZIP_STORED
            for i in archive.infolist()
        ):
            raise ValueError("Compressed/oversized tensor rejected")
    with np.load(io.BytesIO(raw), allow_pickle=False) as data:
        result = {key: data[key] for key in SHAPES}
    if any(
        result[key].shape != shape
        or result[key].dtype != np.float32
        or not np.isfinite(result[key]).all()
        for key, shape in SHAPES.items()
    ):
        raise ValueError("Actor numeric compatibility failure")
    return result


class NumericPolicy:
    def __init__(self, path: Path, metadata: PolicyMetadata) -> None:
        if metadata.weights_sha256 is None:
            raise ValueError("Weights identity required")
        self.arrays = load_arrays(path, metadata.weights_sha256)
        self.metadata = metadata

    def decide(
        self, observation: TrafficObservation, state: PolicyState, constraints: SafetyConstraints
    ) -> PolicyDecision:
        if len(state.history) != 4:
            raise ValueError("Actor requires four history frames")
        g = observation.geometry
        history = np.asarray(state.history, dtype=np.float32)
        geometry = np.tile(
            np.asarray(
                [g.lanes_per_approach / 8, g.lane_length_m / 1000, g.headway_s / 10],
                dtype=np.float32,
            ),
            (4, 1),
        )
        x = np.concatenate((history, geometry), axis=1).reshape(INPUT)
        a = self.arrays
        hidden = np.tanh(a["w0"] @ x + a["b0"])
        hidden = np.tanh(a["w1"] @ hidden + a["b1"])
        logits = a["wa"] @ hidden + a["ba"]
        index = int(np.argmax(logits))
        return PolicyDecision(
            metadata=self.metadata,
            proposed=ControlAction.model_validate(decode_action(index).model_dump()),
            available_phases=constraints.allowed_phases,
            facts={"action_index": float(index), "winning_logit": float(logits[index])},
        )


def _worker(channel: Connection, path: str, metadata_json: str) -> None:
    try:
        policy = NumericPolicy(Path(path), PolicyMetadata.model_validate_json(metadata_json))
        channel.send_bytes(b'{"ready":true}')
        while True:
            raw = channel.recv_bytes(256_000)
            if raw == b"STOP":
                break
            task = json.loads(raw)
            try:
                started = time.perf_counter()
                decision = policy.decide(
                    TrafficObservation.model_validate(task["observation"]),
                    PolicyState.model_validate(task["state"]),
                    SafetyConstraints.model_validate(task["constraints"]),
                )
                result = {
                    "decision": decision.model_dump(mode="json"),
                    "compute_ms": (time.perf_counter() - started) * 1000,
                }
            except Exception as exc:
                result = {"error": type(exc).__name__}
            channel.send_bytes(json.dumps(result, allow_nan=False).encode())
    except (EOFError, BrokenPipeError, OSError, ValueError):
        pass
    finally:
        channel.close()


class IsolatedPolicy:
    """Spawned allowlisted worker; JSON IPC, one outstanding job, hard timeout quarantine."""

    def __init__(self, path: Path, metadata: PolicyMetadata) -> None:
        self.metadata = metadata
        context = mp.get_context("spawn")
        self.channel, child = context.Pipe()
        self.process = context.Process(
            target=_worker,
            args=(child, str(path.resolve()), metadata.model_dump_json()),
            daemon=True,
        )
        self.closed = False
        self.last_compute_ms: float | None = None
        self.process.start()
        child.close()
        if not self.channel.poll(10):
            self.close()
            raise TimeoutError("Actor startup timeout")
        try:
            if json.loads(self.channel.recv_bytes(256_000)) != {"ready": True}:
                raise ValueError("Actor startup failed")
        except (EOFError, OSError, ValueError):
            self.close()
            raise ValueError("Actor incompatible/corrupt") from None

    def decide(
        self, observation: TrafficObservation, state: PolicyState, constraints: SafetyConstraints
    ) -> PolicyDecision:
        if self.closed:
            raise RuntimeError("Actor quarantined")
        try:
            payload = json.dumps(
                {
                    "observation": observation.model_dump(mode="json"),
                    "state": state.model_dump(mode="json"),
                    "constraints": constraints.model_dump(mode="json"),
                },
                allow_nan=False,
            ).encode()
            sent: queue.Queue[Exception | None] = queue.Queue(maxsize=1)

            def send() -> None:
                try:
                    self.channel.send_bytes(payload)
                    sent.put_nowait(None)
                except (OSError, ValueError) as exc:
                    sent.put_nowait(exc)

            sender = threading.Thread(target=send, daemon=True, name="rohai-actor-json-send")
            sender.start()
            deadline = time.monotonic() + constraints.inference_timeout_s
            try:
                error = sent.get(timeout=constraints.inference_timeout_s)
            except queue.Empty:
                raise TimeoutError("inference_send_timeout") from None
            if error is not None:
                raise ValueError("Actor pipe unavailable") from error
            if not self.channel.poll(max(0, deadline - time.monotonic())):
                raise TimeoutError("inference_timeout")
            result = json.loads(self.channel.recv_bytes(256_000))
            if "error" in result:
                raise ValueError(result["error"])
            decision = PolicyDecision.model_validate(result["decision"])
            if decision.metadata != self.metadata:
                raise ValueError("Actor identity changed")
            self.last_compute_ms = float(result["compute_ms"])
            return decision
        except (EOFError, OSError, ValueError, TimeoutError):
            self.close()
            raise

    def close(self) -> None:
        self.closed = True
        if self.process.is_alive():
            self.process.terminate()
        self.process.join(timeout=0.2)
        if self.process.is_alive():
            self.process.kill()
            self.process.join(timeout=0.2)
        self.channel.close()

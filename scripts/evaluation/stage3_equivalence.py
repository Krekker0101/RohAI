"""Compare safe actor with original tensor forward pass; no SB3/cloudpickle deserialization."""

import json
import zipfile
from pathlib import Path

import numpy as np
import torch
from traffic_core.model_registry import sha256
from traffic_simulator.learning.intelligence_contracts import (
    PolicyMetadata,
    PolicyState,
    SafetyConstraints,
    TrafficObservation,
)
from traffic_simulator.learning.safe_actor import KEYS, NumericPolicy


def main() -> None:
    root = Path("artifacts/rohai-ai-2.0/stage3")
    plan = json.loads((root / "plan.json").read_text(encoding="utf-8"))
    torch.set_num_threads(1)
    results = []
    for model in plan["models"]:
        checkpoint = Path(model["weights_path"])
        if sha256(checkpoint) != model["weights_sha256"]:
            raise ValueError("Source checkpoint changed")
        with zipfile.ZipFile(checkpoint) as archive:
            tensors = torch.load(archive.open("policy.pth"), map_location="cpu", weights_only=True)
        actor = NumericPolicy(
            Path(model["actor"]),
            PolicyMetadata(
                policy_id=model["model_id"],
                version="safe-3.1",
                learned=True,
                weights_sha256=model["actor_sha256"],
            ),
        )
        assert all(np.array_equal(actor.arrays[k], tensors[v].numpy()) for k, v in KEYS.items())
        paths = sorted((root / "evaluation").glob("*" + model["model_id"] + ".decisions.json"))[:3]
        samples, mismatches = 0, 0
        for path in paths:
            audits = json.loads(path.read_text(encoding="utf-8"))
            rows = []
            for audit in audits:
                observation = TrafficObservation.model_validate(audit["observation"])
                row = (*observation.values, *observation.valid, *observation.clipped, 1)
                rows.append(row)
                zeros = (0.0,) * len(row)
                history = tuple([zeros] * max(0, 4 - len(rows)) + rows[-4:])
                state = PolicyState(sequence=samples, history=history)
                g = observation.geometry
                x = np.concatenate(
                    (
                        np.asarray(history, dtype=np.float32),
                        np.tile(
                            np.asarray(
                                [
                                    g.lanes_per_approach / 8,
                                    g.lane_length_m / 1000,
                                    g.headway_s / 10,
                                ],
                                dtype=np.float32,
                            ),
                            (4, 1),
                        ),
                    ),
                    axis=1,
                ).reshape(-1)
                with torch.inference_mode():
                    hidden = torch.tanh(
                        torch.nn.functional.linear(
                            torch.from_numpy(x), tensors[KEYS["w0"]], tensors[KEYS["b0"]]
                        )
                    )
                    hidden = torch.tanh(
                        torch.nn.functional.linear(hidden, tensors[KEYS["w1"]], tensors[KEYS["b1"]])
                    )
                    logits = torch.nn.functional.linear(
                        hidden, tensors[KEYS["wa"]], tensors[KEYS["ba"]]
                    )
                    expected = int(logits.argmax())
                decision = actor.decide(observation, state, SafetyConstraints())
                mismatches += int(decision.facts["action_index"] != expected)
                samples += 1
        results.append(
            {
                "model": model["key"],
                "samples": samples,
                "mismatches": mismatches,
                "tensor_values_identical": True,
                "checkpoint_sha256": sha256(checkpoint),
            }
        )
        if not samples or mismatches:
            raise ValueError("Exported actor differs from original numeric network")
    with (root / "actor-equivalence.json").open("x", encoding="utf-8") as stream:
        json.dump(
            {
                "status": "PASS",
                "results": results,
                "scope": "Original tensor forward pass; measured histories only",
            },
            stream,
            indent=2,
        )
    print(json.dumps(results))


if __name__ == "__main__":
    main()

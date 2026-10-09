# Central model registry — Stage 3

The research registry is `artifacts/rohai-ai-2.0/stage3/registry/registry.sqlite3`.
`traffic_core.model_registry.CentralModelRegistry` manages Vision inventory and executable
Control policies. The existing Vision package registry and original YOLO selection remain
compatible; this new registry never silently replaces the production detector.

Each immutable `model_id@version` records architecture, parent, dataset and training-code
versions, configuration, training/evaluation results, compatibility, provenance, license,
creation time and artifact SHA256. Unknown schema, missing metadata, nonfinite numbers,
identity mismatch and incompatible architecture are rejected.

Transitions are EXPERIMENTAL → EVALUATED → APPROVED/REJECTED → RETIRED. EXPERIMENTAL may
also be rejected directly. APPROVED permits RESEARCH_SHADOW only. Approval never selects
a model. Selection requires an explicit reason and produces an audit event. A selected
model cannot be retired. Rollback verifies the previous approved artifact and stops
inference; resuming observation requires another explicit Shadow start.

SQLite `BEGIN IMMEDIATE` serializes mutations. Metadata bodies have canonical digests;
artifacts are copied and checked again. Release evidence must match the exact frozen report,
model key, weights, dataset and training-code version. Evaluation source hashes are recorded
separately because evaluation can legitimately run newer code than training.

Six actual PPO checkpoints were converted to allowlisted float32 NPZ tensors. The converter
reads only `policy.pth` with explicit `weights_only=True`; it never reads SB3's executable
`data` metadata. This converter is for known local training outputs, **not uploaded models**.
The runtime uses NumPy with pickle disabled, exact tensor shapes/inventory and bounded input.
Known architecture is PPO MLP 64/64, Tanh, nine discrete actions, four histories plus three
public geometry features. Original tensors and exported actions are checked in
`actor-equivalence.json`. No generic plugin/model code is imported.

Vision `.pt` files are inventoried as inert `.archive` objects. This registry does not
deserialize or execute them. Original YOLO still uses its existing explicitly configured,
pinned trusted local checkpoint. No arbitrary model upload endpoint is provided. Existing
training/resume code uses trusted local framework checkpoints/RNG; it is not an untrusted
deserialization boundary. Checksums detect corruption, not malicious authorship by a person
who already controls the registry filesystem.

Inspect: `GET /api/v1/ai`. Operator-authenticated actions: `POST /api/v1/ai/shadow`,
`POST /api/v1/ai/shadow/stop`, `POST /api/v1/ai/rollback`. Physical scope is forbidden.

Sources: [NumPy load security](https://numpy.org/doc/stable/reference/generated/numpy.load.html),
[PyTorch serialization](https://docs.pytorch.org/docs/stable/notes/serialization.html),
[Ultralytics licensing](https://www.ultralytics.com/license). A restrictive tensor loader
does not make arbitrary third-party Torch checkpoints trusted.

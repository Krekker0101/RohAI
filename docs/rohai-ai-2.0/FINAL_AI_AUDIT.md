# Final AI engineering audit — 2026-10-09

Code and actual artifacts were inspected; documentation presence alone was not acceptance.
Original YOLO/Adaptive and RohAI 1.0 safety/runtime tests remain enabled.

| Finding | Severity / root cause | Correction / evidence | Regression |
|---|---|---|---|
| Unsafe SB3 user deserialization | HIGH: ZIP metadata can contain cloudpickle | No SB3 load in research runtime; exact numeric tensor export/known architecture, inert Vision archives | Numeric loader corruption/object/shape tests; 2,160 measured tensor/action equivalence checks |
| Native actor survives timeout | HIGH: daemon inference thread cannot preempt native calls | SHADOW requires a terminable process, JSON IPC, quarantine and bounded shutdown | Hard hung-worker termination test |
| Blocking IPC send | HIGH: dead worker can stop reading a pipe | Deadline also covers JSON send; worker termination releases pipe writer | Hung-worker regression |
| NPY allocation bomb | HIGH: small archive header can declare huge arrays | Validate shape/type/order and exact payload length before allocation; bounded same-byte read | Huge declared-shape regression |
| Source/controller clock mismatch | HIGH: recorded PTS is not runtime monotonic time | Bridge uses controller clock while retaining measured source evidence/age | Actual recorded Shadow acceptance |
| Invented field service history | HIGH: runtime does not expose prior phase service timestamps | Unknown service and pending budget explicitly masked; no field fairness claim | Shadow mask assertion |
| Model history cadence mismatch | MEDIUM: camera control ticks can be faster than trained one-second decisions | Explicit one-second observational sampling with a separate counter | Shadow sampling/compatibility regression |
| Actor/safety input mismatch | HIGH: only clock/phase checked | Compare all common normalized values, validity and clipping; reject changed queue input | Forged-feature test |
| Missing common release registry | MEDIUM: separate Vision/control lifecycles | Central transactional metadata/status/evidence/selection journal | Lifecycle, scope, corrupt metadata/artifact, rollback tests |
| Corrupt AI database blocks baseline startup | HIGH: optional AI service initialized in shared lifespan | AI registry failures become diagnostics; original runtime still starts | Corrupt-registry startup regression |
| Sidecar leak after failed selection | MEDIUM: actor closed but newly started observer thread survived | Close both resources when selection fails after startup | Native worker/sidecar cleanup regression |
| Ineffective CPU thread budget | MEDIUM: Ultralytics resets Torch threads during setup | Enforce configured limit after warm-up; report actual pool size | Real detector thread-budget integration test |
| Unknown age shown as zero | MEDIUM: diagnostic placeholder mistaken for fresh input | Missing age is null/NOT MEASURED; stale guard remains active | Shadow input masks/fallback suite |
| Browser Start race | LOW: test observed calls before asynchronous POST finished | Poll actual completed request instead of assuming click waits for HTTP | Full 14-test E2E rerun |
| Evidence copied between models | HIGH: version/hash mismatch | Binding checked against frozen evaluation payload and immutable artifact | Registry integrity and independent-gate rejection tests |
| Accidental RL physical execution | CRITICAL potential: admission confused with actuation | Only RESEARCH_SHADOW scope; no hardware import/path in sidecar; simulation start rejected | Physical-scope test, AST dependency guard, original safety suite |
| Missing live integration | MEDIUM: simulation-only decisions | Nonblocking refreshed TrafficState bridge, same input to reference/candidate, independent safety copies | Sidecar copy isolation test and recorded-video proof |
| Counterfactual field performance | HIGH methodological risk: proposal not executed | Field counterfactual delay null; simulated outcomes in separate section | Sidecar trace assertions |
| Leakage / label provenance | HIGH if unchecked | Existing sealed source/cohort splits, duplicate checks, rights, exhaustive reviewed labels; model proposals never GT | Dataset foundation and AI20 dataset suites |
| Inflated field model claim | HIGH methodological risk: rectangle fixture | Fixture deployment rejected; field training remains blocked | Package/registry fixture rejection |
| PPO nonresponsive / poor result | HIGH research limitation: short budget and near-constant observed action sequences | Preserve all seeds/results; no tuning to old TEST; fresh suite and strong baselines | Benchmark pairing/invariants, immutable final protocol |
| Unknown cause of weak PPO | NOT VERIFIED: insufficient evidence for causal diagnosis | Record hypotheses, not an asserted overfitting diagnosis | Actual optimizer logs/checkpoints retained |
| Wrong forecast recovery target | MEDIUM: late recovered sample substituted for exact horizon | Stage 2 exact-target postprocessor excludes missing/right-censored targets | Existing forecast recovery regression |
| Delay denominator bias | HIGH risk: completed trips omit pending entities | All-arrival right-censored delay, pedestrian/downstream costs explicit; units preserved | Baseline metric / digital-twin tests |
| Reward oracle leakage | HIGH risk: GT actor access | GT used only by reward/scorer; actor gets observation masks and public geometry | Existing observed-only learning/safety tests |
| Checkpoint incompleteness/nondeterminism | MEDIUM | Episode/rollout boundaries, optimizer/RNG hash checks, deterministic seed configuration; fresh train/export smoke | Existing exact-resume tests and Stage 3 smoke artifacts |
| Unbounded monitoring | MEDIUM: growing history/jobs | One queued input/request, 128 UI records, 5,000 journal retention, explicit drop count | Queue/lifecycle checks; retention documented |
| Frontend/backend contract drift | MEDIUM | Typed dashboard data, cancelled polling, six lazy-loaded sections, explicit EMPTY states | Typecheck/lint/unit/E2E and empty API test |
| Invalid physical PASS | HIGH reporting risk: no hardware | Eight HIL tests remain skipped; no replacement with mock evidence | Full-suite skip reasons and acceptance matrix |

The broad simulator fingerprint includes Shadow code even where it does not affect simulation.
Exact preregistered source is preserved in `stage3/frozen-source`. Later import typing,
NPY header hardening and camera-only Shadow mask/clock/fallback fixes leave the evaluated
SIMULATION numerical actor unchanged; their runtime qualification is tested separately.
Reports retain the actual original protocol hash.
Reproduction must use that snapshot rather than rewriting historical hashes to current code.

Checksums are integrity controls, not a sandbox for hostile code or authentication against a
filesystem administrator. No generic uploaded Torch/SB3 execution is supported. Resource
bounds are fixed tensor/input sizes, single outstanding work and process lifetime; an OS-level
RSS quota, certified hard real-time scheduling and field-calibrated drift remain limitations.

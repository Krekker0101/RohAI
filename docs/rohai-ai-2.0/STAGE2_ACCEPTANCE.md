# AI 2.0 acceptance after Stage 3

Status meanings: PASS = directly verified within stated scope; FAIL = measured criterion
not met; BLOCKED = necessary input/resource absent; NOT VERIFIED = not directly tested.

| Area | Status | Scope / evidence |
|---|---|---|
| Vision dataset/training infrastructure | PASS | Sealed lineage/splits/rights, actual CPU one-epoch smoke, package fixture refusal |
| Actually trained Vision model | PASS | Synthetic rectangle fixtures only; actual best/last weights and logs |
| Field Vision model | BLOCKED | No independent licensed human-reviewed field dataset |
| Field Vision independent evaluation/release | BLOCKED | No queue/wait/tracking GT or valid frozen field gates |
| RL training infrastructure | PASS | Checkpoint/resume tests, six prior final-budget models and fresh two-seed train/export smoke |
| Actually trained RL | PASS | PPO smoke tensors, optimizer logs and configuration; not convergence |
| RL strong-baseline superiority | FAIL | Stage 2 models worse than Actuated/pressure; fresh final results in benchmark report |
| Independent simulation evaluation | PASS | New 26-case held-out tapes, four baselines/all six fixed prior models, pairing/invariants/CI |
| Independent road evaluation | BLOCKED | Uncalibrated simulator does not supply field evidence |
| Central registry / integrity / rollback | PASS | Transactional registry, known numeric loader, lifecycle/corruption/scope tests |
| Vision safe generic execution in central registry | BLOCKED | Inventory-only archives; no arbitrary checkpoint loader |
| Shadow software integration | PASS | Same input, separate safety copies, recorded-video observational proof, no hardware imports |
| LIVE CAMERA Shadow | NOT VERIFIED | Camera not used for acceptance |
| Software safety / RohAI 1.0 regression | PASS | Full Python suite, original guards enabled, bounded worker/fallback tests |
| Physical HIL | NOT VERIFIED | Eight real-device tests skipped; no ESP32 configured |
| CPU recorded performance | PASS | Isolated repeat, retained historical baseline, separately labelled recorded source |
| Recorded CPU latency/FPS non-regression | FAIL | Current 2-thread p95 181.87ms / 6.61 FPS vs historical 45.63ms / 12.49 FPS; causes not fully established |
| Physical camera-to-ACK performance | NOT VERIFIED | No physical instrumentation/equipment |
| Target hardware / OS RSS quota / field drift | NOT VERIFIED | Software bounds tested; field qualification absent |
| Frontend / E2E | PASS | Typecheck/lint/build, 12 unit tests, 14 browser tests; owned services closed |
| AI 2.0 complete field deployment | BLOCKED | Field data, model quality, target budgets and HIL gates do not pass |

Detailed measured totals, trained model inventory, resources, hashes and warnings are in
`AI_BENCHMARK_REPORT.md` and `artifacts/rohai-ai-2.0/stage3/`. This matrix never treats
synthetic training, recorded video, live camera and HIL as interchangeable evidence.

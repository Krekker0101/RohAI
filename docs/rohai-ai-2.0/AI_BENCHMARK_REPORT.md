# Final benchmark methodology and evidence

Stage 3 uses 26 fresh SIMULATION cases: thirteen families with two new tape seeds each,
varying demand, asymmetry, changes/bursts, night degradation, partial/full outages, spillback,
lane geometry, headway and perception noise. All six fixed prior final-budget PPO actors
and four baselines see identical per-case arrivals. Models, seeds, budgets, reward and gates
are frozen before this evaluation. No TEST selection or retraining is performed on this suite.

`stage3/summary.json` contains aggregate metrics, paired comparisons and familywise-corrected
intervals; `metrics.csv` contains every policy/model/scenario. Raw reports and decision traces
are retained under `evaluation/`. `comparison.png/svg` are actual plotted results, not mock data.
All-arrival average delay is a right-censored lower bound; throughput includes modeled entities;
fairness is maximum direction wait gap in seconds. Unsafe final signal invariants are checked.
Aggregates are case-weighted means of per-case metrics, not a pooled arrival-weighted road
statistic. Learned summaries weight the six fixed models equally within every case.

Confidence intervals bootstrap paired scenario differences conditional on these six fixed
smoke models and the finite synthetic case suite. Family realizations are correlated; these
intervals do not quantify arbitrary roads, all training seeds or field sensor uncertainty.
The ensemble comparison cannot justify cherry-picking an individual seed. Pressure without
downstream counters is an upstream queue proxy, not full movement/network max-pressure.

Historical Stage 2 mean total delay/entity·s per 120s case: Fixed 7578.65, Adaptive 7608.67,
Actuated 6680.94, upstream-pressure 6676.71, PPO2400 7814.30, PPO4800 8022.73. Increasing
budget did not produce an improvement. Near-constant validation actions are observed; the
causal reason is not proven. New final values are appended from immutable measured artifacts.

Evidence domains remain separate:

| Domain | What was measured |
|---|---|
| SIMULATION | Closed-loop outcomes, training, fairness/starvation and safety invariants |
| RECORDED VIDEO | Original YOLO pipeline CPU performance and observational Shadow decisions; no traffic GT |
| LIVE CAMERA | NOT VERIFIED |
| PHYSICAL HIL | NOT VERIFIED; eight tests skipped |

Reproduce in a separate workspace: restore paths from `stage3/frozen-source`, copy the
original plan and numeric actors into an otherwise empty Stage 3 output directory, use
recorded dependency versions, then run `python scripts/evaluation/stage3_release.py benchmark`.
The runner refuses changed source or overwriting reports. Current runtime has loader hardening
and camera-only Shadow fixes qualified separately; do not rewrite frozen hashes. For a new experiment use a new
output/protocol and freeze it before seeing results. Training smoke is separately reproducible
with `python scripts/evaluation/stage3_smoke.py`; it never creates a field release.

`actor-equivalence.json` compares original Torch tensors/forward pass with the numeric actor:
six models ×360 observed histories, zero action mismatches. This proves conversion on those
samples, not all possible inputs. CPU inference timing is measured separately from process
IPC and physical latency. GPU utilization/queue MAE are NOT MEASURED without appropriate GT.

## Measured final results

26 fresh cases, 260 episodes; all final safety invariants passed.

| Policy | Mean censored delay s/arrival | Mean queue objects | Departures/case | Fairness gap s | Starved entities/case |
|---|---:|---:|---:|---:|---:|
| fixed | 35.23 | 54.79 | 65.96 | 29.71 | 1.81 |
| adaptive | 36.30 | 56.83 | 61.81 | 29.68 | 1.96 |
| actuated | 31.57 | 48.91 | 72.42 | 29.51 | 2.35 |
| max_pressure | 31.44 | 49.43 | 72.12 | 31.18 | 1.85 |
| rohai_ppo | 38.51 | 58.03 | 57.35 | 49.41 | 5.76 |

PPO mean delay is 22.0% higher than Actuated (38.51 vs 31.57s), with worse fairness/starvation. Scientific target: FAIL. No physical promotion.

| Comparator | PPO delay delta s | Conditional simultaneous CI |
|---|---:|---|
| fixed | 3.28 | 1.27 to 5.72 |
| adaptive | 2.21 | 0.19 to 4.46 |
| actuated | 6.94 | 4.32 to 9.90 |
| max_pressure | 7.07 | 4.34 to 10.15 |

## Model and artifact inventory

71 registry entries: 68 completed PPO runs (60 historical v1; six Stage 2 final-budget; two new Stage 3 240-step smoke), two synthetic Vision one-epoch runs, one external pretrained COCO baseline. 70 local training runs are visible. Six Control entries are approved for research Shadow only; 63 experimental inventory entries; two rejected Vision fixtures. Field Vision/full-budget converged RL/physical learned control are not trained or qualified.

Exact IDs, versions, lineage and every checkpoint path/hash are in `stage3/model-inventory.json` and `stage3/all-model-artifacts.json`. Intermediate checkpoints are not additional independently trained models. Historical archives are not executable.

Actual new Vision smoke: one CPU epoch on the sealed owned fixture; best/last retained, six synthetic validation examples mAP=0. The two new PPO smoke seeds 3001/3002 each completed 240 steps and train/export, without independent release evaluation. Resources: Vision wall 51.52s, sampled tree RSS 920,465,408 bytes, CPU 92.80s; Control wall 26.32s, RSS 351,830,016 bytes, CPU 24.27s. Measurements included concurrent verification load, not isolated training speed. Initial one-seed control config failed preflight and was corrected before training; its log is preserved.

## Performance comparison (CPU recorded video only)

| Measurement | Historical isolated Stage 1 | Current enforced two-thread runtime |
|---|---:|---:|
| Inference FPS | 12.49 | 6.61 |
| Inference p50 ms | 41.06 | 151.69 |
| Inference p95 ms | 45.63 | 181.87 |
| Capture-to-publish p95 ms | 139.72 | 343.75 |
| Peak RSS MiB | 445.58 | 442.61 |
| CPU percent (100 = one core) | 751.22 | 207.09 |
| Capture drops | 0.00 | 51.00 |

The two preceding Stage 3 repeats at the inherited eight-thread behavior measured 8.00/7.65 FPS, p95 136.06/146.07ms, CPU 802.60/790.88%, and 38/42 drops. They are preserved. Ultralytics resets the configured pool during setup; post-setup enforcement now actually uses two threads and reduces CPU to 207.09%, but FPS is 6.61 and p95 181.87ms with 51 drops. Non-regression is FAIL; the entire historical-to-current slowdown is not causally established. Do not replace this evidence with a favorable run or claim a field latency gate passed. Memory is stable (~442.61MiB); no GPU or camera-to-ACK measurement was made. Queue/count MAE remain unavailable without GT.

Control process/JSON inference p95 measured across the six known actors: 7.53-8.30ms in the normal-load qualification sample. Simulation timings were collected during concurrent verification and are not a hardware SLA. Resource limits and native timeout/termination were separately tested.

## Shadow and system acceptance

Qualified recorded Shadow: eight actual one-second-cadence decisions and eight numeric model proposals on real decoded file frames. Minimum measured confidence remains zero in every trace; all candidate proposals therefore stay under mandatory low-confidence fallback. Source/clock/input hashes, separate baseline/candidate validation, stop and explicit rollback are retained in `recorded-shadow-qualified.json`; the original Adaptive policy stays unchanged. Counterfactual road delay is null. Earlier 31-trace diagnostic runs are preserved separately.

Full Python: 506 passed, eight real HIL skipped. After observational cadence hardening, the 19 affected engine/registry/sidecar tests also passed. Ruff/format and Mypy passed (186 files); frontend typecheck/lint/build and 12 unit tests passed, 11 Maket overlay tests passed, E2E 14/14 passed with owned services closed. Training, tensor inference, integrity, rollback, cleanup, safety and original RohAI 1.0 paths were exercised. Field deployment remains BLOCKED; LIVE CAMERA and PHYSICAL HIL remain NOT VERIFIED.

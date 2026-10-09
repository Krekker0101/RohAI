# Remaining limitations

* No licensed, independently human-reviewed real-road or physical-maket training release.
  Two completed YOLO rectangle smoke runs prove training mechanics only. Field Vision is
  NOT TRAINED; queue MAE has no independent GT. Original pretrained COCO remains the baseline.
* Six Stage 2 final-budget PPO models are real, but short smoke training is not convergence.
  Two additional Stage 3 smoke seeds exercise train/export; they have no independent release
  evaluation. Existing full-budget configuration remains unexecuted. No seed is selected
  for superior performance, and the scientific improvement target has not been demonstrated.
* The simulator is an uncalibrated engineering twin. Pressure is upstream-only without exit
  counters; stops are modeled queue-entry proxies; delay includes right-censored unfinished
  arrivals. Finite source-family realizations cannot establish population-level road results.
* Research Control approval is deliberately weaker than physical readiness. Field geometry,
  service history, noise, arrival processes and model uncertainty need calibration. Shadow
  compares validated proposals, not actual counterfactual delay or physical actuator outcomes.
* LIVE CAMERA and PHYSICAL HIL are NOT VERIFIED. No ESP32 upload or rewrite occurs.
  Recorded CPU performance is separate from physical camera-to-ACK.
* Vision registry inventory accepts inert archived known checkpoints; generic safe executable
  Vision import is not implemented. Existing local YOLO training/resume remains a trusted
  operator workflow, not an arbitrary user-upload service. License/provenance gaps remain
  explicit, including missing original upstream acquisition evidence.
* Process isolation controls hangs and inputs, but is not a hostile-code sandbox or an
  OS-enforced memory quota. Very large BLAS pools and target-device tail latency must be
  measured before any future physical release. No GPU utilization was measured in CPU runs.
* Recorded FPS/latency non-regression is FAIL. The enforced two-thread setting reduces CPU
  load but currently measures 6.61 FPS, p95 inference 181.87ms and 51 capture drops. The
  historical isolated measurement was 12.49 FPS / 45.63ms / zero drops. The full cause of
  the session-to-session slowdown is not established; target-hardware profiling is still
  necessary. Original model and input resolution were retained; no favorable run was substituted.
* Legacy simulation/injected-proposer harnesses retain trusted threaded callbacks. New
  registry Control Shadow uses the terminable process path; this is not a hard-real-time
  certification for all older developer harnesses.
* Drift is NOT VERIFIED without a reference dataset. Journal retention is 5,000 decisions;
  dropped inputs are counted. Approved selection does not automatically restart on boot.
* Exact Stage 3 evaluation source is archived. Older Stage 2 training has code hashes and
  dirty-tree provenance, but its entire prior source snapshot was not archived before this
  continuation. Exact reconstruction of that old training environment remains NOT VERIFIED;
  current-code resume correctly refuses a mismatched old plan instead of rewriting hashes.

Completion path: acquire rights and independent GT → seal source-separated release → freeze
Vision and target-hardware gates → train/evaluate without TEST tuning → improve Control on
new preregistered training/validation experiments → safe model qualification → recorded and
live observational Shadow → independently specified physical HIL. Until those gates pass,
RohAI AI 2.0 is an experimental research platform, not a completed field intelligence release.

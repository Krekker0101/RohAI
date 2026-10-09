# Preregistered release criteria

Stage 3 simulation protocol `rohai-final-simulation-3.1` and research release protocol
`rohai-release-3.1` are frozen in `stage3/plan.json` before fresh results. All six prior
final-budget models are included. No TEST-based model/seed selection, reward changes or
hyperparameter tuning occurs. The scientific target of 10–15% delay reduction is aspirational;
it is not a threshold adjusted after results.

## Control: RESEARCH_SHADOW

Required automatic gates: exact evidence/model/artifact/dataset/code binding; compatible
allowlisted numeric actor; independent held-out tapes; successful safety invariants; zero
invalid outputs after validation; measured inference p95 ≤100ms; failure scenarios; measured
fairness and starvation; comparison with Actuated and upstream-pressure baselines.
The deadline derives from the existing actor budget. This is software admission, not a
physical real-time guarantee. Failing any required gate blocks approval.

Research approval allows examining a poor model and its rejections. It is never a claim of
superiority. `superiority_proven=false`, warnings and physical prohibition remain visible.
No automatic activation follows approval. The reviewed ensemble's comparison does not
establish superiority of any individually selected seed.

## Control: physical release

No physical release scope exists in the registry/API. A future protocol requires independently
calibrated field demand/perception, strong-baseline non-regression, acceptable starvation and
fairness, tail latency/resource budgets on target hardware, measured failure recovery and
physical HIL. These conditions are BLOCKED/NOT VERIFIED. Current PPO models fail the strong
baseline scientific target. No unsafe-action proposal can bypass core yellow/all-red,
minimum/maximum green or pedestrian clearance.

## Vision

The existing `traffic_vision.training.gates` protocol requires licensed, human-reviewed
independent TEST detection and downstream GT, sealed source-group splits, fixed evaluation
settings/classes, matching baseline/candidate protocol, no regression in detection/tracking,
critical classes/strata, queue/wait/count errors and false-reliable observability. A measured
application-specific green-error budget must be frozen before training. Unavailable metrics
never become zero or PASS. Latency must also be reviewed against the recorded baseline and
the target deployment budget; recorded CPU latency is not camera-to-ACK.

Vision field gates cannot be frozen honestly with the current rectangle fixture. Its
synthetic annotation source and six zero-recall held-out examples cannot support field
release. Central Vision entries remain inventory-only; executable research admission is
currently implemented for numeric Control actors. An additional safe Vision execution
format/qualification remains necessary for central untrusted-model loading.

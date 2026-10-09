# Shadow intelligence

`traffic_simulator.learning.shadow` is a sidecar over observed `TrafficState`, not a lamp
controller. It has no backend hardware/serial/ESP imports. The VisionRuntime bridge submits
the refreshed camera/video state and a deep copy of the actual core safety machine using
a bounded nonblocking queue. No model, disk or journal work runs on the physical control loop.
The sidecar samples at a one-second decision cadence matching these trained actors; faster
runtime ticks are counted as sampled-out observations, not inference failures or queue drops.

Start with an approved research model, an attributed reason and explicit known geometry:

```json
{"model_key":"rohai-ppo-v2-2400-901@safe-3.1","reason":"Recorded-video research review",
 "geometry":{"lanes_per_approach":2,"lane_length_m":90,"headway_s":2}}
```

These numbers are examples, not calibration evidence. Configure actual geometry before
using a field source. A simulation/demo source cannot activate this endpoint. Recorded
file input is RECORDED VIDEO; camera input is LIVE CAMERA. No automatic start on boot occurs.

Reference Adaptive and candidate receive the same normalized input/history. Each proposal
is checked by an independent copy of the existing safety rules. The reference proposal is
not a claim that a model reproduces every operator/emergency decision in the physical runtime.
The actual observed signal is also recorded. Candidate validation never mutates it.

Trace records observation ID/time, policy/version/hash, complete input, original proposal,
fallback request, safety reason/status, confidence/freshness, model compute and IPC latency,
and differences. Neural explanations consist of measured action/logit facts, not invented
causal reasoning. A model rejection and acceptance of its fallback are distinct events.

No field counterfactual delay is computed: `counterfactual_delay` and `simulation_outcomes`
are null. Potential effects belong to the separately labelled SIMULATION benchmark. The
recording lacks independent traffic GT, so even recorded decision traces are not accuracy proof.

Inference uses an allowlisted spawned process and JSON IPC, one outstanding request, a
100ms deadline and quarantine/termination on failure. The original baseline/safety loop
continues. Shutdown is bounded; native inference cannot survive worker termination. Fixed
safe fallback is used for invalid/stale/low-confidence input or failed actor. The original
physical Adaptive remains unchanged.

Research Shadow may compute a displayed proposal on low-confidence masked input, but that
proposal is rejected: only the validated fallback appears as the effective request. Unknown
service history/pending budgets are masked, not invented; field fairness is NOT VERIFIED.
Source frame time and controller monotonic time are distinct; the bridge uses the latter
for safety while preserving measured evidence ages. In the current recording all confidence
minimums are zero, so measured proposals are observational and fallback remains mandatory.

The SQLite trace retains the last 5,000 decisions; UI retains 128 and shows at most 50 rows.
This is bounded rolling diagnostics, not permanent storage of every historical frame.
Archive the journal externally if longer retention is required. Queue drops and failures
are visible. Field drift remains NOT VERIFIED without a calibrated reference distribution.

Research software integration is tested with actual recorded frames. LIVE CAMERA and
PHYSICAL HIL remain NOT VERIFIED; no such devices are substituted with mocks for acceptance.

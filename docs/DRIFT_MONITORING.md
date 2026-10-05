# Drift Monitoring

DriftMonitor compares a bounded 100-sample window to a fixed reference using normalized
mean distance. Default trigger is 0.2. It reports evaluation_required and always leaves
quality_regression UNKNOWN. Drift is evidence of change, not measured model degradation.

The runtime currently monitors seven lightweight signals: object-count input proxy,
class-mixture score, observation confidence, camera failure-reason count, tracking
confidence, queue total and safety intervention frequency. Reference is the first complete
observation of the session. Incomplete queues remain unknown and trigger evaluation;
they are not labeled empty traffic. These proxies do not replace image embeddings,
per-class distribution tests, per-camera night/weather strata or queue histograms.
Class-mixture scores can miss changes with equal weighted means; a reviewed deployment
should supply representative fixed reference windows and richer features offline.

Review drift per camera/model/season, verify sample coverage and compare to independent
human-labeled benchmarks. Determine whether camera alignment, lighting, traffic patterns,
tracking or policy intervention changed. Evaluate before training, promotion or rollback.
No monitor edits model weights or issues hardware commands.

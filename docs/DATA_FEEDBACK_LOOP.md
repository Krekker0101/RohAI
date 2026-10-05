# Human Verified Data Feedback Loop

FeedbackWorkflow enforces:
collect → review → annotate → validate → version_dataset → benchmark → train → evaluate
→ approve → deploy → monitor → rollback.

Every transition records actor, evidence and evidence hash. Review, annotation,
validation and approval require explicit human verification. Use one workflow directory
per improvement batch; a rejected batch must not be moved forward with passing evidence.
Rollback is an incident step, not an instruction to roll back every healthy deployment.

Collection automatically marks low confidence, large queue change, safety intervention,
policy disagreement and available camera-condition failure evidence. hard_case_tags
also classifies supplied model disagreement, track fragmentation and unusual scene
signals. Live vision also marks track-ID churn as a fragmentation review candidate
and unknown-lane observations as unusual-scene candidates. Churn can include normal
arrivals/departures; it is not a verified tracking error. A single-model runtime cannot
manufacture model disagreement; cross-model evidence must come from an evaluation collector.
Absence of these signals means unknown, not a verified healthy scene.

All collected observations are UNVERIFIED_PREDICTION. Review candidates are not ground
truth. Reviewers select clips/frames, annotate with the existing annotation schema,
validate labels/calibration, separate camera/session/time splits, and seal datasets using
the existing immutable dataset release tools. Archive selected records and source clips
before bounded journal retention removes them; no raw video is copied by this logger.

Training and benchmarks consume versioned human-verified data offline. Independent
evaluation, accountable approval, shadow evidence and controlled stand evidence precede
release. The local workflow records these external jobs; it does not claim that a
human_verified flag itself establishes annotation correctness.

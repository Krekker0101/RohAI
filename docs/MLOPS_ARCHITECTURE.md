# Continuous AI Improvement Loop

Production inference never trains or changes weights. Improvement is an offline,
human-approved process; ACTIVE learned traffic control remains prohibited.

The live simulation and vision runtimes submit decision evidence to ImprovementWriter.
Its 128-entry queue and separate thread isolate disk I/O from control. ImprovementLog
stores SQLite records with event/session IDs, decision timestamp, camera IDs, observation
schema and observation, confidence, vision/policy versions, proposed and sent actions,
safety intervention, authenticated ACK where available, next observation outcomes and
fallback reasons. Sent action is explicitly not physical execution evidence.
SHADOW proposals are correlated by session and observation sequence, including proposals
which complete before command delivery. DISABLED still logs baseline decisions.

Retention defaults: seven days, 10,000 decisions, maximum initial record 256 KiB; no
images are copied. SQLite auto-vacuum reclaims deleted pages. Reads and writes prune
expired data. The writer exposes dropped/error counters; storage failure does not stop
control. Abrupt process exit can lose queued records. This is a local bounded audit,
not a tamper-evident regulatory archive. Monitor these counters and export selected
review evidence before expiry using an authorized local process.

STA_IMPROVEMENT_LOG_PATH selects local storage. STA_VISION_MODEL_VERSION and
STA_CONTROLLER_POLICY_VERSION identify loaded versions; the default vision value is
UNVERIFIED_LOCAL_MODEL, not a fabricated immutable model ID. Production deployment
must bind these to the verified loaded release. No network log export is installed.

Offline components live in traffic_vision.training.improvement_release:
FeedbackWorkflow enforces ordered stages; ImprovementReleaseRegistry binds artifacts,
training configuration, dataset reference, code commit, benchmark, evaluation, approval,
staged qualification and rollback. Existing dataset release, model packaging, measured
benchmark/regression and vision registry tools remain the underlying training tools.
The new release registry is a deployment evidence/pointer store, not a live model loader.

Tests: tests/test_improvement_loop.py; existing dataset/model/control training suites.

Verification on 2026-10-04: full Python suite 326 passed (39.35 seconds, two existing
deprecation warnings). All ten improvement tests passed, including retained artifact
recovery after source deletion. Ruff passed; mypy passed for backend, release
workflow and improvement tests. These are software results, not physical readiness.

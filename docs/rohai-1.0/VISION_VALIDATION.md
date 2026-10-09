# Smart Vision validation — 2026-10-08

Final full Python suite: **460 passed, 8 HIL skipped**, including camera startup cancellation,
cleanup and unconfirmed telemetry gating. The focused camera/calibration subset also passed
**50 tests**.
Scoped strict mypy passed 19 files. Repository Ruff lint and changed Python formatting pass.
MAKET Node overlay suite: **11 passed**; both UI scripts pass Node syntax checks.
Existing unrelated whole-repository mypy issues are documented in STAGE1_PROGRESS.

New `tests/test_smart_calibration.py` covers atomic calibration save/load, reference/hash
and pose rejection, source/transform/resolution binding, normalized scaling, convex perspective
anchors, image-derived proposals versus blank frames, moving vehicles versus queue, frame
provenance, unavailable stale/unconfirmed metrics, bounded recovery, real process/shared-memory
cleanup using a controlled worker fixture, damaged images and slow post-inference rejection.
Fixtures are explicitly not physical-camera validation.

Existing regression tests verify YOLO/ByteTrack adapter resets, ID recovery without additional
arrival, ambiguous identity rejection, brief occlusion, queue confirmation, count-line events,
wait accumulation, confidence degradation, stale observations and camera/safety failures.
No detector weights or ByteTrack model were replaced; motion/queue analytics are retained.
Moving objects are not queue merely because they are visible. Queue requires stop evidence
and calibrated approach/waiting-zone conditions. Predictions/occlusion are distinguished from
visible detections; the ordinary overlay does not draw missing tracks as real visible boxes.

Actual YOLO26 + ByteTrack was also run on the existing `recordings/car-detection.mp4` with
the pinned existing weights; performance evidence is in PERFORMANCE_RESULTS. This road-video
run establishes software execution, not toy recognition accuracy or physical safety.

The repository has no synchronized labelled physical-maket recordings with queue/count
ground truth. `datasets/manifests/analytics-baseline.json` likewise marks control-feature
accuracy NOT_MEASURED. Queue MAE and counting error remain null. COCO weights are not
claimed to recognize small toy vehicles reliably; no toy-model training was performed.

Next acceptance: connect a real webcam, verify driver FPS/resolution and recovery, annotate
toy objects plus frame-aligned queues/count crossings, measure recall/ID continuity/count error
and queue MAE through the existing offline benchmark contract. Train a domain model only
after obtaining a versioned labelled dataset and compare it with the pinned COCO baseline.
Then run camera-to-ESP32 HIL with existing commissioning and independent protection gates.

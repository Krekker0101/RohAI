# Aerial recognition verification — 2026-10-12

New server-owned drone profile uses official YOLO26s COCO weights with pinned SHA-256,
overlapping 1024-pixel crops, full-frame box deduplication, one global ByteTrack,
size/edge filtering, and an optional rotated pass. It is not fine-tuning.

- Original-frame spot check: frame 586, exact original uploaded file.
  Previous YOLO26n whole-frame output: one low-confidence truck box on the roof
  identified by the user. Enhanced/rotated output: nine vehicle candidates and no
  box on that roof. See `comparison.json` and `last-frame-enhanced.jpg`.
  This is a spot observation; missed vehicles remain and no precision/recall is claimed.
- Native/browser drone acceptance: all six selected 4K clip frames processed,
  19 observations, zero dropped frames, preview and report checked. Three browser
  scenarios passed, including software-fixture cancellation and damaged upload.
  Evidence: `../aerial-acceptance/acceptance.json`, `presentation.png`, `mobile.png`.
- Ordinary profile regression: 24/24 frames, real YOLO26n pedestrian tracking,
  preview/report and three browser scenarios passed.
- Full Python regression: 533 passed, eight real-ESP32 tests skipped (device absent).
- Ruff and mypy passed (207 source files); production build, ESLint and 12 Vitest tests passed.
- Twelve unreviewed original frames prepared under `.runtime/aerial-training-review-v1`.
  No human ground truth or newly trained checkpoint has been created.

CPU latency is substantial and variable: the six-frame browser clip took about 87
seconds of worker time in this run. Whole-recording speedup has not been claimed.
The new profile must be selected on a new upload; historical job outputs are unchanged.

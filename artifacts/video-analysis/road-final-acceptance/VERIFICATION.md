# Road overlay and analysis animation — 2026-10-12

- Full Python regression: 538 passed, eight real ESP32 tests skipped (no device).
- Ruff and strict mypy passed (210 source files).
- TypeScript/Vite production build, ESLint and 12 Vitest tests passed.
- Native/browser acceptance on two selected frames of the user's 4K recording:
  actual YOLO26s and NVIDIA SegFormer inference, both frames completed, no dropped
  frames, road overlay displayed and report downloaded.
- Four browser scenarios passed: real inference, cancellation, invalid upload,
  and backend loss. Activity indicators disappear on terminal/error states;
  reduced-motion preferences disable CSS animations.
- Evidence: `acceptance.json`, `browser-report.json`, `analysis-in-progress.png`,
  `presentation.png` and `mobile.png`.

Road masks use official pretrained Cityscapes weights. Aerial refinement is explicitly
labelled as a semantic seed plus a connected-surface heuristic. Coverage percentage
is not accuracy. No new training or independent road-mask validation was performed.

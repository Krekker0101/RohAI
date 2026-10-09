# Stage 1 progress — 2026-10-08

## Smart Vision & Calibration continuation

Added bounded local-camera reopen with stream epochs/FPS request/resource cleanup, corrupt
and late-inference rejection, default raw JPEG preview, JPEG/publication performance samples,
camera-bound atomic calibration persistence, four-anchor perspective assistance and typed
frame-derived MAKET vision state. Unconfirmed calibrated traffic metrics are withheld.
Existing safety/hardware architecture is retained; recovered camera streams require revalidation.

Changed vision files: `packages/vision/src/traffic_vision/{camera,pipeline,settings}.py`.
Changed MAKET files: `apps/backend/src/smart_traffic_backend/maket/{app,assisted,config,runtime}.py`;
new `calibration.py` and `vision_state.py`. UI changes: `apps/maket-ui/{app,simple,overlay}.js`
and `overlay.test.mjs`. New `tests/test_smart_calibration.py`, updated `test_vision_pipeline.py`,
new `scripts/benchmark_vision_stage1.py` and measured JSON under `artifacts/vision-stage1`.

Validation: final full suite 460 Python tests passed / 8 HIL skipped;
19-file scoped mypy, Ruff, Node syntax and 11 overlay tests passed. Measured existing
YOLO26/ByteTrack road-video processing at ~12.5 FPS; no camera/toy accuracy/HIL claim.
See CAMERA_CALIBRATION, VISION_VALIDATION and PERFORMANCE_RESULTS for workflow/evidence.
Next: real webcam/stand acceptance and labelled toy-domain evaluation before any training.

First Reliable Physical Intersection increment implemented on the existing codebase.
Vision, simulator, digital twin, policy models and frontend behavior are retained.

## Changed files

- Backend `hardware_service.py`: shared execution boundary and physical timing gate.
- Core `device_state.py`, `safety.py`: lifecycle/events and explicit conflict matrix.
- Backend `hardware.py`, `config.py`, `main.py`: mock/replay selection and hardware API.
- MAKET `config.py`, `runtime.py`, `serial.py`: commissioning gate, application-service
  integration, versioned firmware contract, bounded retry, cancellation/fault latch,
  fresh reconnect session and electrical ARM/stop confirmation.
- MAKET `app.py`: typed geometry response correction.
- ESP32 `src/main.cpp`, `include/stand_config.h`: version 1.0.0 and idempotent retries.
- `START_DEMO.bat`, `START_REAL_VISION.bat`: explicit mock output selection.
- `configs/vision.env.example`, `configs/maket/maket.json`: explicit nonphysical vision
  output and default-false physical commissioning attestation.
- `tests/test_stage1_hardware.py`, `test_maket_mode.py`, `test_maket_hil.py`: new contract/
  fault injection/API tests, updated wire fixtures and opt-in USB duplicate checks.
- `apps/esp32/README.md` and this directory: actual architecture/safety/HIL limitations.

## Prepared interfaces

HardwareController remains the transport contract. HardwareApplicationService governs
execution; DeviceLifecycle records diagnostics; existing SafetySupervisor approves proposals;
existing SessionRecorder/replay stores evidence. SerialESP32Adapter and MockHardwareAdapter
are compatibility aliases; ReplayHardwareAdapter cannot open physical transport.

REAL backend remains observational/simulated with explicitly nonphysical output. Physical
control uses commissioned MAKET preflight. Host and firmware updates must be coordinated.
Next work: build firmware, verify physical circuit/interlock, connect ESP32, run HIL,
calibrate the camera/toy model and measure full-cycle/readback/latency acceptance.

## Validation

- Full Python suite: 439 passed, 8 physical HIL checks skipped. After the final serial
  watchdog/readback tightening, its affected subset also passed: 40 tests, 8 HIL skips.
  Final config/API changes passed 26 affected tests and scoped typecheck.
- Dashboard: TypeScript typecheck, ESLint, 12 Vitest tests and Vite production build passed.
  TypeScript/Vite needed approved execution outside the sandbox after EPERM writes.
- MAKET overlay: 10 Node tests passed.
- Repository Ruff lint and changed-file formatting passed; git diff whitespace check passed.
  Whole-repository formatting reported existing differences in unrelated files.
- Strict scoped mypy: 21 source files passed. Whole-repository mypy reported 23 errors
  in 7 unchanged files (audit scripts and older fault/safety/overlay/camera tests).
- PlatformIO was not installed in PATH, the virtualenv or the usual per-user installation;
  firmware compilation was not performed. No physical HIL success is claimed.


## Final integration (2026-10-08)

Supersedes earlier check summaries above: 478 Python tests passed, 8 real HIL tests skipped; full strict mypy passes 161 files; repository Ruff lint/format pass. Dashboard typecheck/lint/build, 12 Vitest and 11 Node overlay tests pass. Firmware 1.0.1 compiles (RAM 21,620 bytes, Flash 284,789 bytes), with no upload. Local Windows launcher and 13 headless Edge E2E tests pass; evidence is artifacts/rohai-1.0/workspace-acceptance.json. Physical acceptance remains NOT VERIFIED; see FINAL_ENGINEERING_AUDIT.md and ACCEPTANCE_TESTS.md.

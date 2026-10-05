# Physical Maket readiness report

Date: 2026-10-04. Scope: current working tree, local ESP32 DevKit/WROOM-32 LED
stand software. This extends the earlier audit; it does not turn simulation
results into physical acceptance evidence.

Software implements live camera input, setup without equipment, USB Serial
discovery, calibration, frozen policies, supervised output requests, device
protocol, ESP32 firmware, independent device lease checks, electrical readback,
dashboard, recording and replay. No ESP32 or USB camera was discovered here.
Default CAMERA_ONLY now supports built-in laptop cameras independently of ESP32.
Actual probes of indices 0–10 through DSHOW and MSMF received no frames in this
environment; that does not identify whether privacy, driver, occupancy or absence
caused the failure. No device was flashed, no physical lamp was observed, and no toy-object ground
truth was available. Physical demonstration and road deployment are NOT VALIDATED.

## Component status

PASS below means the named software check passed, never physical certification.

| Area | Software status / evidence | Physical status |
|---|---|---|
| CAMERA | PASS contracts: first-class built-in/USB sources; indices 0–10, sequential DSHOW/MSMF, actual-frame availability, timeout/IPC/drop/transform checks | NOT TESTED: no real frames received; RTSP not provided |
| VISION | PARTIAL: pinned current YOLO COCO_BASELINE; separate PHYSICAL_MAKET manifest/package/registry; hash/domain gates retained | NOT TESTED: toy precision/recall/mAP; no evaluated Maket release |
| TRACKING | PASS contracts: shared tracker/state estimator, observed UID/crossing fixtures, short-occlusion checks | NOT TESTED: real IDF1, ID switches, hand occlusion |
| QUEUE | PASS contracts: image-relative queue/wait estimates; entries require observed entry crossing; disappearance is not departure | NOT TESTED: human count/wait error, throughput accuracy |
| CONTROL | PASS contracts: Fixed/Adaptive/frozen learned bridge, shared observation masks and supervisor, bounded policy worker, manual clearances | NOT TESTED: actual closed-loop stand; no learned superiority claim |
| SAFETY | PASS software checks: preflight rejects absent inputs; complete geometry required; E-stop available during discovery; restart stays stopped/SHADOW; REAL_ROAD blocked | NOT TESTED: independent physical interlock, optical conflicts, power faults |
| ESP32 | PASS compilation: espressif32 6.9.0, Arduino ESP32 2.0.17, ArduinoJson 6.21.5; RAM 21,604 B, flash 284,477 B | NOT TESTED: no upload, GPIO wiring, driver polarity/current or serial ACK from a device |
| WATCHDOG | PARTIAL: compiled independent FreeRTOS guard plus Task WDT; TTL/max-green/readback/local E-stop checks; WDT registration/init gates | NOT TESTED: timeout, task hang, MCU reset and power-loss behavior |
| UI | PASS: browser fixture; unknown metrics and failed preflight shown; authenticated setup/config/calibration/control; hot reload and discovery choices | NOT TESTED: actual live-camera drawing, device interaction or presentation acceptance |
| LATENCY | PARTIAL: separate latest-frame stages/control clock, bounded preview task and recorder queue, p50/p95 instrumentation | NOT TESTED: camera-to-light latency, real FPS, soak, recording drops |
| RECOVERY | PASS contracts: unique serial candidate/identity gate; source identity changes invalidate old calibration; explicit reconnect/operator/local ARM | NOT TESTED: real unplug, reconnect, sleep, driver/native stalls |
| DEMO READINESS | PARTIAL: setup operates without equipment; software build/contracts verified | NOT TESTED: full physical acceptance scenario |

## Using RohAI with only a laptop camera

Current camera-only software validation: **408 passed, 7 HIL skipped**, two dependency
deprecation warnings, 45.14 s (`laptop-full.xml`). The 28 new laptop-camera contracts
exercise discovery/default suggestion/multiple selection/unavailable/busy/no-frame,
backend fallback, crop/rotate/mirror, latest-frame IPC, presets, zero hardware commands,
shadow recommendations, resolution/calibration invalidation and reconnect/revalidation.
Two browser contracts passed, including Select+Save and explicit Camera Only Start
without ESP32. TypeScript, ESLint, 12 Vitest tests, Ruff and scoped mypy (17 files) passed.

`laptop-no-hardware.json` records actual app startup/shutdown: console 200,
CAMERA_ONLY, no global failure from absent ESP32, no hardware command, physical ARM 409.
`laptop-discovery.json` records actual unsuccessful Windows camera probes. IPC and
browser fixtures are not evidence that this laptop's physical camera works.
`laptop-evidence-manifest.json` binds current camera-only sources/evidence/environment;
earlier physical-output build/evidence files remain separate historical measurements.

Start `.venv/Scripts/python.exe -m traffic_vision.cli maket start`; copy its temporary
local token into `/maket` and Connect console. Discover cameras → Preview/Test →
BUILTIN_CAMERA, CAMERA_ONLY, preset → Select+Save → live calibration → Start Camera Only
/ Shadow. Optional Record video. See MAKET_SETUP_GUIDE.md for full instructions.

VISION_READY means fresh functioning perception; CONTROL_READY additionally requires
complete, matching, revalidated geometry/analytics and controller. HARDWARE_READY is
separate and can remain false throughout this demo. CAMERA_ONLY never opens Serial or
sends HELLO/STATUS/ARM/SIGNAL/ESTOP; virtual supervisor output is labelled a preview,
executed_action and ACK remain null. Optional physical output uses the same camera path
after explicit mode/config change and renewed hardware/operator gates.

Presets request 640×480/5 Hz, 1280×720/10 Hz or 3840×2160/20 Hz; actual negotiated size,
processing rates, drops and latency must be measured on the target laptop. HIGH is a
maximum request, not proof of maximum supported sensor resolution. Driver-reported FPS
from discovery is not measured end-to-end inference FPS. Friendly names/device paths
are optional provider metadata; OpenCV index alone is not immutable hardware identity.
Reconfiguration changes source identity; resolution mismatch blocks calibration;
ORB/RANSAC pose evidence plus explicit revalidation is still not a measured pose bound.

### Camera-only acceptance scenario — NOT TESTED on a real camera

| Step | Expected observable result |
|---|---|
| 1–3 Start, discover, select laptop camera | real frame availability, selected index/backend, no assumed index 0 |
| 4–6 Preview, position maket, calibrate | continuous fresh live frames; transformed geometry and source/resolution saved |
| 7–9 Place toy car, attempt detection | actual model bbox if detected; missed car recorded, never fabricated |
| 10–11 Move toy car | measured track trajectory/UID; verify manually, record switches |
| 12–14 Stop near calibrated line | queue update and increasing wait after stop confirmation; compare human timing |
| 15–17 Recommendation, ESP32 absent, no commands | SHADOW/adaptive request; HARDWARE_READY false; executed action/ACK null |
| 18 Record session | observations/tracks/decisions/video saved; capture timestamps/drop counters checked |
| 19 Disable/unplug camera | stale/no-frame health stops shadow demo, no stale feed portrayed as live |
| 20 Restore camera | restart, fresh pose revalidation/recalibration and explicit Start; no automatic resume |

No real-camera ground truth or completed acceptance record exists. COCO baseline toy
accuracy remains NOT VALIDATED. This scenario verifies a laptop-only demo separately
from real LEDs, HIL, watchdog qualification and road deployment.

Additional reproduction:

```powershell
.venv/Scripts/python.exe -m traffic_vision.cli maket cameras
.venv/Scripts/python.exe -m traffic_vision.cli maket camera-test --index 0 --backend AUTO
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp .runtime/pytest-laptop-reproduce --junitxml artifacts/maket-validation/laptop-reproduce.xml
.venv/Scripts/mypy.exe --config-file configs/maket/mypy.ini apps/backend/src/smart_traffic_backend/maket packages/vision/src/traffic_vision/camera.py packages/vision/src/traffic_vision/settings.py packages/vision/src/traffic_vision/pipeline.py packages/vision/src/traffic_vision/sources.py tests/test_laptop_camera.py tests/test_maket_mode.py
```

## Architecture and operation

`maket/runtime.py` owns an independent monotonic control loop. Capture, inference,
analytics and preview use separate threads/latest slots; UI is optional. Native
camera discovery runs in killable processes with a 3-second deadline per probe,
at most two probes concurrently. Runtime probes the selected USB camera before
loading its detector. AUTO Serial never picks arbitrarily among multiple devices.
HELLO then verifies actual firmware identity, domain and watchdog availability.

The default COCO profile retains the original upstream manifest/hash and makes
no toy-validation claim. Separate Maket artifacts keep strict domain, checksum
and class mapping checks, including the existing training compatibility gate.
Learned ACTIVE requires an evaluated Maket vision profile, frozen policy and
hash-bound measured OFFLINE/SHADOW/STAND approval reports, plus complete preflight.
There is no online optimizer or spontaneous weight update.

Requested action, supervisor outcome, sent command, hardware ACK and electrical
sense are distinct. Electrical voltage readback is not optical illumination.
STATUS does not renew an output lease. Device TTL/max-green/E-stop/readback guards
operate without the backend event loop; this implementation still needs physical
measurement. ALL_RED is an explicit LED-stand requirement, not a road fail-mode.

Configuration/calibration changes are authenticated and allowed only while
stopped. Setup saves JSON and reloads stopped/SHADOW. Calibration uses a fresh raw
frame and creates a new analyzer lifetime. Changing a camera endpoint changes its
source ID. Reconnect never authorizes learned control automatically. Calibration
alignment uses expiring ORB/RANSAC evidence, not a demonstrated pose-error bound.

## Verification and evidence

- `artifacts/maket-validation/full-release.xml`: 380 passed, 7 HIL skipped,
  1 dependency deprecation warning; 114.38 seconds.
- `artifacts/maket-validation/contracts-final.xml`: 28 Maket software tests passed,
  7 real-device HIL cases SKIPPED. Fixtures are expressly not physical evidence.
- `artifacts/maket-validation/no_equipment_startup.json`: actual console/API 200,
  disconnected camera/serial, no sent hardware command, Start 409, shutdown completed.
- `artifacts/maket-validation/firmware-build.log` and `firmware_build.json`:
  successful compile, tool versions and source/binary hashes; COMPILE ONLY.
- Maket Playwright browser fixture: 1 passed; dashboard Vitest: 12 passed;
  TypeScript and ESLint passed. Browser fixtures contain no camera/hardware.
- Ruff passed. Scoped strict mypy passed 15 files; configuration skips recursive
  upstream ML implementation analysis, retaining project contract checks.
- `evidence_manifest.json`: working-tree source/evidence hashes and environment.

The first no-equipment shutdown check exposed premature detector loading; corrected
by bounded camera probing before pipeline start and verified again. Setup reload
testing exposed attempted mutation of frozen settings; replaced by immutable copies
and rerun. E-stop bypasses setup locks; its concurrent-discovery regression passed.
Both full suite and affected subsystem were rerun after changes.

## Remaining risks and acceptance work

1. No labelled real-camera/toy dataset: baseline class confusion, missed cars or
   pedestrians and tracking/queue errors remain unquantified. Collect/review/version
   labels and evaluate a separate Maket release; predictions are not ground truth.
2. No physical wiring/readback/watchdog testing. A stuck output cannot be repaired
   by software; wire an independent hardware interlock. MCU power loss does not
   guarantee illuminated red LEDs without separate red circuitry/power.
3. Pose registration can be fooled by similar/feature-poor scenes. Add fixed
   fiducials and independently test camera movement/resolution/lighting changes.
4. Threads cannot terminate arbitrary native detector/driver hangs. Shutdown failure
   is reported, not hidden; device TTL must be independently measured. A failed
   pipeline stop prevents silently opening another pipeline over the old driver.
5. USB CRC detects corruption, not malicious commands. Local USB/filesystem/operator
   access is trusted; this is not an authenticated network hardware protocol.
6. Latency/throughput/long soak and pedestrian performance need target equipment.
   AVI is sampled at control cadence with nominal preview FPS; use event capture
   timestamps for timing, not AVI playback duration. Bounded retention may drop
   records; drops/errors are exposed, so completeness must be checked.
7. Legacy simulation interfaces were retained; shared state/safety/learned schemas
   do not imply every legacy controller was rewritten to a single new API.
8. No learned Maket release or evidence sufficient for ACTIVE exists here. Local
   approval files are trusted operator evidence, not external certification.

Follow `MAKET_SETUP_GUIDE.md`, `HARDWARE_WIRING.md`, `HARDWARE_IN_LOOP_TESTING.md`
and `MAKET_DEMO_SCENARIO.md` when equipment is connected. Standard selected board,
port/camera choice and calibration require configuration/wizard operations, not
source edits. Nonstandard board variants/wiring require reviewed pin configuration.

## Exact reproduction commands

From repository root in PowerShell, using new pytest output directories:

```powershell
uv sync --extra vision --extra maket --extra control-training
.venv/Scripts/python.exe -m traffic_vision.cli maket ports
.venv/Scripts/python.exe -m traffic_vision.cli maket cameras
.venv/Scripts/python.exe -m traffic_vision.cli maket validate
$env:ROHAI_MAKET_OPERATOR_TOKEN='replace-with-a-long-local-secret'
.venv/Scripts/python.exe -m traffic_vision.cli maket start
# http://127.0.0.1:8001/maket; choose devices, calibrate, run preflight
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp .runtime/pytest-maket-reproduce --junitxml artifacts/maket-validation/reproduce.xml
.venv/Scripts/ruff.exe check apps/backend/src packages tests scripts
.venv/Scripts/mypy.exe --config-file configs/maket/mypy.ini apps/backend/src/smart_traffic_backend/maket packages/traffic_core/src/traffic_core/runtime_modes.py tests/test_maket_mode.py tests/test_maket_hil.py scripts/maket_extract_frames.py
# Install build tooling in a separate environment to avoid its pinned deps altering the app.
uv venv .runtime/pio-venv
uv pip install --python .runtime/pio-venv/Scripts/python.exe platformio==6.1.18
$env:PLATFORMIO_CORE_DIR="$PWD/.runtime/platformio"
.runtime/pio-venv/Scripts/python.exe -m platformio run --project-dir apps/esp32
Push-Location apps/dashboard
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run test
npx.cmd playwright test tests/maket-ui.spec.ts --output=../../artifacts/maket-validation/browser-reproduce
Pop-Location
```

Build tooling was installed in the existing environment during this run and changed
its click/starlette/uvicorn versions; `evidence_manifest.json` records the measured
environment. Use the separate build environment above and the project lock for
clean reproduction. No automatic dependency migration is claimed.

**Proven status: software implementation/build/contracts verified; physical Maket
operation NOT TESTED; real-road activation blocked; learned ACTIVE unapproved.**

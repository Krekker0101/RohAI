# Fault Injection Report

## Scope

Executed against the Python runtime, authenticated protocol models, and an emulator
device kernel. These tests demonstrate software contracts only. No traffic cabinet,
MCU firmware, physical lamp sensor, electrical interlock, or live network was tested.

## Results

| Injected fault | Check | Result / boundary |
| --- | --- | --- |
| Controller task/process crash | `tests/test_api.py`, `tests/test_hardware_protocol.py` | Runtime task failure latches software safe state. A controller subprocess is also killed with `os._exit` after ACK; its sibling device process independently expires the lease and enters the configured emulator safe program. |
| Vision/model crash | `tests/test_vision_pipeline.py` | Runtime reports failed/not-ready and requests safe state. |
| Camera disconnect/stale frame | `tests/test_vision_pipeline.py`, `tests/test_perception_reliability.py` | Stale/unreliable observation is rejected; automatic source restart is not implemented. |
| Network loss | `tests/test_hardware_protocol.py` | Initialization/send failure is recorded as hardware `FAULT`. |
| Hardware ACK loss | `tests/test_hardware_protocol.py` | Timeout is a fault; no physical confirmation is produced. |
| Stuck command/output | `tests/test_hardware_protocol.py` | Emulator feedback disagreement is rejected. Actual stuck-lamp detection needs physical sensors. |
| Duplicate command | `tests/test_hardware_protocol.py` | Idempotent ACK does not renew watchdog lease. |
| Old command replay | `tests/test_hardware_protocol.py` | Old sequence is rejected. |
| Out-of-order command | `tests/test_hardware_protocol.py` | Sequence gap is rejected. |
| Watchdog timeout | `tests/test_hardware_protocol.py` | Separate-process emulator latches its explicitly configured test safety program. Not device firmware. |
| Policy exception, NaN/schema mismatch | `tests/test_hardware_protocol.py` | Proposal is discarded; deterministic baseline stays in control. |
| Policy latency | `tests/test_hardware_protocol.py` | Slow proposal exceeds its bounded timeout without blocking control. |
| Invalid phase request | `tests/test_hardware_protocol.py` | Device contract rejects unsafe transition and returns a negative ACK. |
| Preview encoding/overlay failure | `tests/test_vision_pipeline.py` | Analysis remains available; preview is optional. |
| Fresh commands indefinitely renewing green | `tests/test_fault_injection.py` | Independent maximum-green timer latches safe program despite a valid renewed lease. |
| Replayed feedback after successful delivery | `tests/test_fault_injection.py` | Delivery becomes FAULT and current emulator/physical confirmation is cleared. |
| Wrong command correlation during safe entry | `tests/test_fault_injection.py` | Readback rejected; ACK cannot establish safe-program completion. |
| Constructed policy instance bypassing validation | `tests/test_safety_architecture.py` | NaN, infinity and negative duration rejected on revalidation. |

## Verification run

Verification date: 2026-10-04.

Full Python repository suite:
`.venv\\Scripts\\python.exe -m pytest -q -p no:cacheprovider --basetemp=.runtime/pytest-safety-final`.

Result: 316 passed, 2 deprecation warnings, 38.72 seconds. Ruff passed for backend,
packages and tests; mypy passed for the four changed backend source modules.
The live black-frame fault fixture waits until at least one healthy
analytics snapshot exists before introducing its fault, so it tests the intended
healthy-to-fault transition rather than a pacing-dependent startup/EOF path. Two
Starlette/httpx deprecation warnings remain. Platform-level hardware checks also remain
outstanding; software tests do not establish physical production readiness.

## Uncovered physical failures

Bench testing is required for power loss, watchdog clock/reset failure, output-driver
short/open, cross-channel electrical conflict, sensor disagreement, corrupted firmware,
key compromise, device reboot/boot-ID rollover, and transport behavior under real
packet loss/reordering. Production remains prohibited until these have witnessed,
site-specific results and approval.

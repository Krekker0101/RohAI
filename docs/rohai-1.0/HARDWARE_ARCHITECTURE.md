# RohAI 1.0 hardware architecture

## Existing paths and corrected boundary

The ordinary backend factory in `main.py` previously created MockHardwareController
unconditionally. REAL denotes observation source, not physical output. The factory now
selects `STA_HARDWARE_MODE=mock|replay`, wraps default adapters in HardwareApplicationService,
and exposes output mode through `/api/v1/system` and `/api/v1/hardware`. Both Windows
launchers explicitly select mock. Compatibility defaults are retained; no serial fallback
exists. Explicit injected test/custom adapters keep their original behavior and are reported
as unknown by the hardware endpoint.

DemoRuntime remains isolated from physical hardware. The DEMO/REAL dashboard switch
selects existing API streams; it never arms ESP32. Recorded MAKET replay serves video/events
without importing/creating hardware. ReplayHardwareAdapter is a software output sink for
the ordinary runtime; the existing replay server is the recorded-session player.

MaketRuntime creates Esp32TrafficLightController, also exported as SerialESP32Adapter.
CAMERA_ONLY forbids physical commands. PHYSICAL_OUTPUT requires explicit selection,
preflight and local rearm. Live and operator-test SIGNALs and stop requests now go through
the shared HardwareApplicationService. Vision, ByteTrack, policies and simulator remain intact.

## Shared contract

HardwareController retains `apply(SignalState)` and `close()` for mock, replay and serial.
MockHardwareAdapter aliases the existing MockHardwareController for compatibility.
The application service tracks desired/confirmed output, rejects queued execution and
independently checks physical transition times. Reset is called after explicit successful ARM.

DeviceLifecycle defines DISCONNECTED, CONNECTING, READY, ACTIVE, DEGRADED, FAULT and
EMERGENCY_STOP with explicit transitions and 256-event bounded history. Faults cannot go
directly to ACTIVE. DEGRADED is reserved for recoverable diagnostics; serial failures latch
FAULT. MAKET telemetry adds serial lifecycle plus `hardware_execution`. Backend hardware
telemetry labels software completion separately from physical electrical readback.

## Serial protocol and firmware

Auto-discovery requires exactly one supported USB VID candidate. HELLO checks device ID,
protocol 1, firmware 1.0.1, PHYSICAL_MAKET domain and watchdog presence. Old firmware
without the version field is incompatible: upgrade host and firmware together.

Commands bind fresh session, boot ID, sequence, command UUID and device-time TTL.
CRC32 detects corruption and is not authentication; exclusive trusted USB is required.
ACK/NACK bind the outstanding request. Masks/phases are validated; SIGNAL, ARM and ESTOP
require appropriate electrical readback/watchdog state. Late responses never confirm output.

STATUS/SIGNAL allow one identical retry within the original transaction deadline. Firmware
remembers the last accepted canonical packet. Exact unexpired duplicates return current
readback without executing or extending lease; changed/reused identities, older sequences,
old sessions/boots and expired commands are rejected. HELLO/ARM/ESTOP are not retried.
Queues are forbidden; cancellation and communication error invalidate confirmation and
latch a fault. Explicit reconnect creates a new session and leaves the stand latched.

SIGNAL cadence renews lease; idle STATUS never renews it. The existing independent ESP
guard task checks lease, electrical readback, E-stop and maximum green; task watchdog
covers stalled main/guard tasks. GPIO assignments are unchanged.

## Observable control chain

Camera -> existing YOLO/ByteTrack/analytics -> TrafficState -> adaptive proposal -> existing
SafetySupervisor/SafetyController -> HardwareApplicationService -> serial -> ESP32 guard
-> stand outputs/electrical sense. Existing MAKET checks cover frame freshness/freezing,
inference, calibration, policy timeout and firmware timing. Readiness loss requests stop.
Without a stop ACK, physical output is unknown and the original device lease must expire;
the host does not assert successful safe execution. Electrical readback is not optical proof.

# Hardware Protocol

## Scope

`traffic_core.hardware_protocol` defines the versioned command/ACK/feedback contract.
`ProtocolHardwareController` implements host validation and delivery-state tracking.
`ProcessDeviceTransport` and `DeviceKernel` are executable test fixtures only. No
physical vendor transport or cabinet firmware is included.

## Provisioned requirements

`DeviceRequirements` requires device ID, safe mode ID, a non-empty requirements
reference, maximum lease, ACK and feedback deadlines, and maximum clock skew. There is
intentionally no default safe mode. ACK/readback deadlines must be shorter than the
lease. The site authority selects the program and its identifier; RohAI does not infer
that all-red, flashing, or any other phase is universally safe.

## Command envelope

Each `rohai-hardware-v1` command carries:

- Unique `command_id` and per-device monotonic `sequence`.
- `device_id`, current `device_boot_id`, and `controller_instance`.
- `kind` (`SIGNAL` or `ENTER_SAFE`) and requested signal state or approved safe-mode ID.
- UTC `issued_at` and `expires_at`; expiry is also enforced against a device monotonic
  lease established at receipt.
- SHA-256 canonical payload checksum and HMAC-SHA256 signature.

The device validates authentication, session identity, timestamp/TTL, command order,
transition constraints, safe-mode identity, and latch state before accepting. A
duplicate ID with the same payload is idempotently acknowledged but does not refresh
the lease. Reused ID with different content, old/replayed sequence, out-of-order
sequence, and invalid phase transitions are rejected.

## ACK and feedback

An ACK identifies device/boot/controller, command ID, sequence, command checksum,
accept/reject result, reason, timestamp, and signature. An ACK means only that the
device accepted or rejected the identified message; it is not lamp evidence.

Feedback has a separate sample sequence, timestamp, command correlation, measured
lamp groups, watchdog state, safe-mode ID, origin, and signature. The host rejects
stale/future/replayed samples and mismatched command identity. Only independent sensors
on a physical device can set `physically_confirmed`. Emulator-origin feedback is
recorded separately.

## Watchdog contract

Device firmware must enforce the accepted lease without relying on host timers,
network heartbeats, or the Python event loop. Lease expiry enters the explicitly
configured site safety program; a green indication may require a site-defined
clearance transition. The fault is latched until explicit rearm. The emulator's
separate OS process demonstrates lease expiry and latch behavior but is not a substitute
for a hardware watchdog, MCU firmware, independent power path, or electrical output
interlock.

## Conformance tests

`tests/test_hardware_protocol.py` covers expiry, invalid phase, duplicate, replay,
out-of-order sequence, watchdog expiry, ACK loss, network loss, stuck lamps, and the
separation between emulator feedback and physical confirmation. Real-device acceptance
still requires vendor/device conformance tests and bench evidence.

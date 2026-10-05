# Safety Architecture

## Status and operating boundary

RohAI is not approved to operate physical traffic signals. `MockHardwareController`
remains the default test/demo adapter. `ProtocolHardwareController` is an authenticated
host protocol boundary, but RohAI does not ship a cabinet transport, device firmware,
independent lamp sensors, or commissioning evidence. Its emulator reports
`physical_device=false`; a successful HTTP response, command send, device ACK, or
emulator readback is not proof of physical lamp state. The API therefore keeps
`physical_production_ready=false` unconditionally.

The learned controller is not a safety authority. Only the deterministic baseline
controller and the independent safety supervisor choose the command that reaches the
hardware adapter. `ACTIVE` policy mode is rejected. No policy mode bypasses the
supervisor, command protocol, or device interlocks.

## Command and state path

The state path is recorded as five separate facts:

1. `requested`: what the current baseline/policy interface requested.
2. `safety_approved`: the state returned by the deterministic safety supervisor.
3. `command_sent`: the state in the identified, expiring device command.
4. `hardware_acknowledged`: the state identified by a correlated, authenticated ACK.
5. `physically_confirmed`: independent sensor readback correlated to that command.

Mock adapters have no ACK or physical readback, so later stages remain unknown. The
protocol emulator has authenticated ACK and emulator feedback only. It cannot populate
`physically_confirmed`.

The hardware protocol binds a command ID, monotonic sequence, device and boot IDs,
controller instance, requested state, issue/expiry timestamps, payload checksum, and
HMAC signature. ACKs are correlated to command ID, sequence, checksum, identities, and
time. The host rejects stale/replayed feedback and reports ACK and readback separately.

## Independent device watchdog

The protocol requires the device to enforce the command lease with a local monotonic
clock and to run its watchdog independently from the host process and its Python event
loop. Expired lease, host/network loss, controller crash, or invalid command must enter
the site-approved device safety program and latch until explicit local/operator rearm.
Malformed messages, duplicate commands, and transport heartbeats must not extend a
lease.

The device also bounds continuous green independently of command renewal. At the
configured maximum green duration it latches the selected safety program, even when
fresh authenticated commands continue arriving. A failed feedback refresh clears
current confirmation and marks delivery FAULT; historical ACK is not current readback.

The actual safe traffic program is identified by `safe_mode_id` and linked to a
`requirements_reference` supplied by the authority responsible for the intersection.
RohAI has no default production traffic mode. The emulator implements only the
explicit test program `emulator-all-red`; it is a contract fixture, not firmware or a
claim that all-red is correct for every installation. A real safe program must be
derived from the site's approved hazard analysis and commissioning requirements.

## Component health and priority

Vision, analytics, controller, and hardware health are separate. Last-success wall
timestamps are exposed for frame, inference, analytics update, control tick, and
hardware ACK. Freshness enforcement uses monotonic time. Preview overlay/JPEG is an
optional consumer of analyzed frames. Control reads `latest_analysis` directly; preview
failure does not stop analytics/control.

SHADOW and ADVISORY policy evaluation run as a bounded side task with at most one
outstanding evaluation. Slow policy work does not delay deterministic control. Invalid
observation, timeout, exception, non-finite/invalid output, or schema mismatch leaves
the baseline command in force and records a policy error.

## Recovery

Recovery states are `HEALTHY`, `DEGRADED`, `FAULT`, `SAFE`, `RECOVERING`, and
`READY_FOR_OPERATOR_CONFIRMATION`. `DEGRADED` is entered automatically while the
controller is still running but vision or analytics health is impaired, or any
direction's evidence is unusable; it is reported with a machine-readable reason and
clears when the impairment does. It never unlatches `FAULT`/`SAFE` and never enables a
policy. Recovery from `SAFE` checks source connection, calibration, freshness,
analytics, controller, and hardware. A reconnect alone cannot move the state machine to
healthy or enable a learned policy. All checks must pass, then an authorized operator
must confirm. Confirmation resets policy mode to `DISABLED`.

The current vision source/runtime is terminal after source failure; these endpoints
provide explicit qualification and confirmation, not automatic camera restart. A new
runtime/process or a separately implemented supervised reconnect is needed before
checks can pass after a terminal fault.

## Release gates

Physical deployment remains prohibited until all of the following exist and are
validated for the target site:

- Device-side watchdog firmware, power-loss behavior, and independent output interlocks.
- Approved safe program and signed requirements reference.
- Vendor transport implementation, key provisioning/rotation, and replay protection.
- Independent per-lamp physical sensing, ACK/readback conformance, and stuck-lamp tests.
- Commissioning, hazard analysis, operator procedures, and witnessed fault-injection.
- Durable tamper-evident audit storage and deployment security review.

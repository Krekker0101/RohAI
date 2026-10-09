# ESP32 HIL plan

No ESP32 was used for this implementation. Missing equipment is SKIP, not emulator PASS.
Firmware toolchain is also absent. When installed: `pio run -d apps/esp32`.
Flash matching firmware 1.0.0 only after checking stand_config.h against actual wiring.

PowerShell from repository root:

```powershell
$env:ROHAI_HIL_PORT = 'COM5' # actual ESP32 port
.\.venv\Scripts\python.exe -m pytest tests/test_maket_hil.py -v
```

Automated USB tests check identity/version, expired/old-boot/corrupt/out-of-order commands,
changed packet reusing sequence, exact duplicate STATUS, ESTOP and electrical ALL_RED.
These tests do not request green.

After verifying independent protection, hold the local rearm button and explicitly opt in:

```powershell
$env:ROHAI_HIL_PROTECTION_VERIFIED = '1'
$env:ROHAI_HIL_ARM = '1'
.\.venv\Scripts\python.exe -m pytest tests/test_maket_hil.py -v
```

The optional ARM branch stops refreshing lease and checks latched ALL_RED after expiration.
Actual ACK evidence is written to artifacts/maket-validation/hil.json; unexecuted watchdog
tests remain explicitly NOT_TESTED. For MAKET live operation separately save
`hardware.independent_protection_verified=true` after actual commissioning.

| Scenario | Expected result | Verification |
| --- | --- | --- |
| Missing ESP32 | SKIP; no mock substitution | Automated |
| Wrong firmware | Connect fails, ARM forbidden | Fixture; mismatched firmware physical check |
| Duplicate STATUS | Same output, no lease refresh | Automated USB |
| Reused/expired/CRC/old boot | NACK or timeout; latched outputs | Automated USB |
| Backend silence | Original lease expires to ALL_RED | Optional locally armed USB test |
| Backend process killed | Device guard survives, no automatic rearm | Manual process kill |
| Lost/duplicate/late ACK | No false confirmation, bounded retry | Software; USB fault tool for HIL |
| Cable removed during SIGNAL | Safe outputs by original lease deadline | Powered stand and scope |
| Frozen/disconnected camera | Stop, AI authority revoked | Live camera interruption |
| Conflict/short intervals | Reject unsafe transition | Software plus instrumented phase cycle |
| Physical E-stop/stuck output | Output inhibition, latched fault | Electrical test |
| Reboot/reconnect | New session, ALL_RED, local rearm required | Manual USB/power test |

Record board ID, firmware build, port, wiring revision, command/ACK timestamps, electrical
measurements and visible lamps. Full-cycle timing, power-loss, independent interlock and
camera-to-output latency require actual hardware and cannot be inferred from fixtures.

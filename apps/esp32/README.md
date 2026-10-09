# ESP32 teaching-stand firmware

Firmware exists in `src/main.cpp`; its GPIO and timing configuration is in
`include/stand_config.h`. Protocol 1 / firmware 1.0.1 requires the matching Python host.
Build with `pio run -d apps/esp32` when PlatformIO is installed.

USB serial provides HELLO identity, boot/session/command IDs, TTL, CRC32, ACK/NACK,
electrical sense, local rearm, E-stop and device lease/task watchdogs. Exact unexpired
STATUS/SIGNAL retries are idempotent and never extend the original lease.
GPIO assignments are unchanged. CRC is corruption detection, not authentication.

Physical operation uses MAKET PHYSICAL_OUTPUT, not the dashboard REAL switch.
ARM requires commissioning attestation `hardware.independent_protection_verified=true`,
all existing preflight checks and the physical local rearm input. Verify actual independent
hardware protection before setting the attestation. Software is not an independent interlock.

See [hardware architecture](../../docs/rohai-1.0/HARDWARE_ARCHITECTURE.md),
[safety specification](../../docs/rohai-1.0/SAFETY_SPECIFICATION.md) and
[opt-in HIL plan](../../docs/rohai-1.0/HIL_TEST_PLAN.md).
Firmware 1.0.1 compiled successfully on 2026-10-08 (RAM 21,620 bytes, Flash 284,789 bytes). No device upload or physical validation was performed. Evidence: artifacts/rohai-1.0/firmware-build.log. IDENTIFY is a read-only protocol request; opening USB may still trigger DTR reset.

# Teaching-stand safety specification

AI only proposes. Existing SafetySupervisor/SafetyController approve execution, the shared
physical application boundary checks time since acknowledged transitions, and firmware
enforces independent transitions. Delayed ticks cannot skip clearance outputs.

The explicit conflict matrix in `traffic_core/safety.py` makes NS, EW and pedestrian groups
mutually conflicting. Opposite vehicle approaches share a phase. SignalState/masks represent
one permitted group; `validate_active_groups` rejects combinations for external validation.

Stand defaults remain: minimum vehicle green 8 s, maximum green 45 s, yellow 3 s,
all-red 2 s, pedestrian green 10 s and pedestrian clearance 5 s. Preflight compares firmware
timings with configuration. Host transition clocks start at ACK completion; repeated same
phase does not reset age. Pedestrian host yellow maps to physical ALL_RED while retaining
pedestrian clearance. The shared boundary also rejects excessive continuous green.

E-stop/readiness loss/shutdown discards active authority and requests immediate latched
ALL_RED, the existing bench emergency program. This is not a road emergency clearance
policy. Device lease expiry on backend silence, E-stop, sense fault or maximum green
independently latches safe output. Missing stop ACK leaves confirmation unknown/FAULT.
Reconnect cannot restore active authority; fresh preflight plus local operator ARM is required.
Application generations prevent a late signal completion from restoring authority after stop.

`hardware.independent_protection_verified` defaults to false. ARM is blocked until an
operator verifies wiring, power/reset behavior, independent physical E-stop/output inhibition
and electrical sense circuitry, then explicitly saves this flag. It is an operator attestation,
not automatic certification. Do not set it just to bypass preflight. Local rearm remains required.

Software checks/task watchdogs do not replace independent hardware protection. Output-enable
and shift-register behavior must be verified on the actual circuit. GPIO intent does not prove
electrical output; electrical sense does not prove visible lamp light. Without verified physical
protection, hardware operation remains blocked. Camera-only/replay cannot prove physical safety.

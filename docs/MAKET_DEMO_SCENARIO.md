# Physical demo acceptance

Run only with labelled toy-model evaluation, fixed validated camera geometry, wired
ESP32/readback, passed local Hardware Test and reviewed firmware watchdog tests.
Record session UUID, model/policy hashes, config/calibration version, machine environment,
video, observations/tracks, decisions, physical ACK and human verified counts.
Enable Record video. Keep the local E-stop accessible. Use the journal and observer
checklist; never substitute replay for a missing live camera.

1. Empty maket: verify live source identity, observable empty queues vs unknown states.
2. Place one labelled car North; human records count=1; compare bbox/track/domain class.
3. Move by hand toward oriented stop line; preserve identity/lane and real capture time.
4. Stop in queue zone; verify queue=1 after stop confirmation and increasing wait in s.
5. Add four labelled East cars; record count/queue ground truth; compare live metrics.
6. In SHADOW review AI proposed phase/duration and actual baseline request separately.
   EW preference is not guaranteed by learned weights; a disagreement is evidence,
   not something to hide or convert into generated reasoning.
7. Use approved baseline or reviewed learned ACTIVE; observe supervisor-approved
   minimum green → yellow → all-red → next green, ESP32 masks and LEDs visually.
8. Record actual electrical feedback AND human observation of all 14 LEDs; ACK alone
   does not establish optical illumination. Verify never simultaneous NS/EW green.
9. Move cars through stop/exit lines. Verify observed crossing events, queue decrease,
   throughput and stable UID; mere disappearance must not increment departure.
10. Unplug camera while active. Record last valid frame/ACK, control stop request,
    physical all-red time and device TTL expiry if command delivery is unavailable.
11. Reconnect camera; Restart camera, renewed preflight, local rearm and explicit Start.
    No automatic learned ACTIVE. Repeat USB unplug, MCU reset and computer sleep.

For every step save expected human label, measured estimate, timestamps, PASS/FAIL
criterion and remaining uncertainty in the recorded session. A failed detector count,
ID switch, missed crossing, stale frame or undesired AI phase fails that step even if
software tests pass. Repeat variable lighting/hand occlusion, frozen/black frame,
camera movement/resolution mismatch, high latency and stuck electrical output.
Observed outcomes are not causal policy superiority or road results.

This physical acceptance scenario has NOT been performed: equipment and independently
labelled Maket model/dataset are absent. See PHYSICAL_MAKET_READINESS_REPORT.md.

# Hardware-in-the-loop testing

Contract fixtures in test_maket_mode.py are host software checks, never HIL evidence.
Physical tests are isolated in test_maket_hil.py. Missing ROHAI_HIL_PORT yields SKIP;
set only a connected, flashed stand ESP32, not virtual/mock serial. Tests may latch
all-red. All optical lamp checks and cabling/current validation need an observer.

```powershell
$env:ROHAI_HIL_PORT='COM7'
.venv/Scripts/python.exe -m pytest tests/test_maket_hil.py -q -p no:cacheprovider --basetemp=.runtime/pytest-maket-hil --junitxml=artifacts/maket-validation/hil-tests.xml
# Optional TTL test: operator holds LOCAL_REARM button; no green requested.
$env:ROHAI_HIL_ARM='1'
.venv/Scripts/python.exe -m pytest tests/test_maket_hil.py -q -p no:cacheprovider --basetemp=.runtime/pytest-maket-hil-arm
```

Real-device tests check identity/ACK/electrical all-red, duplicate/out-of-order commands,
old boot, expired command, CRC corruption/version mismatch. Optional ARM test stops
heartbeat and measures device all-red/latch after TTL, without backend watchdog help.
hil.json labels optional timeout NOT_TESTED unless actually armed and sampled.
The test does not flash hardware or silently simulate a response.

Manual qualification matrix (log separately; no automatic PASS here):

| Injection | Required observation |
|---|---|
| USB unplug/network partition | LEDs enter configured all-red within remaining lease; reconnect latched |
| kill backend / computer sleep | independent device lease expires; no implicit rearm |
| firmware serial loop hang | guard keeps checking; Task WDT reset, gated startup/all-red |
| MCU reset | changed boot ID, old command rejection, local rearm needed |
| duplicate/replay | rejected without lease renewal |
| output stuck/open sense | fault latched, no activation; observer records each real lamp |
| partial/garbled/oversized JSON | no outputs change or lease extension |
| camera unplug/black/frozen/moved | controller leaves AI, device remains under independent checks |
| camera reconnect/resolution change | fresh calibration/preflight; explicit operator/local ARM |
| E-stop at any stage | local hardware interlock wins; all-red behavior measured |
| all six phase outputs | individual vehicle/ped LEDs, conflict/mapping verification |

Measure optical and electrical transition latency with an independent instrument;
API ACK latency is not lamp latency. Check power loss and /OE pull-up separately.
Never debug the watchdog under JTAG and infer normal behavior from it: Espressif
documents watchdog effects of debugger attachment. Wiring and firmware design are
not proof until this matrix and the demo acceptance scenario are executed.

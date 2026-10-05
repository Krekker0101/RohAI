# Maket wiring

Stand only, isolated low-voltage LED circuits. Board: classic ESP32 WROOM-32/DevKit;
other ESP32 variants need reviewed pin config, not copy-paste flash. Central mapping:
apps/esp32/include/stand_config.h. No direct road lamp/cabinet actuation.

| Function | GPIO | Connection |
|---|---:|---|
| 74HC595 DATA | 23 | first SER; Q7' cascades second SER |
| output CLOCK | 18 | both SRCLK |
| output LATCH | 19 | both RCLK |
| output ENABLE | 25 | both /OE, external pull-up to 3.3 V |
| 74HC165 sense DATA | 34 | cascaded serial Q7, input-only pin |
| sense CLOCK | 22 | both CP |
| sense LOAD | 21 | both /PL |
| local E-stop | 27 | normally released HIGH; button pulls LOW |
| local ARM | 26 | momentary button pulls LOW |

3.3 V logic, common GND, decoupling capacitors per chip. /MR on 595 held HIGH;
clock-enable on 165 LOW; unused sense inputs held LOW (no floating). Configure
drivers/current-limiting resistors for each LED; do not exceed MCU or shift-register
current limits. Use suitable buffered/transistor LED drivers. Sense independently
after the driver via correctly voltage-limited/conditioned electrical circuits, not
by reading host output variables. Logic input must never receive a 5 V signal directly.
Validate driver polarity: firmware active HIGH output masks and active HIGH sense.

| Logical bit | Lamp |
|---:|---|
| 0,1,2 | North red/yellow/green |
| 3,4,5 | South red/yellow/green |
| 6,7,8 | East red/yellow/green |
| 9,10,11 | West red/yellow/green |
| 12,13 | Pedestrian red/green |
| 14,15 | Unused LOW |

Cascade byte order must reproduce exact sense_mask == output_mask. Read all-red
4681 (0x1249), NS green 4708, NS yellow 4690, EW green 6409, EW yellow 5257,
ped green 8777. Determine chain direction using all-red and manual controlled phases;
reverse chain wiring if bits differ. Do not disable readback checking to make it pass.

The E-stop GPIO contract depends on live MCU/guard. For an independent physical stop,
the actual button must ALSO gate green/yellow driver power in hardware, with separate
red fail-state circuitry if required by the stand. /OE pull-up prevents uncontrolled
outputs during reset but blanks lamps; it alone does not illuminate red. Electrical
sense does not detect a broken LED that retains driver voltage. Optical/current sensors
and power-failure behavior need additional hardware and qualification. No physical
wiring, current limits or actual LED state were validated in this workspace.

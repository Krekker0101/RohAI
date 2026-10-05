# ESP32 traffic lights

Firmware: apps/esp32, pinned PlatformIO espressif32 6.9.0 / Arduino ESP32 2.0.17 /
ArduinoJson 6.21.5. USB Serial 115200, newline-delimited bounded JSON; Wi-Fi adapter
не реализован. Protocol abstraction Esp32TrafficLightController изолирует transport
от policy; Arduino firmware может реализовать тот же protocol позже.

Command fields: protocol=1, boot, session (32 hex), sequence, command_id (32 hex),
deadline_ms (device uptime), ttl_ms 500..2000, kind, phase, crc32. CRC input — ASCII
values этих девяти fields в указанном порядке, separated `|`; zlib/IEEE CRC32 lowercase
8 hex. Это защита от corruption на доверенном физическом USB, не authentication
для сети. Command time сопоставляется device uptime из последнего ACK; wrap использует
uint32 modular differences. Старый boot/session/sequence и expired deadline отвергаются.
Replay не продлевает lease. Version mismatch и oversized/garbled input не могут ARM.

HELLO binds new session и ALWAYS latches MAKET_ALL_RED. ARM требует local button,
released E-stop, independent sense ALL_RED и working watchdog. SIGNAL утверждает
одну из ALL_RED, NS_GREEN, NS_YELLOW, EW_GREEN, EW_YELLOW, PED_GREEN. NS — N/S
синхронно; EW — E/W синхронно, phase configuration центральная. STATUS читает состояние,
но НЕ обновляет TTL. ESTOP немедленно latches all-red независимо от AI.

ACK envelope содержит payload JSON string и CRC32 exact payload bytes. Payload binds
protocol/boot/session/sequence/command_id и сообщает accepted/reason, actual_phase,
output_mask, sense_mask, watchdog, sense_fault, E-stop и firmware timing. Host проверяет
schema, identity, CRC и соответствие phase/output masks. ACK state — firmware output
register plus independent electrical sense; отсутствует optical LED current/light proof.
HTTP success, requested outputs и ACK не объединяются в единую «actual lamp» величину.

Device transition checker повторяет min green 8 s, max green 45 s, yellow 3 s,
all-red 2 s, pedestrian green 10 s и clearance 5 s. Host проверяет совпадение timing,
отсчитывает подтверждённый transition от ACK и ограничивает green до device maximum
с запасом на tick/policy/ACK budget. Delayed control не пропускает clearances.
Emergency/lease expiry может немедленно all-red: это требование LED stand.

High priority FreeRTOS guard task на core 0 проверяет TTL, electrical readback,
E-stop и green maximum каждые 5 ms отдельно от serial loop. Task WDT 3 s проверяет
guard и loop; firmware не объявляет watchdog_present при failed task registration.
Backend event loop/USB traffic не обязателен для guard. После MCU reset outputs
initially gated, затем all-red; board power loss НЕ гарантирует red LED без отдельного
питания/interlock. Обязательные физические reset/hang/power tests описаны в HIL guide.

Watchdog design использует [Espressif Task WDT](https://docs.espressif.com/projects/esp-idf/en/release-v5.5/esp32/api-reference/system/wdts.html)
с version-compatible Arduino IDF4/5 API. Serial read/write deadlines соответствуют
[pySerial API](https://pyserial.readthedocs.io/en/stable/pyserial_api.html).

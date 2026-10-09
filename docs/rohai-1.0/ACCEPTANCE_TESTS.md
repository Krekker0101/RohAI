# Приёмка RohAI 1.0

Дата: 2026-10-08. PASS относится только к явно указанному объёму. Fixture, записанное видео и реальные устройства не взаимозаменяемы.

| Requirement | Implementation | Test | Evidence | Status |
|---|---|---|---|---|
| A. DEMO без оборудования | Изолированный DemoRuntime/Mock | Реальный HTTP и Edge: policy, emergency, comparison, ROI, offline | `dashboard.spec.ts`, `rohai-workspace.spec.ts`, acceptance JSON | PASS |
| B. Камера без ESP32 | CAMERA_ONLY, вывод недоступных данных, SHADOW | Camera/IPC и browser fixtures; backend без устройств | `test_laptop_camera.py`, `test_smart_calibration.py`, MAKET E2E | NOT VERIFIED |
| C. ESP32 подключение | IDENTIFY/HELLO, firmware 1.0.1, boot/session binding | Serial fault injection и read-only identity fixture | `test_maket_mode.py`, `test_rohai_workspace.py` | NOT VERIFIED |
| D. Калибровка макета | Авто/4 угла, ручное подтверждение, camera signature | Геометрия, atomic save, GUI fixture | `test_smart_calibration.py`, `maket-simple.spec.ts` | NOT VERIFIED |
| E. Учебное управление | FSM, preflight, independent protection gate, explicit confirm, local ARM | Программные проверки отказов, clearance, single-flight | `test_stage1_hardware.py`, `test_maket_mode.py` | NOT VERIFIED |
| F. Отключение камеры | Ограниченное восстановление, epoch reset, повторная проверка | IPC/process и stale/corrupt/slow fixtures | `test_smart_calibration.py`, `test_vision_pipeline.py` | NOT VERIFIED |
| G. Отключение ESP32 | ACK timeout, latched fault, safe-stop/watchdog | Fault injection; HIL skipped | `test_maket_mode.py`, `test_maket_hil.py` | NOT VERIFIED |
| H. Emergency Stop | Приоритетный endpoint, защита от поздних команд | Реальный HTTP/UI без actuator; программная FSM | workspace E2E, `test_stage1_hardware.py` | PASS |
| I. Запись/replay | Sequenced monotonic log, bounded video queue, pagination, no output | Реальный recorder/API/Edge, явно synthetic replay frame | `test_rohai_workspace.py`, workspace E2E | PASS |
| Запуск/завершение | Два owned сервиса, lock, health, graceful shutdown | Launcher + повторный процесс + Ctrl+Break | `workspace-acceptance.json` | PASS |
| Сопряжение | DPAPI, 7-day view-only cookie, consent, 8-hour launch authority, revoke | Windows crypto roundtrip, restart/expiry/CSRF и реальный браузер | `test_rohai_workspace.py`, workspace E2E | PASS |
| Приватность/лимиты | Видео opt-in, retention labels, selected closed-session deletion | Recorder privacy/storage/order tests | `test_rohai_workspace.py` | PASS |
| Доступность | Labels, focus, skip link, keyboard coordinate editor, responsive UI | Headless Edge keyboard и overflow; ручной код-review | workspace E2E, UI исходники | PASS |
| Производительность | Existing YOLO26n + ByteTrack на записанном видео | CPU benchmark, без ground truth | `artifacts/vision-stage1/recorded-performance.json` | PASS |
| Firmware сборка | Закреплённый PlatformIO проект 1.0.1 | `platformio run --project-dir apps/esp32` | `artifacts/rohai-1.0/firmware-build.log` | PASS |

## Обязательная физическая приёмка

Заполните сведения о плате, GPIO/схеме, независимом interlock, аварийном выключателе, камере, экспозиции, версии firmware и модели. Не выставляйте `independent_protection_verified` до фактических испытаний.

Выполните C/D/E/F/G/H на настоящем макете: boot/reset/DTR и отключение USB; local ARM; TTL, watchdog и не продлевающий lease duplicate; перезапуск host/ESP32; потеря камеры; отсутствие ACK; конфликт/readback mismatch; physical E-stop; green/yellow/all-red/pedestrian интервалы под нагрузкой. Одновременно фиксируйте command IDs, timestamps, электрический readback и независимое наблюдение ламп. Проверяйте отсутствие пересекающихся зелёных, реальные интервалы и отсутствие автоматического rearm. Сохраните запись с пропусками/ошибками, если они возникли, а не скрывайте их.

HIL запускается только при явно заданном `ROHAI_HIL_PORT` и остальных предпосылках в [HIL_TEST_PLAN.md](HIL_TEST_PLAN.md). Без устройств тесты пропускаются. Hardware-stage не считается завершённым до принятия этих доказательств.

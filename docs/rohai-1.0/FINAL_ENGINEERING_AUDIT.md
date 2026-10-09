# Итоговый инженерный аудит RohAI 1.0

## Заключение

Программная интеграция реализована. Готовность физического учебного макета **NOT VERIFIED**, весь этап Reliable Physical Intersection не объявляется завершённым. Реальный ESP32, независимая защита, оптическое подтверждение ламп и точность на игрушечном транспорте не испытаны.

## Проверенные свойства

DEMO/Mock, REAL VISION/SHADOW и PHYSICAL MAKET изолированы. Проект использует существующие YOLO26n, ByteTrack, analyzer и adaptive controller. Новые модели и обучение не добавлены. Hardware application service сохраняет desired/confirmed, FSM, clearance и single-flight; serial привязывает ACK к command/session/boot. Firmware 1.0.1 добавляет IDENTIFY без изменения lease/output внутри обработчика, GPIO сохранены. Само открытие USB может вызвать DTR reset — это требует HIL.

Autosetup выполняется асинхронно с блокировкой конкурирующих setup операций; emergency stop не ждёт эту блокировку. Не открываются два читателя активной камеры/serial. При отсутствии устройств выдаются коды/действия и остаётся безопасный просмотр. Выбор нескольких устройств требует пользователя. Готовность сервиса отличается от готовности Vision, controller и hardware; auto ARM отсутствует.

Сохранённое сопряжение имеет только scope VIEW_ONLY. Windows DPAPI защищает digest/expiry текущего пользователя, cookies HttpOnly/SameSite Strict; проверяются loopback peer, точный Host/Origin и Sec-Fetch-Site. Изменения/контроль требуют отдельного разрешения на запуск. Физический старт требует `confirm_physical`, preflight и локального ARM. Доверенные процессы того же OS-пользователя остаются за пределами этой защиты.

Камера изолирована процессом и shared memory, старые epochs/устаревшие кадры исключаются; JPEG/видео не блокируют control loop. Калибровка привязана к настройкам камеры/разрешению/эталону; автоматическая разметка не считается подтверждённой. Восстановление не означает автоматическую готовность управления.

Recorder использует ограниченную очередь, единый sequence lock для нескольких producers и monotonic timestamps. Команды/ACK пишутся отдельно, а не только последним snapshot. Video frame index появляется после записи кадра; очередь видео ограничивается. Ошибки/пропуски делают manifest неполным. RTSP credentials/query удаляются из записанного config. Лимиты прекращают запись, старые записи не удаляются. Явное удаление ограничено выбранным закрытым UUID и известными файлами; path traversal/symlinks отвергаются. Replay не импортирует hardware runtime, выдаёт physical_execution=false и сохраняет порядок истории.

Launcher проверяет окружение/build, loopback health и workspace identity, владеет только своими child processes, предотвращает дубли и штатно завершает сервисы. Принудительное завершение после timeout не доказывает безопасное состояние физических ламп.

## Проверки и доказательства

Полный Python набор: **478 passed, 8 HIL skipped**. Полный mypy: **161 файлов, ошибок нет**. Ruff lint проходит; финальные актуальные результаты находятся в `artifacts/rohai-1.0/verification.txt`. Frontend typecheck/lint/build и **12 Vitest** прошли, **11 Node overlay tests** прошли. Итог browser/launcher — `workspace-acceptance.json`; промежуточные ошибки старых E2E ожиданий исправлены под фактический DEMO контракт. Firmware 1.0.1 **успешно собрана** (RAM 21,620 bytes, Flash 284,789 bytes), без upload; её результат фиксируется в `firmware-build.log` и таблице приёмки, а не приравнивается к HIL.

По записанному `car-detection.mp4` с существующим YOLO26n/ByteTrack на CPU: ~12.5 FPS; inference p50/p95 **35.04/39.84 ms**, analytics **5.78/6.09 ms**, JPEG **1.30/1.41 ms**, capture→analytics **118.68/127.90 ms**, capture→publish **119.95/129.31 ms**. Наблюдаемый RSS **467,857,408 bytes (~446 MiB)**; CPU **747.11%**, где 100% означает одно логическое ядро. За короткий замер 102 capture/102 inference/101 analytics/101 publish, drops=0. Это paced recorded input, а не измеренная физическая задержка камеры; native thread pools отдельно не профилировались. MAE/accuracy/physical camera→ACK отсутствуют.

## Изменённые области

`apps/backend/.../launcher.py`; MAKET `app/config/runtime/serial/session/replay`, новые `pairing/setup/history`; `apps/maket-ui` workspace/auth/calibration/replay; ESP32 `main.cpp` и `stand_config.h`; Windows `start-rohai.bat`; integration/E2E tests и `scripts/run_workspace_acceptance.py`; исправления типов существующих audit/test fixtures. Предыдущие этапы также изменили core FSM/safety/hardware service, camera/pipeline и calibration/vision state. Полный перечень сохранён в `artifacts/rohai-1.0/changed-files.txt`; commit/reset не выполнялись.

Оставшиеся ограничения перечислены в [REMAINING_LIMITATIONS.md](REMAINING_LIMITATIONS.md), приёмочная матрица — [ACCEPTANCE_TESTS.md](ACCEPTANCE_TESTS.md).

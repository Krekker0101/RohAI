# Повторный аудит F01–F08

Дата: 27 сентября 2026. Область: контракты восприятия, аналитика, модель классов, передача состояния в safety. RL, обучение, новые нейросетевые архитектуры и новые пользовательские функции не добавлялись.

Статус FIXED означает закрытие конкретного программного дефекта в проверяемых границах контракта. Он не означает подтверждённую точность на всех дорожных видео или готовность физического перекрёстка. Первоначальный отчёт `AI_READINESS_AUDIT_2026-09-27.md` сохранён как исторический baseline.

## Результаты по замечаниям

| Замечание | Статус | Реализация: файл / функция | Проверяемое доказательство |
|---|---|---|---|
| F01: неизвестное направление превращалось в пустое | **FIXED** — программный контракт | `packages/traffic_core/src/traffic_core/models.py`: ObservationEvidence.reliable, ApproachState.invalid_is_not_empty; `packages/vision/src/traffic_vision/models.py`: DirectionMetrics.invalid_is_not_empty; `analytics.py`: to_core_state; `pipeline.py`: observation; `traffic_core/safety.py`: accept_observation | `test_invalid_measurement_never_becomes_valid_zero` — все 6 статусов × значения 0/1/12; `test_empty_observed_vs_unobserved_direction`; `test_incomplete_observed_payload_stays_unknown`; `test_observed_safety_checks_measurement_completeness`; `test_pipeline_ages_evidence_without_new_frames`. Invalid/null не превращается в valid zero, включая переход vision → core |
| F02: один пропуск обнулял очередь и ожидание | **FIXED** — bounded occlusion | `packages/vision/src/traffic_vision/estimator.py`: TrackStateEstimator.missing/resolve; `analytics.py`: process/_observe/_snapshot | `test_missed_detections_preserve_bounded_estimate`, `test_occlusion_expires_without_unbounded_phantom`, `test_id_recovery_does_not_create_arrival`, `test_ambiguous_id_recovery_is_not_invented`: скрытый трек помечен невидимым, оценка ограничена, confidence убывает, ожидание восстанавливается, неоднозначный ID не присваивается |
| F03: при 1 FPS неподвижная машина давала 0 | **FIXED** — временная семантика | `packages/vision/src/traffic_vision/analytics.py`: _observe/_metrics; `health.py`: PerceptionHealthMonitor._image_checks; `settings.py`: AnalyticsSettings/PerceptionHealthSettings | `test_stationary_vehicle_time_independent_of_fps` — 30/15/10/5/2/1 FPS; `test_motion_speed_is_not_scaled_by_frame_count`; `test_irregular_dropped_frames_and_camera_jitter`; `test_insufficient_fps_health_reports_degradation`: неподвижная машина получает очередь 1, ожидание по timestamp; непригодный интервал даёт null/DEGRADED |
| F04: нулевое ожидание пешеходов и смешанные arrivals | **FIXED** — модель состояния | `packages/vision/src/traffic_vision/geometry.py`: ApproachGeometry/IntersectionGeometry.assign; `analytics.py`: _observe/_metrics/to_core_state; `models.py`: PedestrianState | `test_pedestrian_waiting_crossing_clearing_and_separate_arrivals`: waiting → crossing → clearing, измеренное ожидание 2 s, vehicle arrival rate 0, pedestrian rate 30/min в заданном 2-секундном окне, ожидание доходит до core. Реальные зоны ещё требуют полевой калибровки |
| F05: пиксельная скорость и перспективная длина очереди | **NOT FIXED** | `packages/vision/src/traffic_vision/analytics.py`: _observe/_metrics; `geometry.py`: meters_per_pixel/StopLine.distance_pixels | Сохранён `test_length_units_and_core_state_adapter`: он доказывает арифметику заданного масштаба, **не** точность физических метров. Homography, дорожная плоскость и перспективная калибровка не реализованы |
| F06: новые ID считались новыми физическими прибытиями | **PARTIALLY FIXED** | `packages/vision/src/traffic_vision/estimator.py`: resolve; `analytics.py`: _observe/_reset | `test_id_recovery_does_not_create_arrival` и прежний `test_no_duplicate_counting_for_frames_detections_or_reappearance` проходят для оговорённых случаев. После длинных/неоднозначных перекрытий и между камерами физическая идентичность не доказана. Счётчики остаются сессионными; нет подтверждённого пересечения входной/выходной линии |
| F07: detector confidence выдавался за confidence очереди | **FIXED** — разделение семантики | `packages/traffic_core/src/traffic_core/models.py`: Uncertainty; `analytics.py`: _snapshot/_metrics; `pipeline.py`: observation | `test_independent_uncertainty`, `test_low_confidence_cannot_claim_empty`: detection confidence не подменяет tracking/motion/queue/observation; скрытый объект имеет observation confidence 0 и отдельную затухающую tracking confidence. Эти числа — эвристики, не статистически откалиброванные вероятности |
| F08: жёсткая COCO-нумерация | **FIXED** | `packages/vision/src/traffic_vision/manifest.py`: ModelManifest.complete/validate_model; `detector.py`: YOLODetector.open/process; `configs/models/yolo26n.json` | `test_wrong_missing_unexpected_model_class`, `test_manifest_mapping_and_weight_integrity`, `test_missing_manifest_refuses_start`, `test_adapter_custom_class_zero_is_car`. ID 0 собственной модели интерпретируется как CAR по manifest. Реальные локальные веса также успешно прошли startup со сверкой всех 80 классов и SHA-256 |

Все указанные новые тесты находятся в `tests/test_perception_reliability.py`, кроме явно названных прежних тестов `tests/test_vision_analytics.py`. **Результат каждого перечисленного теста: PASS** в итоговом полном прогоне. У F05 проход старого арифметического теста намеренно не трактуется как исправление физической геометрии; у F06 проход ограниченных случаев не доказывает общее решение идентификации.

## Дополнительные проверки

- Независимые плохие входы: stale/frozen/black/underexposure/overexposure/blur, падение текстуры ROI, повреждение локальной ROI, недостаточный FPS, отсутствие/истечение калибровки, inference/analytics timeout.
- Разрыв/повторное соединение камеры: изменение stream epoch и повторное установление cadence; RTSP адаптер проверяется с подставным transport, физический RTSP-сервер не запускался.
- Источник evidence от другого кадра отвергается; отсутствие track ID не превращается в достоверную пустую дорогу.
- Safety: все пять недостоверных статусов проверены после green, включая активный emergency; fault остаётся all-red после возвращения хорошего наблюдения.
- Интеграция `test_live_runtime_black_frame_latches_all_red`: настоящий четырёхэтапный pipeline и VisionRuntime с детерминированными изображениями и fixture-detector доходят до green; чёрные кадры приводят к all-red, null-измерениям и `control_ready=false`.
- Проверены конфигурируемые временные границы, отрицательное ожидание и непротиворечивость настроек.

## Совместимость прежних проверок

Сохранены все 46 исходных тестовых функций. Аналитическим unit-тестам добавлена явная simulator-evidence: они проверяют аналитику известных наблюдений, а качество реальных изображений проверяется отдельно. Ожидания тестов на число кадров и сброс при пропуске изменены согласно новым требованиям; старое ошибочное поведение не сохранялось ради зелёного теста. Тест vision API теперь требует отказа control endpoint для старой непроверенной калибровки. Проверки simulation, safety, API, tracking adapter и завершения pipeline продолжают выполняться.

Реальная vision-телеметрия повышена до 3.0-vision. Существующий клиентский normalizer принимает достоверную v3, но явно отклоняет недостоверные направления/null через прежний путь ошибки; контроль доверия учитывает control_ready. Регрессии клиентской границы находятся в `apps/dashboard/src/domain/telemetry.test.ts`. Новых UI-компонентов не добавлено. Симуляция и отдельный presentation demo сохраняют прежние версии. Подмена неизвестных измерений нулями ради старого UI не выполняется.

## Quality gate

Команды выполнялись из корня проекта, клиентские — из `apps/dashboard`.

| Проверка | Фактический результат |
|---|---|
| `.venv/Scripts/ruff.exe format .` | 58 файлов соответствуют форматированию |
| `.venv/Scripts/ruff.exe format --check .` | PASS, 58 files already formatted |
| `.venv/Scripts/ruff.exe check .` | PASS, All checks passed |
| `.venv/Scripts/mypy.exe` | PASS, strict checking, 46 source files |
| `.venv/Scripts/python.exe -m pytest tests/test_perception_reliability.py -q` | **73 passed**, 11.58 s, целевой прогон новых регрессий |
| `.venv/Scripts/python.exe -m pytest -q` после последних Python-изменений | **119 passed**, 20.38 s: прежние 46 тестов с указанной миграцией ожиданий + 73 новых случая |
| `npm.cmd run typecheck` | PASS |
| `npm.cmd run lint` | PASS |
| `npm.cmd test` | **12 passed**: 5 прежних + 7 случаев границы нового контракта |
| `YOLODetector(VisionSettings()).open()` / `.close()` с реальными локальными весами | PASS: `MANIFEST_STARTUP_OK ultralytics-yolo26n-coco 80` |
| `git diff --check` | PASS; только стандартные уведомления Git о LF/CRLF |

Полный Python-прогон оставляет два прежних deprecation warning в Starlette/TestClient; ошибок тестов нет. Клиентские зависимости восстановлены строго из `package-lock.json` через `npm ci --ignore-scripts`; установка, TypeScript и Vitest потребовали запуска вне файловой песочницы после EPERM. Изменений lock-файла и новых зависимостей нет.

Физические камера/RTSP/светофор, GPU, дорожная точность и обучение в этот quality gate не входят. Тестовый real-time сценарий использует настоящие pipeline/runtime/safety и подставные source/detector/hardware, что явно отражено в его коде.

## Сохраняющиеся ограничения

1. Пригодность картинки оценивается эвристически. Не доказано распознавание всех окклюзий/сдвигов камеры; frozen может ложно сработать на статичной сцене. Нужен размеченный полевой benchmark.
2. Старые камеры намеренно не получили выдуманную действующую калибровку. Пока нет проверенных зон и даты проверки, система выдаёт неизвестные измерения и не разрешает управление.
3. Физические ACK/watchdog/ESP32, метрическая перспектива, подтверждённые физические arrivals/departures и несколько камер остаются отдельными задачами.
4. Краткое восстановление ID консервативно, но не является обученным ReID. Неоднозначность сохраняется явно.
5. Safety использует прежний демонстрационный all-red fallback. Автоматическое снятие fault и дорожные нормативные режимы не добавлялись.
6. Проверка manifest доказывает совместимость артефакта и декларированной схемы, а не правильность обучения или ручного семантического domain mapping.

Обучение не запускалось. Прохождение программного инварианта является необходимым этапом; оно не заменяет измерение качества на целевых размеченных данных.


## Subsequent Traffic State Estimation audit

The follow-up F05/F06/F07 assessment, physical metric definitions and measured benchmark results are in [STATE_ESTIMATION_VALIDATION.md](STATE_ESTIMATION_VALIDATION.md). This earlier document records the preceding contract stage.

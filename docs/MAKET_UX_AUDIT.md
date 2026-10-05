# Maket UX audit — 2026-10-05

## Проверенная реализация

| Область | Проверка | Результат |
|---|---|---|
| Simple default | `/maket` без token/COM/backend/JSON/raw preflight | PASS, browser fixture |
| Engineer | Все прежние controls сохранены в `/maket/engineer` | PASS, route + 3 прежних browser tests |
| Локальный вход | HttpOnly Strict cookie, без копирования token | PASS, backend contracts |
| Граница доступа | Другой peer/Host/Origin/cross-site и factory default | PASS, отказ авторизации |
| Перезапуск | Cookie предыдущего процесса не принимается | PASS, backend contract |
| CLI | Default start, `--open/--no-open`, отсутствие secret в output | PASS, mocked server invocation |
| Одна камера | Автовыбор и сохранение без auto control | PASS, browser fixture |
| Несколько камер | Thumbnail choice без индексов/backend | PASS, browser fixture |
| Нет камеры | Понятная ошибка, Retry, Start запрещён | PASS, browser fixture |
| First run | Preview → graphical calibration → explicit Shadow Start | PASS, browser fixture |
| Second run | Совместимый готовый профиль → один Start, без setup | PASS, browser fixture |
| Calibration | Blank image rejected; image candidate unverified | PASS, synthetic image contract |
| Graphical editor | Рисование polygon, submit, explicit confirmation | PASS, browser fixture |
| Pose change | Start запрещён, требуется проверка calibration | PASS, browser + existing backend contracts |
| Virtual lights | Recommendation EW может сосуществовать с ALL_RED supervisor | PASS, browser fixture |
| Отсутствие ESP32 | CAMERA_ONLY работает, physical command не отправляется | PASS, browser/backend contracts |
| Presentation | Fullscreen request, скрытые setup/engineer controls | PASS, browser fixture |
| Secret persistence | UI preferences не содержат bearer token | PASS, source/browser review |

## Артефакты

Итог запуска: **417 Python tests passed, 7 HIL skipped; 6 browser tests passed**.
Ruff и scoped strict mypy (4 затронутых Python files) прошли.
Payload графической калибровки отдельно принят `MaketCalibration`/geometry schema.

- [Simple 1366×768](../artifacts/maket-validation/simple-ux/simple-1366.png) — full-page
  capture при таком viewport; метрики могут требовать вертикальную прокрутку.
- [Presentation 1920×1080](../artifacts/maket-validation/simple-ux/presentation-1920.png).
- [Graphical calibration payload](../artifacts/maket-validation/simple-ux/graphical-calibration-fixture.json).
- [Python suite](../artifacts/maket-validation/simple-full.xml).

Кадр помечен SYNTHETIC BROWSER FIXTURE. Camera images, detections, policy decisions
и readiness в browser tests задаются фикстурами. Эти screenshots не доказывают
распознавание игрушечных машинок или работу подключённой камеры.

## Оставшиеся ограничения

### Исправление camera identity и пустого preview

После выбора камеры source ID может отличаться от ID в черновом geometry template.
Runtime теперь привязывает только непроверенный черновик к текущему source;
калибровка другого source не применяется и её файл не изменяется. Аналитика
работает для setup, но CONTROL_READY остаётся false до новой проверки калибровки.
Повторный camera restart использует те же правила.

Без живого кадра Simple canvas пуст: zones, tracks и detections не рисуются.
Preview старше 1.5 с скрывается. При потере backend очищаются preview и показанные
измерения. Regression: 66 affected Python tests и 6 browser tests прошли;
`artifacts/maket-validation/camera-identity-fix.xml` и browser run
`no-camera-overlays-verified`. Это дополнение к предыдущему полному запуску.

Реальные first/second-run на ноутбуке, browser launch на interactive desktop,
разрешения Windows и camera pose registration: NOT TESTED в текущей среде.
ESP32 и hardware-in-the-loop: NOT TESTED / SKIPPED. COCO toy accuracy: NOT VALIDATED.
Сессия рассчитана на доверенный локальный компьютер, а не hostile local processes.
Assisted calibration ограничена осевыми границами; шаблонные линии/зоны требуют
ручной проверки и исправления. Наличие кандидата не означает корректную геометрию.
При недостатке статических признаков повторный запуск может потребовать калибровку.
Tablet layout имеет responsive CSS; физический tablet/touch UX не проверялся.

## Повторить

```powershell
.venv/Scripts/python.exe -m pytest tests/test_maket_simple.py tests/test_laptop_camera.py tests/test_maket_mode.py -q -p no:cacheprovider --basetemp .runtime/pytest-simple-repeat
.venv/Scripts/python.exe -m pytest -q -p no:cacheprovider --basetemp .runtime/pytest-simple-full-repeat --junitxml artifacts/maket-validation/simple-full-repeat.xml
cd apps/dashboard
npx.cmd playwright test tests/maket-simple.spec.ts tests/maket-ui.spec.ts --output=../../artifacts/maket-validation/simple-browser-repeat
```

Доказанный статус: программные контракты и UX на фикстурах проверены;
физическая работоспособность и точность на реальном макете не подтверждены.

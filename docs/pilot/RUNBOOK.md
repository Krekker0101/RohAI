# Запуск и эксплуатация

Команды выполняются из корня проекта. Параметры `configs/pilot.env.example`
добавить в локальный `.env`; токен сгенерировать через
`.venv/Scripts/python.exe -c "import secrets; print(secrets.token_urlsafe(32))"`.
Использовать отдельный секрет просмотра при необходимости. Не сохранять секреты в git.

```powershell
.venv/Scripts/python.exe -m uvicorn smart_traffic_backend.main:app --host 127.0.0.1 --port 8000
```

Открыть `/dashboard/#operations`, выбрать REAL и ввести токен в настройках.
REAL означает подключение к основному backend; фактический источник указан в отчёте.
По умолчанию backend работает в SIMULATION. Для записанного видео настроить существующий
Vision config (`configs/vision.env.example`) и его калибровку. Для ноутбука использовать
существующий CAMERA_ONLY console `/maket`, не включать PHYSICAL/REAL_ROAD.
Отчёты нового operations API относятся к основному backend, не к отдельному Maket runtime.

API с `X-Operator-Token`:

* `GET /api/v1/operations/status` — состояние записи.
* `GET /api/v1/operations/report?days=7` — JSON, либо `&format=csv`.
* `GET /api/v1/operations/metrics` — Prometheus.
* `POST /api/v1/operations/backup` — согласованный SQLite backup, только оператор.

Не открывать локальный сервис в интернет без HTTPS, сетевого ограничения доступа
и отдельной схемы управления идентификацией. Это локальная пилотная конфигурация.

## Monitoring и восстановление

Шаблоны: `configs/monitoring/prometheus.yml`, `alerts.yml`. Секрет передаётся файлом
`/run/secrets/rohai_operator_token`; правила сначала проверять `promtool check`.
Порог 500ms — начальный эксплуатационный сигнал, не SLA и не safety gate.
Для уведомлений подключить свой Alertmanager; внешние сообщения автоматически не отправляются.

Backup находится рядом с базой в `backups/`; максимум 10 файлов, затем оператор архивирует
их вручную. Старые backups не удаляются автоматически. Полная резервная копия площадки
также должна включать конфигурацию, калибровку, записи и модели: этот endpoint копирует
только базу наблюдений.

```powershell
.venv/Scripts/python.exe scripts/restore_observations.py .runtime/pilot/backups/FILE.sqlite3 .runtime/pilot/restored.sqlite3
```

Остановить сервис, заменить `STA_OPERATIONS_DATABASE` на новую базу, запустить,
проверить site ID, status и отчёт. Активная база не перезаписывается.
При ошибке записи control loop продолжает работать; метрика storage_healthy становится 0.
После исправления причины runtime-ошибки запись повторяется; ошибка инициализации
требует перезапуска. Отчёты об отсутствующих интервалах не заполняются задним числом.

## Данные и эксперименты

```powershell
.venv/Scripts/python.exe scripts/prepare_video_review.py recordings/car-detection.mp4 --output artifacts/pilot/review-car --count 24
# index.html — локальная галерея, review.json — DRAFT_UNREVIEWED, не ground truth.
.venv/Scripts/python.exe -m traffic_vision.cli dataset validate DATASET.json
.venv/Scripts/python.exe -m traffic_vision.cli dataset stats DATASET.json
.venv/Scripts/python.exe -m traffic_vision.cli dataset release DATASET.json --destination datasets/releases/field-v1
.venv/Scripts/python.exe -m traffic_vision.cli benchmark vision --release datasets/releases/field-v1 --output artifacts/pilot/field-vision.json
```

Импорт делается через `configs/datasets/import-template.json`: права по умолчанию
не подтверждены. Заполнить действительные сведения; не менять их ради обхода проверки.
Одного ролика недостаточно для независимых train/validation/test по источникам.

SUMO установлен отдельно; основные зависимости не изменяются:

```powershell
uv pip install --python .venv/Scripts/python.exe --target .runtime/sumo-deps -r configs/sumo-requirements.txt
$env:PYTHONPATH = (Resolve-Path .runtime/sumo-deps).Path
$env:PATH = (Join-Path $env:PYTHONPATH 'bin') + ';' + $env:PATH
.venv/Scripts/python.exe scripts/evaluation/sumo_benchmark.py --output artifacts/pilot/sumo-new --seeds 42 43 44 --seconds 300 --demand 900
```

На другом компьютере установить SUMO/TraCI той же версии и добавить бинарники в PATH.
Plan и маршруты сохраняются до сравнения; модель трафика не откалибрована под дорогу.

## Проверки

```powershell
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m ruff format --check .
.venv/Scripts/python.exe -m mypy
.venv/Scripts/python.exe -m pytest --basetemp=.pytest-tmp --junitxml=artifacts/pilot/python-tests.xml
npm.cmd --prefix apps/dashboard run build
.venv/Scripts/python.exe scripts/run_workspace_acceptance.py --output artifacts/pilot/browser
.venv/Scripts/python.exe scripts/pilot_readiness.py --output artifacts/pilot/readiness-new.json
```

CI запускает lint/types/tests, сборку dashboard, browser acceptance и container smoke.
Настроить обязательный статус CI для protected branch в GitHub отдельно.
Dependabot предлагает обновления; каждое проходит проверки перед merge.

Короткая проверка встроенной камеры без записи изображения:

```powershell
.venv/Scripts/python.exe scripts/check_local_camera.py --backend MSMF --seconds 5 --output artifacts/pilot/capture-new.json
```

Для сводки с этим свидетельством добавить `--capture artifacts/pilot/capture-new.json`
к `pilot_readiness.py`. Захват и полная дорожная квалификация остаются разными критериями.

Настройка CPU на том же видео и той же модели:

```powershell
.venv/Scripts/python.exe scripts/tune_vision.py --output artifacts/pilot/tuning-new --seconds 8 --repeats 2
```

Измерения идут последовательно, порядок потоков чередуется. Если ни один вариант
не проходит временные пороги, рекомендация равна null и конфигурация не меняется.
Для применения подтверждённого результата задать `STA_VISION__TORCH_THREADS` и повторить
измерение в целевом режиме. Порог fps зависит от записанного источника 12.5 FPS;
тест не устанавливает максимальную пропускную способность для других камер.

Официальные интерфейсы: [TraCI](https://sumo.dlr.de/docs/TraCI/Interfacing_TraCI_from_Python.html),
[Prometheus config](https://prometheus.io/docs/prometheus/latest/configuration/configuration/),
[uv CI](https://docs.astral.sh/uv/guides/integration/github/).

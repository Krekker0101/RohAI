# Проверка Vision Engine

Проверено 13 сентября 2026 года: Windows, CPython 3.14.5, CPU, Ultralytics 8.4.150,
PyTorch 2.14.0+cpu, OpenCV 4.14.0, NumPy 2.5.3. Фактические версии закреплены в `uv.lock`.

| Проверка | Результат |
|---|---|
| `uv sync --locked --extra vision --offline` | Успешно |
| `uv pip check` | Зависимости совместимы |
| Импорты всех внутренних модулей | 25 модулей импортируются |
| Python 3.12 syntax | Проверен через `ast.parse(feature_version=(3,12))`; отдельный Python 3.12 runtime не запускался |
| Ruff lint / formatting | Без ошибок |
| Strict mypy | Без ошибок, включая CLI scripts |
| pytest | 41 passed; 2 предупреждения upstream Starlette |
| Synthetic demo | 150 кадров, 600 детекций, 4 уникальных ID; peak queue 4, max observed wait 4.87 s |
| YOLO26 + ByteTrack на записанном car video | Все 377 кадров обработаны; 66 detection observations, 6 track IDs |
| YOLO26 + ByteTrack на mixed video | Все 647 кадров обработаны; 368 detection observations, 10 track IDs; car, bicycle, pedestrian |
| Настоящий YOLO через vision backend | Capture/inference/analysis/publishing: 377/377/377/377, drops 0 |
| HTTP и WebSocket при inference | Проверены через FastAPI TestClient; 288 health-запросов, max latency 5.74 ms |
| EOF и graceful shutdown | Readiness 503, liveness 200, all-red, mock hardware закрыт |

Реальный backend работал с исходным темпом около 12.5 FPS. Последняя измеренная latency
конвейера — 128 ms. Эти значения относятся к данному запуску на данном CPU, без network/TCP
latency; это не гарантия производительности на другом оборудовании.

Проверки запускаются так:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check.ps1
uv run --extra vision python -m traffic_vision.demo --frames 0
uv run --extra vision python scripts/verify_vision_backend.py
```

Артефакты: `recordings/vision-demo.mp4`, `recordings/mixed-demo.mp4`, `recordings/synthetic-demo.mp4`, их `.jpg`,
`.jsonl`, `.report.json`, а также `recordings/vision-backend.report.json`.
Они локальные и исключены из Git. Полный список исходников — [files.md](files.md).
Оба записанных входа вместе содержат 1024 кадра. В каждый output добавлен один
завершающий all-red кадр; JSONL хранит только аналитику исходных обработанных кадров.

В процессе проверки исправлена гонка старта: readiness теперь требует уже опубликованной
контроллером телеметрии. Проверены дубли ID, пропуски наблюдений, TTL трека, ожидание после
остановки и движения, неоднозначные ROI, недостаточные/некорректные полигоны, перегрузка,
отказ модели, stale source, калибровка и сохранение прежнего simulation API.

Число track IDs не означает такое же число физических машин: ByteTrack может менять ID
после перекрытий. Среди raw predictions короткого car video встречаются несколько классов;
без разметки это не подтверждает правильность каждой классификации. Этот материал —
проверка выполнения detector/tracker/pipeline, а не precision/recall benchmark.
Очереди и waiting timers дополнительно проверены на детерминированной сцене с известными
движением и идентичностями. Наблюдаемые метрики не заменяют размеченную оценку точности.

Webcam/RTSP адаптеры реализованы, но физическая камера, RTSP-сервер, CUDA, ESP32 и Docker
в данном окружении не проверялись. Проверенные входы — локальные видеозаписи и synthetic.
Flutter и физический hardware adapter в этот этап не включены.

Сеть во время установки была нестабильна; wheel-файлы восстановлены частями с обязательной
проверкой SHA-256 из `uv.lock`. Веса получены из официального Ultralytics mirror и сверены
с опубликованным SHA-256. Рабочий pipeline выполняет inference локально.

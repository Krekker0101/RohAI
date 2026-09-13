# Архитектурные решения

## Границы

`traffic_core` знает только модели, тайминги, решения, safety и порты. Он не
импортирует FastAPI, simulator, OpenCV или аппаратные библиотеки. `traffic_simulator`
реализует TrafficStateSource и application use case для simulation.
Backend создаёт объекты через constructor injection и отвечает за lifecycle,
HTTP, WebSocket, мониторинг и вызов HardwareController. `traffic_vision` реализует
source → YOLO26/ByteTrack → geometry → state builder → overlay/publishing.
Четыре worker threads связаны очередями ёмкостью один кадр; подробности —
[VISION_ENGINE.md](VISION_ENGINE.md).

Общий расчёт counts, Traffic Score, ожидания и congestion находится в
`traffic_core/state.py`; simulator передаёт ему наблюдения очереди. Vision
adapter использует ту же функцию после назначения подхода и определения
остановившихся объектов, не дублируя правила Traffic Score в YOLO wrapper.

Установка общая через корневой pyproject, четыре отдельных Python namespace.
Это модульная монорепа, без сети между внутренними слоями и без тяжёлого DI.
VisionRuntime управляет наблюдаемым состоянием по отдельным монотонным часам;
Simulation Runtime сохраняет прежний детерминированный путь и KPI benchmark.

## Safety и допустимая геометрия

MVP моделирует прямолинейное движение: NS и EW конфликтуют между собой;
пешеходная фаза конфликтует с обеими. Поворотов, трамваев, нескольких полос
и одновременного пешеходного/автомобильного зелёного нет.

Startup: all-red не меньше 2 s. Между транспортными фазами: green → yellow
(3 s) → all-red (2 s) → green. Между pedestrian и транспортной: pedestrian
green не меньше 10 s → clearance 5 s с красными лампами → all-red 2 s.
Это параметры демонстрационной модели, не расчёт для реального перекрёстка.

Safety ограничивает длительность каждого green; delayed tick делает не больше
одного перехода. При fault состояние all-red защёлкивается до нового runtime.
Нельзя считать отправленную команду гарантией физического all-red: это потребует
ACK и независимого watchdog на ESP32.

Нормальный порядок фаз циклический NS → EW → pedestrian. Adaptive выбирает
длительность при входе в green: min(max_green, min_green + seconds_per_score ×
sum(score)). Пешеходная длительность фиксирована. Порядок обеспечивает обслуживание
каждой фазы даже при перекосе потока. Это прозрачный baseline адаптивного управления,
не обученная RL-модель и не претензия на оптимальность.

Emergency задаётся оператором для направления, TTL 1–120 секунд симуляции;
последний запрос заменяет предыдущий. Он меняет желаемую фазу, но не сокращает
minimum green, yellow или clearance. После TTL нормальный цикл возобновляется;
уже начатый green заканчивается в пределах max_green. Повторные запросы могут
продлевать приоритет, что требует операторской дисциплины на будущем стенде.

## Детерминированность и метрики

Прибытия — процесс Пуассона по каждому подходу и отдельному потоку пешеходов.
Один seed, глобально упорядоченные события и отдельность генерации от обслуживания
дают одинаковый поток для разных политик. Обслуживание — FIFO point queues с
headway; нет координат машин, ускорения, длины в метрах или spillback.

- `counts`: текущие ожидающие объекты подхода, не накопленные пересечения линии.
- `queue_length`: количество ожидающих транспортных объектов.
- `traffic_score`: сумма весов (car 1, bus 2.5, truck 2, motorcycle 0.5,
  bicycle 0.4) + 0.05 × возраст самого старого ожидающего объекта в секундах.
- `arrival_rate_per_minute`: прибывшие за последние min(t, 60) секунд / окно × 60.
- `mean_wait_seconds`: средний возраст объектов текущей очереди.
- `congestion`: low < 5, moderate 5–14, high ≥ 15; пороги конфигурируются.
- `total_wait_seconds`: сумма завершённого ожидания и возраста оставшихся очередей.
- `mean_wait_per_arrival_seconds`: total_wait / arrived; включает незавершённое
  ожидание на конце горизонта, не прогноз финальной задержки этих объектов.
- `mean_completed_wait_seconds`: среднее только по departed, отдельно от предыдущего KPI.
- `throughput_per_minute`: departed / время × 60.
- `max_queue`: максимум общего числа ожидающих объектов, включая пешеходов.

Глобальные KPI включают транспорт и пешеходов с одинаковым весом одного объекта.
Автобус не преобразуется в число пассажиров. Headway входит в моделируемое ожидание.
Comparison использует одинаковые scenario, seed, timing, горизонт и шаг 0.5 s;
процент улучшения отрицательный при ухудшении, null при нулевой базовой задержке.
Live simulation время продвигается на tick; wall-clock scheduler может работать
медленнее. Поэтому воспроизводимый benchmark запускается отдельно от live runtime.

## Эксплуатация backend

Один uvicorn worker: несколько процессов создали бы несколько независимых
контроллеров. Lifespan запускает task и останавливает его с all-red и close порта.
Timeout hardware и просрочка tick переводят runtime в fault. Liveness проверяет
HTTP-процесс, readiness — живую task и свежесть успешного tick.

WebSocket хранит для клиента максимум один snapshot: медленный клиент пропускает
промежуточные состояния. История не накапливается. Ошибка runtime отправляет all-red
snapshot и закрывает канал. Heartbeat устройства ещё не реализован.

`decision` содержит предложение алгоритма на текущем tick. `green_target_seconds`
содержит принятую safety длительность текущего green; она фиксируется на входе
в фазу и не пересчитывается вслед за уменьшающейся очередью.

Control endpoints допускают X-Operator-Token через STA_OPERATOR_TOKEN. По умолчанию
локальный demo без токена, запуск на 127.0.0.1. Это не полноценная auth/RBAC.
Только один benchmark одновременно, выполнение в thread pool, live engine не изменяется.
SQLite пока не используется: в этом этапе нет требования долговременной записи;
JSON benchmark можно сохранить в recordings. Storage repository и SQLAlchemy
миграции будут добавлены вместе с историей запусков, затем возможен PostgreSQL.

## Использованные официальные источники

Lifecycle соответствует [FastAPI Lifespan](https://fastapi.tiangolo.com/advanced/events/),
WebSocket adapter — [FastAPI WebSockets](https://fastapi.tiangolo.com/advanced/websockets/).
Настройки используют [pydantic-settings](https://github.com/pydantic/pydantic-settings).

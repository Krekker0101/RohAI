# Traffic Event Model

Контракт находится в `traffic_core.traffic_events`; формирование событий —
`traffic_vision.state_estimation.TrafficStateEstimator`. Контракт не содержит pixels
или traffic-control policy. События текущего кадра доступны в
`traffic.estimation.events` через существующий vision state/telemetry API.

| Событие | Условие |
|---|---|
| VehicleEnteredApproach | Контакт пересёк конечный entry segment по ходу движения |
| VehicleStopped | Подтверждён эпизод низкой метрической скорости |
| VehicleJoinedQueue | Остановившийся автомобиль находится в queue zone до stop line |
| VehicleStartedMoving | Подтверждённая остановка завершилась |
| VehicleCrossedStopLine | Контакт пересёк конечный stop segment по ходу движения |
| VehicleExitedIntersection | Контакт пересёк конечный exit segment по ходу движения |
| PedestrianArrived | Наблюдался переход из-за пределов зон в waiting zone |
| PedestrianStartedCrossing | Waiting → crossing одного crosswalk |
| PedestrianClearedCrossing | Crossing → exit одного crosswalk |

Первый кадр track, даже внутри подхода/перехода, не создаёт прибытие. Для пешехода
также нужна наблюдаемая геометрическая смена зоны. Неоднозначность зон не
интерпретируется как начало или завершение перехода.

Событие содержит уникальный в сессии event_id, type (`kind`), timestamp в секундах
временной шкалы источника, track_uid, session_id, source, confidence,
geometry_version, calibration_version, approach и optional lane_id.
UTC относится к метаданным калибровки; его нельзя смешивать с media timestamp.
Confidence события сейчас основан на detector confidence и не является
эмпирически откалиброванной вероятностью правильного события.

Для линий применяется hysteresis 0.1 m. Timestamp пересечения интерполируется
на фактическом соседнем сегменте траектории; публикация может задерживаться до
подтверждения downstream стороны. Это линейная интерполяция, а не измерение
непрерывной траектории между кадрами. Обратное пересечение и пересечение продолжения
линии вне её конечного сегмента не создают соответствующее событие.
Entry/stop/exit учитываются один раз за жизнь канонического track.

## Идентичность и пропуски

Reconciliation ограничено 1 s, 1 m и совпадением класса. Требуется взаимно
однозначный кандидат: неоднозначный сосед не выбирается автоматически. Новая
смена raw ID может сохранить road UID, wait state и уже опубликованные crossings.
Длительные пропуски и сложные сцены не имеют гарантии re-identification.

Runtime diagnostics разделяют `reconciled_id_changes`, `lost_tracks`,
`recovered_tracks`, `duplicate_arrivals` (подавленные повторные entry crossings).
Истинные `id_switches` и `track_fragmentation` в runtime равны null: их невозможно
достоверно посчитать без ground truth. `benchmark_identities` измеряет их по
упорядоченным `IdentitySample` с истинной и оценённой идентичностью, включая
отсутствующие наблюдения. Fragmentation — восстановление после пропуска;
ID switch — смена последней сопоставленной идентичности; duplicate arrivals —
лишние arrival events на одну ground-truth траекторию.

Краткая потеря даёт неизвестные агрегаты; после истечения памяти действует
дополнительный 5 s интервал неопределённости. Память ограничена 10 000 tracks,
история событий — 60 s и 160 000 событий. Переполнение не скрывается как точный
подсчёт. При смене stream epoch статистика сессии сбрасывается. Это поток событий
текущего кадра и bounded window, не долговечный event store; потребитель, которому
нужен полный журнал, должен сохранять события по event_id и контролировать пропуски.

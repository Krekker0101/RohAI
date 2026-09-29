# RewardSpecification v1

`configs/control/reward-reference.json` фиксирует reference objective, а `reward-v1.schema.json` — контракт. Это не оптимальные коэффициенты. По умолчанию total_delay имеет вес 1, остальные компоненты — 0; `approved_for_training=false`. Training accessor отклоняет такой reward. Для будущего обучения нужны review, непустое approval_basis и замороженный hash.

| Компонент | Измерение за шаг | Unit | Знак |
| --- | --- | --- | --- |
| total_delay | прирост queue + pedestrian wait + excess downstream hold | entity*s | − |
| queue_delay | прирост vehicle queue wait | vehicle*s | − |
| pedestrian_delay | прирост pedestrian wait | person*s | − |
| throughput | завершённые выходы, включая пешеходов | entity | + |
| maximum_wait | максимальное текущее ожидание × dt | s² | − |
| fairness | разница максимальных ожиданий направлений × dt | s² | − |
| spillback | накопленная длительность lane spillback | lane*s | − |
| switching | фактические начала clearance | transition | − |

Total delay включает upstream очередь и ожидание после stop line сверх номинального transit time. Nominal transit/crossing time не является задержкой. Maximum wait и fairness учитывают ещё не завершённые downstream поездки; это sampled integral, а не отдельная оценка каждой непрерывной траектории. Пустое направление имеет максимум 0. Такое определение fairness прозрачно, но не является доказательством справедливости.

Scalar reward — сумма signed weighted components. Coefficients переводят физические единицы в единицы выбранной utility. Компоненты пересекаются: включение total_delay вместе с queue_delay/pedestrian_delay осознанно повторно усиливает часть задержки. Нельзя выдавать это за независимые costs. Каждый step и episode содержит contribution; episode дополнительно хранит raw, unit и specification. Нулевая contribution не убирает измерение из отчёта.

## Fairness и незавершённые ожидания

Для всех направлений, vehicle classes и отдельно pedestrians выдаются count, mean, median, p90, p95, p99, maximum. Пустая группа имеет null statistics. `completed_total_wait` содержит законченные поездки; `unfinished_wait_lower_bound` — накопленную задержку незавершённых; `all_arrivals_lower_bound` объединяет их с явным right_censored_count. Эти percentiles не являются окончательными lifetime waits при незавершённых поездках.

Service ledger сохраняет identity, class, approach, queue wait и excess downstream hold. Проверяется полнота по arrived и отсутствие дубликатов. Нельзя улучшить отчёт, просто исключив оставшихся в очереди к концу эпизода. Supervisor дополнительно ограничивает отсутствие возможности обслуживания; fairness KPI всё равно обязателен при saturation и camera failure.

Выбор ненулевых дополнительных весов должен опираться на дорожный use case, контроль чувствительности на TRAIN/VALIDATION и отдельно утверждённые требования. После просмотра TEST менять reward для того же experiment нельзя. Ground truth scoring допустим внутри simulator, но никогда не поступает actor через observation.

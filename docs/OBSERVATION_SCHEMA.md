# ObservationV1

Машинный контракт: `configs/control/observation-v1.schema.json`; точный порядок, units и scale каждого признака: `configs/control/observation-layout.json`. Всего 108 признаков.

Для числового измерения `value = clip(raw / scale, 0, 1)`. Boolean и one-hot имеют scale 1. Каждый признак имеет отдельные `valid` и `clipped`: отсутствующее значение кодируется нулём с valid=0; настоящий ноль имеет valid=1. Clipping не скрывается. Scales — фиксированные инженерные диапазоны для v1, не статистика TEST и не acceptance thresholds.

| Признак на каждый approach | Unit | Scale |
| --- | --- | ---: |
| queue count | vehicle | 100 |
| queue length | m | 500 |
| oldest / mean wait | s | 300 |
| arrival / departure estimates | vehicle/min | 120 |
| pedestrian waiting | person | 100 |
| pedestrian oldest wait | s | 300 |
| time since service | s | 600 |
| downstream occupancy | fraction | 1 |
| measurement age | s | 10 |
| observation confidence | fraction | 1 |
| traffic score существующего estimator | score | 1000 |

Дополнительно на approach: has_received_service, camera_connected, reliable и one-hot статуса perception. Это domain features, а не logits или внутренние значения detector. Global features: текущая, pending и последняя запрошенная phase; stage; phase/stage age; текущий и pending green budget; длительность и acceptance последнего запроса; pedestrian time since service; fault latch. Времена фазы и budgets масштабируются на 180 s, pedestrian service age — на 600 s.

Queue length оценивается по наблюдаемым metric contact distances и class-length prior; это оценка, а не точная длина автомобиля. При недостоверной метрике значение неизвестно. Departure rate и downstream occupancy пока отсутствуют в базовом perception output: valid=0. `ObservedSupplement` может предоставить их с source, confidence и timestamp; измерения старше 3 s или из будущего не используются. Ground truth не является fallback.

## Temporal baseline

Gymnasium Box имеет shape `(4, 325)`, float32, диапазон [0,1]. Каждая строка: 108 values, 108 validity flags, 108 clipping flags, history_present. Порядок oldest → newest, интервал решения 1 s. После reset история дополняется нулевыми строками слева; реальная строка имеет history_present=1. Timestamp должен строго возрастать. Массив выдаётся копией.

Stack содержит четыре выборки, а не четыре секунды идеальной видеозаписи: задержки и stale observations остаются видимыми. Last request/pending phase позволяют различать последствия собственных действий. Длинные occlusion и неизвестный demand остаются скрытыми. Recurrent architectures не добавлены. Перед изменением scale, порядка или состава нужен новый schema version и повторная оценка clipping/coverage на TRAIN/VALIDATION.

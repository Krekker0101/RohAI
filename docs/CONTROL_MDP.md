# Learning Control Environment v1

Далее описан исходный foundation contract. Результаты последующего обучения вынесены в [CONTROL_BENCHMARK_REPORT.md](CONTROL_BENCHMARK_REPORT.md); они не меняют контракт actor/supervisor и не включают автоматический production promotion.

Реализация: `traffic_simulator.learning`. Это формализация задачи и проверяемая среда; обученной policy и доказательства улучшения пока нет.

## Control problem

Задача является конечногоризонтной POMDP. Скрытое состояние содержит транспорт, очереди, downstream storage, пешеходов, demand и состояние сенсоров. Динамика Digital Twin принимает фактический безопасный сигнал и изменяет будущий поток. Actor получает только `ObservationV1`, построенную из `ObservedTrafficState` и собственного состояния supervisor. GroundTruthState используется исключительно scorer для reward, инвариантов и итоговой оценки.

Решение принимается раз в 1 s, физика и supervisor работают с шагом 0.25 s. На каждом физическом шаге запрос повторно проходит safety arbitration. Переход определяется сценарием, seed, текущим состоянием и принятым сигналом. Конец заданной длительности — `truncated=True`; нарушение инвариантов — `terminated=True`. Оба требуют reset.

Оптимизационная цель: уменьшать накопленную задержку при сохранении безопасных переходов и ограничений обслуживания. Безопасность не покупается штрафом в reward: она обеспечивается внешним supervisor. Fairness проверяется отдельно от среднего и scalar reward. Deadline ограничивает отсутствие возможности обслуживания; он не гарантирует фактический выезд при spillback или отказе камеры.

## Information boundary

`TrafficPolicy.reset(seed)` и `propose(observation, history) -> ControlRequest` — общий контракт Fixed, Adaptive, Actuated и Learned. LearnedPolicy пока является адаптером предоставленной функции inference. Все четыре проходят один supervisor. Доступа к лампам через action space нет.

Encoder не принимает simulator или GroundTruthState. Недоступные измерения маскируются, а не заполняются идеальными значениями. Четыре последних observation дают простой temporal baseline, но не превращают POMDP в доказанно Markov state. Вектор включает предыдущий запрос и незавершённый переход.

Reward и episode diagnostics привилегированы. `info` предназначен для evaluator/logger; передавать его actor как признаки запрещено протоколом. Python API не является sandbox: пользовательский код технически может обратиться к объекту среды. Проверяемая граница — официальный policy runner и encoder, не защита от злонамеренного кода.

## Evaluation

FixedTimeController сохраняет циклическое расписание. CurrentAdaptiveController использует общую с существующим controller формулу длительности. ActuatedController выбирает фазу по наблюдаемому спросу, допускает gap-out и ограниченное продление. Downstream occupancy влияет на него только при наличии измерения; без него это queue-actuated baseline, а не полноценный MaxPressure.

Одинаковые arrival tapes, timing и supervisor обязательны для сравнения. Fast Simulator сохранён. Digital Twin остаётся приближением с инженерными параметрами perception; полевые Vision Benchmark ещё не подтверждают эти параметры. Результаты simulator не доказывают улучшение на дороге.

Связанные контракты: [observation](OBSERVATION_SCHEMA.md), [actions](ACTION_SCHEMA.md), [reward](REWARD_SPECIFICATION.md), [эксперимент](RL_EXPERIMENT_PROTOCOL.md).

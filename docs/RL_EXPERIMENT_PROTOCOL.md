# Learning Control Experiment Protocol

Этот документ фиксирует foundation-этап до обучения. Следующий контролируемый PPO-эксперимент описан в [CONTROL_BENCHMARK_REPORT.md](CONTROL_BENCHMARK_REPORT.md); его собственный preregistration — `configs/control/ppo-experiment-v1.json`. Утверждения ниже «обучение не запускалось» относятся к исходному этапу, а не к состоянию проекта после нового эксперимента.

Статус: environment foundation. RL обучение, policy checkpoint и claim об улучшении отсутствуют. Real Vision Baseline остаётся заблокирован отсутствием независимого полевого набора; perception noise Digital Twin пока не калиброван таким benchmark.

## Зафиксированный эксперимент

`configs/control/scenario-splits.json` содержит полные ScenarioDefinition и canonical manifest hash. TRAIN: balanced, NS-heavy, burst traffic (seeds 11001–11003). VALIDATION: EW-heavy, pedestrian-heavy (22001–22002). TEST: rapid demand change, balanced (33001–33002). CHALLENGE: saturated, camera outage, night/degraded vision (44001–44003). Длительность каждого — 180 s.

IDs, seeds и arrival tape hashes не пересекаются. Balanced TEST — новая реализация знакомого семейства; rapid demand change — отдельное семейство. Это не заявление о независимости всех физических распределений. Ранее изученный каталог seed 42 не используется как новый held-out набор.

Training accessor разрешает только TRAIN, tuning accessor — TRAIN/VALIDATION. Environment из split фиксирует scenario seed, поэтому reset(seed) не заменяет TEST реализацию. Прямой конструктор нужен для unit tests/checker; он не является официальным training accessor. File hashes обнаруживают изменение, но не заменяют права доступа и подписанный registry.

Перед первым обучением фиксируются reward hash, algorithm config, budget, seeds, критерии остановки, safety/fairness/downstream gates и план анализа. Требования выводятся из use case и sensitivity analysis на TRAIN/VALIDATION. TEST нельзя использовать для reward tuning или выбора hyperparameters. После анализа TEST дальнейшая настройка требует нового независимого held-out release. CHALLENGE — отдельная проверка отказов и saturation, а не источник подбора reward.

## Выбор кандидатов после формализации

| Кандидат | Соответствие задаче | Ограничения |
| --- | --- | --- |
| DQN | 9 дискретных запросов; replay позволяет повторно использовать transitions | нестабильность bootstrap и replay при смене сценариев; scalar reward не гарантирует safety/fairness |
| categorical PPO | тот же action space; полезный policy-gradient comparator | on-policy sampling требует новых rollout; clipping не является safety guarantee |
| ActuatedController | наблюдаемые очереди, bounded extensions, минимальная inference cost | ограниченный горизонт; downstream pressure доступен только при реальном измерении |

Для будущего ограниченного эксперимента выбраны DQN и PPO. Это инженерная гипотеза, не результат обучения. Оба могут принимать простой flattened observation stack; новые recurrent architectures не реализованы. Все предложения проходят один supervisor. Sample efficiency, variance между seeds, wall-clock budget и p95 inference latency предстоит измерить; числовые преимущества не предполагаются. Continuous-control SAC не нужен для текущих девяти дискретных действий.

Поддержку action/observation spaces и особенности реализаций следует сверять с [DQN API](https://stable-baselines3.readthedocs.io/en/master/modules/dqn.html) и [PPO API](https://stable-baselines3.readthedocs.io/en/master/modules/ppo.html). В этом этапе алгоритмическая библиотека и optimizer не добавлены. Machine-readable план: `configs/control/experiment-plan.json`.

## Воспроизводимость и проверка

Установить environment extra: `uv sync --extra vision --extra learning --group dev`. Используется Gymnasium 1.3.0; `reset(seed)` и `step(action)` соответствуют [Env API](https://gymnasium.farama.org/api/env/). Официальный [check_env](https://gymnasium.farama.org/api/utils/) выполняется тестом и evaluation CLI.

```powershell
.venv/Scripts/pytest.exe tests/test_learning_control.py -q
.venv/Scripts/python.exe scripts/evaluation/control_environment.py --split VALIDATION --output docs/control_environment_validation_v2.json
```

CLI требует новый output path, существующий отчёт не перезаписывает. Проверка в [validation v2](control_environment_validation_v2.json) содержит только VALIDATION: два сценария × Fixed/Adaptive/Actuated, одинаковые arrivals, checker, decomposition, fairness и invariants. TEST и CHALLENGE в этом этапе не запускались. Предыдущий validation report сохранён как история реализации.

PolicyRunManifest фиксирует seed, environment/observation/action schemas, reward и его hash, split hash, algorithm config, git commit/dirty flag, code hash, dependency versions, timestamps, checkpoint/hash и evaluation metrics. COMPLETED без checkpoint и metrics запрещён; freeze проверяет checkpoint hash и пишет новый файл. Schema находится в `configs/control/policy-run.schema.json`. Реального training run manifest пока нет, поскольку обучение не запускалось.

Будущий trained policy сравнивается с тремя baseline на одинаковых arrival tapes, perception profiles и safety settings. Нужно публиковать распределения задержек, censoring, throughput, spillback, interventions и latency, а не только scalar reward. Улучшение можно обсуждать только после заранее определённой held-out evaluation; текущие synthetic проверки подтверждают контракт среды, не эффективность обученного управления.

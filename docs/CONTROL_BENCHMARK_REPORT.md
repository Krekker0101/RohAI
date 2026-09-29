# Control Benchmark Report — 2026-09-29

Результат: **Stable superiority not demonstrated; retain heuristic default**. Production default остаётся Adaptive. Обучение реально выполнено; автоматического promotion или изменения backend config нет.

PPO обучен с 12 независимыми seeds для каждого из пяти вариантов: 60 runs × 2160 решений, по 12 эпизодов/run. Это ограниченный synthetic experiment; сходимость не установлена. Validation выбрал checkpoint 2160, общий для всей группы seeds. Не выбирался лучший seed по TEST.

[Зафиксированный план](../configs/control/ppo-experiment-v1.json), [selection](../artifacts/control/20260929-v1/selection.json), [raw aggregate](../artifacts/control/20260929-v1/summary.json), [все episode/evaluation records](../artifacts/control/20260929-v1/).

## Условия и выбор модели

Все policies используют одинаковые ScenarioDefinition, seeds, arrival tapes, perception profile, timing и SafetySupervisor. Их соответствие проверено по hash. Noise имеет одинаковые настройки/seed, но не обязано давать идентичную последовательность детекций: сами trajectories меняются от действий. Ground truth используется только scorer.

Reward — отрицательная суммарная задержка entity*s, с фиксированным множителем 0.01 для optimizer. Веса не подбирались. Алгоритм: SB3 PPO 2.9.0, MLP 32×32, learning rate 0.0003, rollout 360, batch 60, epochs/update 4. Checkpoint сохраняет optimizer и RNG; точное совпадение параметров после episode-boundary resume проверено отдельным тестом.

Pilot использовал 4 VALIDATION seeds: SD парной разницы = 364.79 entity*s. Для precision planning выбран half-width 1,322.45 (10% среднего actuated, не acceptance margin). Правило min=12/max=16 выбрало 12. Ограничение бюджета по этому расчёту: False. Четыре pilot seeds включены в итоговую группу, а не добавлены как независимые повторные runs.

Precision planning не гарантирует точность на новом распределении. На TEST полуширина CI95 парной разницы delay составляет 2,236.79 entity*s; сопоставлять её нужно с запланированным half-width выше. Размер выборки после просмотра TEST не увеличивался. Это ограничивает точность оценки величины эффекта, даже когда знак ухудшения относительно Actuated установлен.

TEST запущен только после immutable selection. Повторный запуск завершённого experiment отклоняется. Resume недописанной final suite допускает только те же jobs и hashes. Ablation не использовались для повторного выбора основной модели по TEST.

## TEST: total delay, entity*s

| Policy | Mean | Median | SD | p90 | p95 | p99 | Worst |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| fixed | 10,936.97 | 10,936.97 | 5,224.95 | 13,892.65 | 14,262.11 | 14,557.67 | 14,631.56 |
| adaptive | 12,740.22 | 12,740.22 | 7,409.91 | 16,931.90 | 17,455.86 | 17,875.02 | 17,979.81 |
| actuated | 8,556.35 | 8,556.35 | 2,407.30 | 9,918.12 | 10,088.34 | 10,224.52 | 10,258.56 |
| full | 13,901.31 | 12,136.81 | 5,477.82 | 22,195.84 | 24,002.05 | 24,209.31 | 24,209.31 |

Таблица описывает распределение episode outcomes. Baselines имеют два scenario outcomes; learned — те же два сценария для каждого training seed. Эти строки нельзя считать равным количеством независимых наблюдений. Для вывода используется парный анализ ниже.

| Сравнение | Средняя разница learned − baseline | CI95 | Одновременный CI | Вывод |
| --- | ---: | --- | --- | --- |
| fixed | 2,964.34 | [773.33; 5,246.90] | [-650.96; 7,147.19] | INCONCLUSIVE |
| adaptive | 1,161.09 | [-1,029.92; 3,443.65] | [-2,454.21; 5,343.94] | INCONCLUSIVE |
| actuated | 5,344.97 | [3,153.96; 7,627.53] | [1,729.67; 9,527.81] | WORSE |

CI строится bootstrap по независимым training seeds после усреднения парных разниц по фиксированному suite. Повторения baseline не увеличивают n. Simultaneous CI использует заранее определённую Bonferroni-поправку; 20 000 resamples. Это приближённый interval с ограниченной tail resolution, не математическая гарантия safety. CI условен на двух TEST scenarios и не оценивает неопределённость всех дорог/камер.

Для каждого KPI сохранены mean, median, SD, CI95, p90/p95/p99, min/max и worst-case job в JSON/CSV. Парные CI по каждому critical metric и каждому сценарию находятся в comparisons. Delay включает queue + excess downstream hold; throughput включает vehicles и pedestrians. Queue length — максимальная lane extent в метрах, без невидимого upstream backlog; queue count включает upstream. Fairness включает right-censored unfinished waits; эти percentiles не являются завершёнными lifetime waits.

## Известная pedestrian-heavy regression

Повторён catalog pedestrian-heavy seed=42, duration=180 s. Это ранее известный регрессионный тест, а не новый held-out scenario.

| Policy | Pedestrian delay, person*s | Pedestrian maximum wait, s |
| --- | ---: | ---: |
| fixed | 15,044.50 | 143.62 |
| adaptive | 15,844.50 | 147.08 |
| actuated | 7,559.50 | 73.22 |
| full | 12,396.33 | 121.59 |

Статистические gates: False. Все simulator invariants: True. Улучшение aggregate delay не отменяет ухудшение pedestrians, tail wait или другого critical subgroup. Inconclusive gate не даёт PASS. В raw fairness сохранены direction, vehicle class и pedestrians с count и censoring.

## Ограничения вывода

Небольшой training budget не позволяет опровергнуть возможность более сильного RL после другого независимого эксперимента. В этом запуске оценивается конкретный PPO и зафиксированный бюджет. Нельзя дообучить по увиденному TEST и повторно назвать этот же TEST независимым. Полевой perception benchmark отсутствует; stress levels не являются измеренными road probabilities. SUMO fidelity и реальная безопасность данным экспериментом не подтверждаются.

Эпизоды начинаются с пустой среды и длятся 120/180 s; warm-up и steady-state оценка не проводились. Queue metrics сэмплируются раз в 1 s, поэтому maximum не является точным пиком непрерывной траектории. Inference latency в raw измерена при параллельном CPU evaluation, а не на отдельном production устройстве.

## Воспроизведение

```powershell
uv sync --extra vision --extra control-training --group dev
.venv/Scripts/python.exe scripts/training/control_experiment.py run --workers 4
```

Команда возобновляет незавершённый experiment, но откажется повторять уже завершённый. Для нового исследования нужны новый preregistration/output и новый held-out release. Checkpoint zip/rng сохранены локально под artifacts/control и исключены из Git; JSON provenance и raw results сохранены отдельно. Контроль целостности не является подписью.

Реализация использует [официальный PPO API](https://stable-baselines3.readthedocs.io/en/v2.9.0/modules/ppo.html) и штатный save/load; backpropagation не переписывался.

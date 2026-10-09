# Честное сравнение управления

Политики: Fixed-Time, existing cyclic Adaptive, Actuated, single-intersection
Max-Pressure и новый RohAI PPO. Все используют одинаковые Timing/SafetySupervisor,
physics, scenario initial state и arrival tape seed; pairwise tape hashes сверяются.
Adaptive переиспользует original adaptive_green_seconds; сравнение относится к
общему simulator shield adapter, а не завершённой physical HIL приёмке.
Downstream sensors отсутствуют: Max-Pressure явно upstream_only, actor не получает
latent simulator occupancy. MPC отсутствует, speculative oracle advantage не заявляется.

13 disjoint TEST realization seeds; training/validation seeds отдельные.
Каждый fixed baseline считается один раз/scenario. PPO — три independent
training seeds; повторённый baseline не превращается в дополнительные independent runs.
Paired bootstrap сначала усредняет scenario deltas внутри training seed, затем
resamples seeds; CI условный для этой suite, four-comparison correction.
Три seeds недостаточны для устойчивой общей/дорожной победы даже при узком CI.
TEST не выбирает hyperparameters/checkpoint. Одно заранее объявленное budget2x
повторение frozen до просмотра нового TEST меняет только training steps.

## Метрики и границы интерпретации

Raw episode reports: total/average delay, median/p95/max wait, throughput,
mean/max queue count/extent, pedestrian delay/service, switch count/frequency,
starvation, spillback и invariants. All-arrival waits для незавершённых trips —
right-censored lower bounds; completed-trip distribution сохраняется отдельно.
Stops — distinct vehicles entering modeled queued/upstream state; не число
микроскопических торможений. Starvation — waiting entities crossing documented
service-bound threshold. Spillback events — contiguous sampled intervals с
положительным aggregate lane-spillback increment. Safety violations — failed
invariant indication, а не normal shield intervention/clearance-lock count.

Moving average, exponential smoothing и historical median используют только
измеренную историю. Forecast содержит horizon, descriptive uncertainty interval;
при короткой/stale history возвращается None. В TEST quality оценивается через
10 s future **observed rolling arrival-rate estimate**, не скрытую true future tape.
MAE/sample counts находятся в episode forecast_quality; это не field forecast
accuracy или calibrated uncertainty coverage. Forecast не включён в управление
до подтверждения полезности на подходящих независимых данных.

## Доказательства

Основной run: artifacts/rohai-ai-2.0/control-stage2-smoke/.
Controlled repeat: artifacts/rohai-ai-2.0/control-stage2-budget2x/.
В каждом: frozen plan/selection, реальные ZIP/.rng/.json checkpoints,
optimizer CSV, training episodes, validation/TEST metrics и полные decisions.
benchmark.json хранит фактические средние и paired comparisons; не сравнивайте
эти новые результаты с исторической таблицей как один и тот же protocol.
494 Python tests passed, 8 hardware HIL skipped; full mypy/Ruff прошли.
Финальные benchmark numbers и readiness фиксируются в stage2-verification.json.
Нет automatic promotion, physical activation или доказательства road transfer.

## Основной smoke: фактический TEST результат

| Policy | Mean total delay, entity·s | Mean delay/arrival, s | Mean departures |
|---|---:|---:|---:|
| Fixed | 7578.65 | 35.71 | 65.23 |
| Existing Adaptive | 7608.67 | 36.58 | 61.23 |
| Actuated | 6680.94 | 30.62 | 78.23 |
| Max-Pressure upstream-only | 6676.71 | 30.62 | 78.31 |
| RohAI PPO 2400 | 7814.30 | 37.56 | 60.85 |

Simultaneous paired cost-delta intervals, three training seeds, fixed suite:
vs fixed [-312.77,924.44], adaptive [-342.79,894.42] — INCONCLUSIVE;
actuated [584.94,1822.15], upstream pressure [589.17,1826.38] — WORSE.
Все проверенные invariants прошли. Promotion остаётся false.
RL хуже особенно в EW-heavy, NS-heavy и morning/evening peak.
VALIDATION показывает constant deterministic action per seed: action0/action4/action2
на всех доступных кадрах; в сумме 1445 каждого. Это наблюдение недостаточной
learned responsiveness, не causal доказательство механизма. Budget2x повтор
проверяет одну гипотезу — training budget, без нового reward/TEST tuning.

## Controlled repeat: 4800 steps

Mean PPO total delay вырос до **8022.73 entity·s**; условные paired сравнения
показывают WORSE относительно всех четырёх baselines. Удвоение бюджета в этом
опыте не исправило responsiveness/качество; дальнейший TEST tuning не выполнялся.
Все invariants прошли. Это отрицательный результат ограниченного эксперимента,
не доказательство, что PPO в принципе неприменим. Нужны full validation studies
credit assignment/action aliasing и budget с preregistration новых TEST suites.

Aggregate table/figure: control-stage2-report-final/metrics.csv, control-comparison.svg/png.
Forecast quality уточнена независимым scripts/evaluation/intelligence_reports.py:
target берётся **ровно** через horizon=10 s; отсутствие target из-за outage или
конца эпизода исключает sample. Это исправляет raw episode forecast_quality,
где pending forecast мог быть scored на первом более позднем recovered observation.
Авторитетные strict-horizon MAE/sample counts — control-stage2-report-final/*-forecast.json;
старые reports сохранены как trace, controller/training/основные KPI не менялись.
Fixed TEST observation stream, north approach, exact 10 s horizon: 1269 scored
samples/method; MAE moving average 6.930, exponential smoothing 4.322, historical
median 6.704 vehicle/min. Frames коррелированы во времени, не 1269 независимых
scenarios; field accuracy и calibrated interval coverage не утверждаются.

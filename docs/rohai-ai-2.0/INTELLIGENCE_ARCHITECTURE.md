# RohAI Intelligence Engine — Stage 2

Дата: 2026-10-09. Модуль не зависит от camera/UI и не содержит GPIO/serial executor.
Он расположен в `traffic_simulator.learning`, переиспользует существующие
ObservationV1, PPO pipeline, Digital Twin и core SafetyController.

## Аудит предыдущей версии

Исторический эксперимент `artifacts/control/20260929-v1/summary.json`:
12 seeds, 2160 шагов/seed (12 коротких episodes), promotion=false.
TEST mean total delay: fixed 10936.97, adaptive 12740.22, actuated 8556.35,
PPO 13901.31 entity·s. CHALLENGE: 8096.39 / 8500.74 / 7783.07 / 9402.58.
Shield intervention fractions на TEST: .218/.284/.287/.483 соответственно.
Они включают нормальные clearance locks и не являются числом safety violations.
Reward имел только total-delay cost; explicit geometry отсутствовала в actor input.
Feature/noise ablations были inconclusive. Малый бюджет и action aliasing shield
являются гипотезами о причинах, не доказанными causal explanations.
Исторический audit сохранён отдельно в control-stage2-smoke/prior-ppo-audit.json.
Старые weights не выдаются за обучение Stage 2 и не используются новой моделью.

## Контракты и поток

`intelligence_contracts.py`: TrafficObservation, ControlAction, PolicyState,
PolicyMetadata, PolicyDecision, SafetyConstraints, DecisionAudit и ControlPolicy.
TrafficObservation наследует существующие normalized values/valid/clipped masks;
содержит current phase/stage, elapsed/budgets, queues/waits/arrivals/confidence,
known geometry и optional свежие measured downstream counts. History ограничена.
One-hot phase/stage/pending, finite values, shape, monotonic clocks проверяются.

PolicyAdapter сохраняет existing fixed/adaptive/actuated, PPOPolicy делает только
inference. MaxPressurePolicy оценивает aggregate pressure для одного перекрёстка.
Без exit counters его режим **upstream_only**, не theoretical network max-pressure.
Counter keys означают сопоставленные выходные потоки подходов; movement routing
и network optimality не утверждаются. MPC не добавлен: нет field-calibrated
forecast/downstream model для честной оценки predictive advantage.

`IntelligenceEngine → SafetySupervisor → существующий SafetyController`.
Actor возвращает phase/duration request, не lamp/GPIO state. Модель не получает
hardware ports, ground_truth, будущую arrival tape или reward-scoring state.
PPO actor history дополнена тремя известными static geometry features.
Privileged GT используется исключительно reward/benchmark scorer.

SIMULATION и SHADOW допустимы; PHYSICAL MAKET запрещён. Для programmatic SHADOW
передают production-compatible ObservedTrafficState и encode(...) от отдельного
shadow supervisor, затем Engine.decide возвращает audit. Его signals — результат
теневого safety, не подтверждённое физическое исполнение. Existing Adaptive
в рабочем backend остаётся активным; GUI/ESP32 интеграция не заменялась.

Новые исходники: intelligence_contracts/policy/engine/experiment.py и forecast.py.
Изменены только learning/environment.py и experiment_runtime.py; добавлены
3 experiment configs, 7 JSON schemas и tests/test_intelligence_engine.py.
Единый `rohai control` CLI запускает отдельный offline experiment process.
scripts/evaluation/intelligence_reports.py создаёт independent strict-horizon
forecast scores, CSV и standalone comparison figure из сохранённых audits.
Новый RL algorithm не изобретён: обучены собственные policy weights на стандартном PPO.
Multi-intersection/network control в этот этап не входит.

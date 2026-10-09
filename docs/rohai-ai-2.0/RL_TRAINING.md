# Воспроизводимое PPO обучение

Переиспользован Stable-Baselines3 PPO 2.9.0 и существующий train_run:
[PPO API](https://stable-baselines3.readthedocs.io/en/master/modules/ppo.html).
Формальная задача — shielded partially observed single-intersection control.
Actor: четыре history frames normalized semantic values + validity/clipping masks,
known lanes/8, lane length/1000, headway/10. Action Discrete(9): три известные
безопасные phase templates × requested green budget {8,20,45} s.
Shield независимо применяет minimum green, yellow/all-red, pedestrian clearance,
maximum green и service deadlines. Несколько разных requests могут иметь одинаковый
shield result; это action aliasing, а не обход constraints.

```powershell
.venv\Scripts\python.exe -m traffic_simulator.learning.intelligence_experiment prepare --config configs/control/intelligence-smoke-v2.json --output artifacts/rohai-ai-2.0/control-stage2-smoke
.venv\Scripts\python.exe -m traffic_simulator.learning.intelligence_experiment train --plan artifacts/rohai-ai-2.0/control-stage2-smoke/plan.json
.venv\Scripts\python.exe -m traffic_simulator.learning.intelligence_experiment benchmark --plan artifacts/rohai-ai-2.0/control-stage2-smoke/plan.json
```

Existing paths не перезаписываются; для нового prepare нужен новый output.
Тот же workflow доступен через общий CLI: `rohai control prepare/train/benchmark ...`.
Gym/SB3 загружаются только в отдельном control process; vision/maket команды
не получают новый eager RL dependency.
Конфиг задаёт seeds, episodes duration, steps/rollout/batch/epochs/lr, network,
device и soft wall-time budget. CPU использует один torch thread, deterministic
algorithms. CUDA selection предусмотрен, unavailable GPU явно отклоняется;
GPU запуск здесь не проверен. Budget проверяется между полными rollouts, не
прерывает physics/optimizer внутри checkpoint transaction.
Во frozen plan записаны scenarios, config/hash, source code hash, runner hashes,
git commit/dirty state, dependency versions, reward и evaluation protocol.
Changing source/dependencies/plan блокирует resume/evaluation прежнего protocol.
Hashes подтверждают integrity, а не авторство; loader только для доверенных местных outputs.

## Checkpoint и resume

`runs/full-SEED/checkpoint-STEPS.zip` — реальные policy/optimizer weights;
одноимённые .rng/.json содержат Python/NumPy/Torch/CUDA RNG, episode cursor,
hashes/versions/config. Optimizer CSV и immutable episode reports сохраняются.
Повтор train продолжает с последнего committed episode+rollout boundary.
В существующем regression test resumed и uninterrupted weights совпадают побитово.
Budget exhaustion выдаёт RESOURCE_LIMIT_PARTIAL; отсутствующий final checkpoint
не допускается в benchmark. Идентичные cache jobs можно продолжить после прерывания.
Best selection/early stopping по TEST не используется; endpoint budget заранее
зафиксирован. Общепринятый validation stop criterion не придумывался.

## Фактические запуски

Smoke: seeds 901/902/903, 2400 steps/seed, 20 episodes по 120 s, MLP [64,64],
lr=.0003, gamma=.995, GAE=.95, entropy=.01, 4 epochs, batch60, rollout240.
Controlled budget2x study меняет только training_steps на 4800 (40 episodes/seed).
Его plan frozen до просмотра нового TEST. Оба — smoke, не convergence/full training.
Weights: `artifacts/rohai-ai-2.0/control-stage2-{smoke,budget2x}/runs/full-SEED/`.
Model export — SB3 ZIP plus sidecar/RNG/plan; новый формат ONNX не нужен для
данного CPU/shadow contract. Weight hashes привязаны к frozen selection.json.
В каждом run добавлен policy-export.json: actual step count, input shape,
weights hash, protocol и запрет physical activation. Все шесть exports перечислены
в artifacts/rohai-ai-2.0/stage2-policy-exports.json. Первые 2400 parameters
в обоих опытах совпали побитово для всех трёх seeds (stage2-budget-prefix-check.json).

Full воспроизводимый конфиг: configs/control/intelligence-full-v2.json,
108000 steps × 8 независимых seeds, 600 episodes/seed по 180 s.
Используйте ту же prepare/train/benchmark последовательность с новым output.
Этот полный эксперимент **не выполнен**. Никакой run автоматически не активирует модель.

13 profiles: uniform/asymmetric, morning/evening peak, abrupt change, burst noise,
low traffic, long one-direction queue, degraded detection, partial/full outage,
spillback и различная lanes/storage geometry. Train/val/TEST realization seeds
disjoint; families общие. Это проверка новых realizations, не unseen-family/road transfer.
Dynamics: .25 s tick, headway2 s, reaction1 s, crossing6 s, finite storage и
explicit downstream capacity/travel/incident profiles. Они engineering assumptions,
не параметры, калиброванные дорожными измерениями Stage 1.

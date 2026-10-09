# Реальное обучение

Команды из корня проекта; rohai заменяется на `.venv\Scripts\python.exe -m traffic_vision.cli`.
Начните с configs/datasets/import-template.json; скопируйте рядом с собственным
медиа и заполните реальные метаданные/права/время. По умолчанию права не подтверждены:
шаблон не импортируется до review. Для train/val/test нужны минимум три независимых
source cohort, один illustrative source в шаблоне недостаточен.

```powershell
rohai dataset import import-spec.json --destination datasets/drafts/owned-v1
rohai dataset propose datasets/drafts/owned-v1/dataset.json --destination artifacts/proposals-v1
rohai dataset labels edited.json --operation review --classes datasets/drafts/owned-v1/dataset.json --width 1920 --height 1080 --reviewer EXPERT --exhaustive 0 1 --output reviewed.json
rohai dataset split datasets/drafts/owned-v1/dataset.json --output proposed-split.json
```

В draft явно внесите reviewed Asset path/hash и созданный SplitDefinition, затем:

```powershell
rohai dataset validate datasets/drafts/owned-v1/dataset.json
rohai dataset release datasets/drafts/owned-v1/dataset.json --destination datasets/releases/owned-v1
rohai benchmark vision --release datasets/releases/owned-v1 --output artifacts/owned-v1-baseline.json
rohai train preflight --config configs/training/owned-v1.json
rohai train vision --config configs/training/owned-v1.json --runs artifacts/training
```

TrainingConfig/schema: experiment, purpose, deployment_domain, immutable release
path/version/hash, baseline, pinned base model/manifest, epochs/batch/lr0/lrf,
optimizer, imgsz, augmentations, early-stop patience, save_period, seed, device,
workers, freeze, amp, deterministic=true. Реальные path/hash обязательны.
Field baseline требует MEASURED TEST GT и независимости, включая отсутствие
неопределённого pretrained exposure. Только exhaustive train/val labels экспортируются.
Run хранит config/hash, git commit/dirty state, code fingerprint, dependencies/platform,
resolved arguments, history, results.csv, best/last.pt и artifact hashes.
Реально используется [Ultralytics train/resume](https://docs.ultralytics.com/modes/train/).
Resume: `rohai train vision --config SAME.json --runs SAME_ROOT --resume RUN_DIR`.
Требует incomplete run, тот же config/environment/code и checkpoint optimizer/epoch;
stripped last.pt завершённого run не является resume checkpoint. Разное оборудование
или CUDA/PyTorch не гарантирует побитовую воспроизводимость.

## Фактически выполнено

scripts/train_vision_fixture.py: owned synthetic data, CPU, seed=2026,
epochs=1, batch=2, image_size=256, freeze=20, AdamW lr0=.0001, train/val=6/6.
Run COMPLETED; реальные best.pt/last.pt (~5.3 MB):
`artifacts/rohai-ai-2.0/fixture-runs/20261008T184437-b713c0852f2c/ultralytics/weights/`.
Полные config/environment/log/results лежат в этом run; итог fixture-training-summary.json.
Это SYNTHETIC_SMOKE_ONLY, field_model=false; deployment package запрещён.
Рабочий fixture manifest — fixture-model-manifest-v2.json. Первый с неверным
регистром class name оставлен как след отклонённой compatibility проверки.
Скрипты не перезаписывают результаты; для нового полного run задайте новые paths.

Для field candidate: package → freeze-policy до просмотра candidate TEST →
model evaluate на том же release/code/environment/protocol → registry
register/validate/approve с attribution/rationale. Train не делает APPROVED.
Начальное сравнение требует общего imgsz=640 и настоящего traffic GT для safety MAE.
Без field data утверждённая собственная дорожная модель не создана.

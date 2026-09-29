# RohAI ModelPackage и registry

ModelPackage — отдельный каталог с `weights.pt`, `model.json`, `training.json`,
`metrics.json`, `classes.json`, `manifest.json`. Package hash связывает полный
inventory SHA256, dataset version/hash, training run/manifest, class mapping,
YOLO26n detect compatibility, annotation schema 1.0, supported input и ожидаемую
metric calibration с fresh camera alignment. JSON schema находится в
`datasets/schema/model-package.schema.json`.

`metrics.json` содержит TRAIN/VALIDATION results. TEST-отчёт хранится отдельно и
ссылается на package hash, чтобы оценка не меняла уже запечатанный пакет.
Seal разрешён только после COMPLETED run; проверяются best checkpoint hash,
реальная модель/task/scale/classes и dataset identity. Упаковка в существующий
каталог запрещена. `rohai model verify --package PACKAGE_DIR` проверяет inventory,
веса, schemas, классы и соответствие training provenance без загрузки PyTorch.

## Registry

Простой локальный file-based store, без обязательной внешней MLOps-платформы:

```powershell
rohai registry register --package PACKAGE_DIR --root .runtime/model-registry
rohai registry validate --model PACKAGE_HASH --evaluation EVALUATION_DIR --reason "Reviewed complete TEST evidence"
rohai registry approve --model PACKAGE_HASH --reason "Authorized production approval"
rohai registry show
rohai registry rollback --reason "Observed production regression"
```

При нестандартном registry path укажите `--root` во всех командах.
`approve` явно активирует production pointer; register/validate этого не делают.
`approve` исполняется уполномоченным оператором после review. В этом этапе никакая
модель фактически не была APPROVED или активирована.

Переходы:

| Из | В | Условие |
|---|---|---|
| CANDIDATE | VALIDATED | Полный evaluation; recomputed gates PASS |
| CANDIDATE / VALIDATED | REJECTED | Явная причина |
| VALIDATED | APPROVED | Повторная проверка evidence; production activation |
| APPROVED | DEPRECATED | Не current и не rollback target |
| Current APPROVED | ROLLED_BACK | Возврат к предыдущему verified APPROVED |

Registry копирует package и evidence в собственное хранилище. JSON state содержит
current, previous approved и журнал событий с UTC timestamps/reasons. Обновление
одной атомарной заменой связывает статусы и pointers. Exclusive lock предотвращает
одновременных писателей. При crash до commit может остаться неиспользуемый каталог;
он не становится автоматически APPROVED. Lock recovery выполняется вручную после
проверки завершения процесса. Store рассчитан на локальный filesystem, не на NFS
или конкурентную запись с нескольких хостов.

Rollback проверяет предыдущий пакет и его evidence, помечает снимаемый current как
ROLLED_BACK и переключает pointer. После rollback previous pointer очищается;
автоматическое переключение обратно на проблемную модель не допускается.

## Startup

В `VisionSettings` задайте `model_registry` для production startup. `YOLODetector`
разрешает только current APPROVED package, повторно проверяет approval evidence,
совместимость и hashes **до** deserialization. После загрузки дополнительно сверяет
model class names. Для offline candidate evaluation есть `model_package`;
он проверяет пакет, но не означает approval. Одновременно два режима запрещены.

Старый pinned pretrained `model_path` + `manifest_path` остаётся для baseline/demo;
его weights hash также проверяется до загрузки. Это не заменяет production registry.
Пакет требует корректной calibration, но не делает camera calibration VALID:
существующие calibration/alignment/perception safety gates продолжают работать.

## Граница доверия

Immutable означает отсутствие разрешённых in-place updates плюс проверку hashes
при чтении. Это не WORM storage и не цифровая подпись. Ограничьте права записи
к registry/packages/evidence и храните резервные копии. PyTorch checkpoints могут
содержать исполняемые сериализованные объекты: seal/train работают только с
доверенными локальными весами. Проверка hash не делает неизвестный checkpoint
безопасным. Registry не предоставляет сетевой authentication/RBAC API.

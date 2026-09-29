# RohAI vision training

Официальный entrypoint: `rohai train vision --config CONFIG.json`.
В текущем checkout также доступен `python -m traffic_vision.cli`.
Оптимизацию и backpropagation выполняет установленный Ultralytics `YOLO.train()`.
Новых архитектур и RL этот этап не добавляет.

## Текущий статус

Независимый полевой TEST ещё отсутствует. Существующий
`datasets/manifests/yolo26n-baseline.json` имеет статус `BLOCKED`.
Обучение не запускалось; новых production weights нет. Тестовые fake checkpoints
проверяют orchestration и не являются результатами обучения.

## Подготовка и запуск

1. Создать и проверить immutable field release по [DATASET_PROTOCOL.md](DATASET_PROTOCOL.md).
2. Измерить закреплённый pretrained yolo26n на TEST через `rohai benchmark vision`,
   затем `rohai benchmark analytics`. Сохранить predictions и оба отчёта.
3. Скопировать шаблон `configs/training/conservative.template.json`, указать реальный
   release, его version/hash и путь к baseline. Нулевой hash в шаблоне — placeholder.
4. Выполнить `rohai train preflight --config CONFIG.json`.
5. До оценки кандидатов зарегистрировать configs и policy согласно
   [MODEL_EVALUATION.md](MODEL_EVALUATION.md).
6. Выполнить `rohai train vision --config CONFIG.json --runs .runtime/training`.

Запуск блокируется без measured independent pretrained TEST baseline именно для
этого dataset hash и закреплённой модели. Повреждение release, несовпадение версии,
неразрешённые leakage candidates и пустой TRAIN/VALIDATION также блокируют запуск.
TRAIN и VALIDATION копируются в отдельный YOLO view; TEST/CHALLENGE в него не входят.
YAML содержит только `path`, `train`, `val`, `names`, без `test` или `download`.

В YOLO view попадают только detection-кадры с exhaustive-разметкой всех классов.
Кадр с ignored object исключается целиком: удаление только bbox превратило бы объект
в ошибочный negative. `selection.json` сохраняет included/excluded frame IDs.
Labels используют нормализованный xywh; исходные аннотации остаются неизменными.

## Конфигурация и воспроизводимость

JSON schema: `datasets/schema/training-config.schema.json`. Неизвестные параметры
отклоняются, arbitrary Ultralytics overrides отсутствуют. Конфигурация включает:
base weights/manifest; dataset version/hash; seed; image size; epochs; batch;
optimizer; lr0/lrf, momentum, weight decay, warmup, cosine schedule; augmentation;
patience; device; workers; checkpoint period; freeze; AMP и deterministic mode.
`optimizer=auto` и auto-batch не допускаются. Pipeline поддерживает один CPU/MPS/GPU
device на run, без неявного распределённого запуска.

Run ID состоит из UTC timestamp и случайного suffix. `manifest.json` содержит git
commit и dirty-tree flag, fingerprint Python-кода vision/core, полный config и его
hash, dataset/base/baseline hashes, seed, Python/platform/dependency versions,
accelerator metadata, начало/конец, attempts и hashes артефактов. После инициализации
trainer сохраняются также все resolved Ultralytics arguments, включая defaults.
Dirty tree допускается и явно фиксируется; для архивного воспроизведения сохраните
сам checkout вместе с run. Fingerprint не заменяет архив исходников.

Сохраняются `base.pt`, `config.json`, YOLO data view, `training.log`, `console.log`,
`ultralytics/weights/last.pt`, `best.pt`, periodic checkpoints, `results.csv`,
Ultralytics plots/args и manifest. Manifest атомарно обновляется после checkpoint.
Все runs локальные; интеграции с внешними experiment trackers не требуются.

Seed и deterministic mode не обещают побитовую идентичность между разными GPU,
драйверами, платформами или версиями библиотек. Повторяемость проверяется в
зафиксированном окружении; run сохраняет данные для расследования расхождений.

## Resume

`rohai train vision --config CONFIG.json --resume .runtime/training/RUN_ID`

Разрешён только incomplete run с теми же config, dependency environment и code
fingerprint, неизменёнными training inputs и проверенным hash `last.pt`.
Проверяются epoch и optimizer state; Ultralytics не разрешается молча начать новое
обучение вместо resume. Завершённый run нельзя продолжить этим режимом: создайте
новый эксперимент. `best.pt` предназначен для упаковки, не для восстановления.

После аварийного завершения процесса может остаться `.writer.lock`. Он намеренно
не удаляется автоматически. Оператор проверяет, что процесс действительно завершён,
и удаляет только этот lock. Если запись checkpoint была оборвана и hash не совпал,
resume отклоняется; не переписывайте manifest, чтобы скрыть повреждение.
Resume сохраняет состояние Ultralytics, но не гарантирует побитового повторения
непрерывного запуска при многопоточной загрузке и случайных augmentation.

## Ограниченная матрица

`configs/training/experiment-matrix.json`: pretrained baseline, conservative
fine-tune и одна альтернативная конфигурация с изменённым learning rate. Это
план, а не автоматический search. Выбор гиперпараметров опирается на VALIDATION.
Все финальные кандидаты используют один frozen TEST/evaluation protocol.
После просмотра candidate TEST нельзя менять thresholds для его принятия.

Упаковка: `rohai model package --run RUN_DIR --destination PACKAGE_DIR`.
Это создаёт проверяемый candidate ModelPackage, но не включает его в production.

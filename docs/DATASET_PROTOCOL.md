# Dataset Protocol

RohAI использует file-based datasets без обязательной MLOps-платформы. Код:
`traffic_vision.dataset`; JSON Schema: `datasets/schema/*-1.0.json`.
CLI доступен сразу через `python -m traffic_vision.cli`; после обычной установки
проекта также устанавливается команда `rohai` из `project.scripts`.

## Контракт и уровни

DatasetSpec содержит dataset_id, version, created_at с timezone, annotation schema
version, domain class mapping, sources, frames, sequences и split definition.
Пути assets относительны к dataset.json, используют `/`; каждый asset имеет SHA-256.
Абсолютные пути, traversal и выход через symlink за корень отклоняются.

| Уровень | Разметка | Единица отбора |
|---|---|---|
| Detection Dataset | Visible bbox, domain class, ignore/occluded/truncated, exhaustive classes | Разнесённые кадры и отрицательные примеры |
| Tracking Dataset | То же плюс стабильная identity в пределах sequence | Короткие непрерывные клипы, полный заявленный stride |
| Traffic Analytics Dataset | Lane/approach, stop/entry/queue/stop-line/exit events, ground-truth feature samples | Непрерывный контекст для inference; выбранные моменты для оценки |
| Pedestrian Dataset | Waiting/crossing/clearing/unknown и пешеходные события | Переходы состояний и трудные условия |
| Calibration Dataset | Отдельный surveyed geometry asset и independent alignment log | Камера/версия/сеанс проверки, не каждый автомобиль |

`exhaustive_classes` означает: все оцениваемые объекты этих классов на данном
кадре размечены. Отсутствие класса в этом поле не делает неразмеченные объекты
false positives. Настоящий отрицательный кадр имеет exhaustive classes и пустой
objects. Неразмеченный контекст имеет `layers: []`, `provenance: context_only`,
пустые objects/events/traffic/exhaustive_classes. Его обработка нужна трекеру,
но в detection denominator он не входит. Отсутствующий annotation-файл — ошибка,
а не контекст и не отрицательный кадр.

## Метаданные и coverage

SourceVideo содержит camera_id, intersection_id, исходный recording_id, acquisition
datetime с исходным timezone, duration, FPS, resolution, provenance и usage.
Conditions включают day/night/twilight, weather, traffic density, occlusion,
camera angle, lighting quality и optional tilt. Неизвестное значение записывается
как unknown, а не угадывается. Если условия заметно изменяются, создаются клипы
с отдельными source_id, но прежним recording_id.

`dataset stats` считает frames/objects, камеры, дни, условия, классы, layers,
пустые splits и unknown metadata. `configs/datasets/coverage.json` задаёт также
пересечения условий, например pedestrian × night. Минимум одного источника в
этом файле — проверка наличия данных, не достаточный объём исследования и не
accuracy acceptance target. Результат текущей инвентаризации:
`datasets/manifests/coverage_gaps.json`; он не является размеченным release.

## Независимость и splits

TRAIN, VALIDATION, TEST, CHALLENGE назначаются **source video**, никогда кадру.
Все производные клипы/перекодировки сохраняют recording_id оригинала. Планировщик
строит связные группы по recording ID, video hash, camera ID и календарным дням
всего интервала, включая переход через полночь. По умолчанию все три основания
независимости обязательны. Seed определяет порядок целых групп; примерно по 15%
групп отводится validation/test, минимум по одной. CHALLENGE выбирается куратором
по source IDs и переносит всю связанную группу. При нехватке групп команда
отказывает, не делит соседние кадры случайно.

Если независимость по камерам/дням невозможна, её ослабление требует явного
`independence_exceptions` в новой спецификации. Recording grouping остаётся
обязательным. Validator всё равно показывает camera/day overlap как ограничение
независимости. Разбиение выбирается до просмотра результатов TEST.

Защита от leakage включает совпадение recording/hash/day/camera, одинаковые
image bytes и decoded pixels. Совпадения dHash между splits — кандидаты для
ручной проверки, не доказательство совпадения. Детектор не гарантирует нахождение
всех cropped/transcoded/визуально сходных утечек; происхождение источников нужно
проверять. Независимый baseline не подтверждается при таких кандидатах.

Поле `pretrained_exposure` отдельно фиксирует known_overlap, unknown или
audited_no_known_overlap. Известный overlap и demo-источники запрещены в TEST/
CHALLENGE. Unknown разрешает диагностическую оценку, но не открывает training
gate. Для независимого baseline нужны реальные field sources и проверенное
отсутствие известных пересечений с pretraining; описание аудита — в provenance.

## Validation и release

Текущий профиль предполагает constant FPS: timestamp сверяется с frame_index/FPS.
Для VFR нужен timebase adapter либо подготовленная CFR-производная с сохранением
recording_id. Средний FPS нельзя молча выдавать за точные исходные timestamps.
`score_from_seconds` задаётся в абсолютной media time шкале исходного видео.

```powershell
.venv/Scripts/python.exe -m traffic_vision.cli dataset validate datasets/working/road/dataset.json
.venv/Scripts/python.exe -m traffic_vision.cli dataset stats datasets/working/road/dataset.json
.venv/Scripts/python.exe -m traffic_vision.cli dataset split datasets/working/road/dataset.json --challenge hard-video --output split-proposal.json
.venv/Scripts/python.exe -m traffic_vision.cli dataset release datasets/working/road/dataset.json --destination datasets/releases/rohai-road-cameras-v1
```

Split-команда создаёт предложение, не меняет исходный файл. Перед release куратор
вносит assignments в draft и проверяет состав групп. `configs/datasets/rohai-draft.json`
— пустой шаблон: его можно анализировать, но нельзя опубликовать как валидный dataset.

Validator проверяет existence/hash/decoding, размеры/FPS/усечение видео, annotation
schema, bbox, классы, duplicate labels/frames/events/features, track ID collisions
и class changes, sequence bounds/stride/layers, event identity, alignment log,
split assignments и overlap. `--metadata-only` пропускает полное декодирование
видео для быстрой диагностики; release всегда выполняет полную проверку.

Release копирует assets, не создаёт изменяемые hardlinks. `release.json` содержит
полную спецификацию, source list, class mapping, statistics, file hashes и hash
манифеста; `release.sha256` фиксирует байты release.json. Публикация выполняется
через временный каталог и rename. Существующий release не перезаписывается.
Каждый evaluator/accessor проверяет целостность всех assets перед чтением.
Исправления разметки требуют нового version/release. Повторное использование
TEST для выбора модели нужно учитывать как адаптацию к holdout и выделять новый
нетронутый аудитный набор.

Это защита от изменений и ошибочного доступа средствами предоставленного
pipeline, не криптографическая авторизация против владельца файловой системы,
который способен подделать данные и пересчитать все хеши. Для команды с разными
ролями TEST/CHALLENGE следует хранить под отдельными filesystem permissions.

## Training gate

```powershell
.venv/Scripts/python.exe -m traffic_vision.cli dataset export-training datasets/releases/rohai-road-cameras-v1 --split TRAIN --baseline runs/baseline/vision.json --destination datasets/training/road-v1
```

Экспорт разрешает только TRAIN/VALIDATION и требует измеренного независимого
pretrained TEST baseline для **этого же** manifest hash. В копию попадают только
разрешённые image/annotation assets. TEST/CHALLENGE отклоняются независимо от
наличия baseline; output внутри release запрещён. Synthetic fixtures и
prediction logs не открывают gate. Добавленный позднее `rohai train vision` использует
тот же gate; описание pipeline — [MODEL_TRAINING.md](MODEL_TRAINING.md).

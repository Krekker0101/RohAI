# Датасет: источники, разметка, версии

`dataset import` принимает локальные видео и image sequences. Проверяет FPS,
повреждения/пустые источники и размер; смена разрешения требует отдельного source.
Stride и max_sampled_frames ограничивают выборку. Изображения сохраняются в
lossless FFV1 adapter с media_kind=image_sequence и условным FPS=1:
это не измеренное движение и не GT времени ожидания.

ImportSpec/MediaSource определены в `dataset/ingest.py`: dataset ID/version,
class_mapping, source/recording/camera/intersection ID, timezone acquisition time,
conditions, provenance, paths и scene_domain REAL_ROAD/PHYSICAL_MAKET/SYNTHETIC.
Синтетика требует отдельного purpose=synthetic_fixture.
UsageRights: license_id, owner, source_reference, training_allowed,
redistribution_allowed, privacy_reviewed, reviewed_by и evidence.
Training rights/privacy review обязательны перед копированием и field validation.
Локальное обучение не выдаёт автоматически права публикации медиа.
Privacy review не является автоматическим анонимизатором: лица/номера обрабатывают
до регистрации разрешённой версии. Без подтверждений field обучение блокируется.

Исходные paths/hashes и labels lineage встроены JSON в source provenance,
поэтому входят в immutable release hash. Отдельный lineage.json удобен для draft,
но не является единственным доказательством. Seal копирует все manifest assets,
проверяет SHA-256; повторное открытие проверяет manifest и полный inventory.
Изменения требуют нового version/destination; существующие releases не переписываются.

## Метки

Native FrameAnnotation сохраняет bbox/class/track_id/lane/direction/stopped,
queue membership, temporal events и FeatureTruth с unit/method/uncertainty.
YOLO/COCO import и настоящая `dataset propose` YOLO auto-labeling всегда
model_predictions/UNVERIFIED, без exhaustive coverage. Пустой proposal тоже
не является отрицательной истиной. Original labels не перезаписываются.
Эксперт исправляет native JSON и применяет labels review с reviewer и явным
exhaustive class coverage. В draft вручную обновляют annotation Asset path/hash.
Повторная validate выявляет неверные ссылки и неподтверждённые labels.
Reviewer фиксирует атрибуцию; качество просмотра требует второго человеческого
контроля части выборки. Context не считается negative GT.
COCO export сохраняет native sidecar; только он несёт traffic/track truth.
YOLO export возвращает текст в JSON text и sidecar, отвергает ignored regions.

## Разбиение и совместимость

Split по связанным source cohorts: recording/video hash, camera, acquisition day;
импорт дополнительно группирует intersection. Соседние кадры одного recording
не разделяются между train/val/test. Минимум три независимых cohort;
при недостатке данных нет fallback на frame split. Ослабление grouping требует
independence_exceptions и независимого review. Conditions используются для
покрытия/стратификации, не для разрыва recording. Проверяются exact/near duplicates.
dataset split создаёт SplitDefinition JSON, включаемый в draft перед sealing.
TEST/CHALLENGE никогда не экспортируются для обучения.

Добавлены optional metadata и обязательный field rights gate в schema 1.0.
Старый draft мигрируется явно; при несовместимой канонической сериализации
старого sealed JSON нужен новый проверенный release, а не переписывание hash.
Исходная инвентаризация: четыре demo/rendered видео без независимого GT.
Создан fixture: 18 generated rectangle кадров, три source, split 6/6/6.
Он не подтверждает качество дорог или physical toy-object stand.

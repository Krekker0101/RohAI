# Datasets

Локальные media, releases и веса не входят в Git. `schema/` и `manifests/`
содержат версионируемые контракты, инвентаризацию и отчёты покрытия.
TRAIN/VALIDATION/TEST/CHALLENGE разделяются по исходным видео, камерам и дням.
Инвентаризация демонстрационных видео не является размеченным dataset release.

Протокол: [DATASET_PROTOCOL.md](../docs/DATASET_PROTOCOL.md).
Разметка: [ANNOTATION_GUIDE.md](../docs/ANNOTATION_GUIDE.md).
Baseline: [VISION_BASELINE_REPORT.md](../docs/VISION_BASELINE_REPORT.md).

CLI: `.venv/Scripts/python.exe -m traffic_vision.cli --help`.
До независимого pretrained TEST baseline training export и запуск обучения закрыты.
Entrypoint `rohai train vision` и ModelPackage описаны в [MODEL_TRAINING.md](../docs/MODEL_TRAINING.md).

`models/yolo26n.pt` загружается через `scripts/fetch_vision_demo.py --asset model`.
Официальный резервный источник включается флагом `--model-mirror`.
Весам сопутствует JSON с URL, размером и SHA-256. Сам downloader проверяет
опубликованный SHA-256 модели до перемещения файла из `.part` в `.pt`.
Источники и лицензии описаны в [VISION_ENGINE.md](../docs/VISION_ENGINE.md).

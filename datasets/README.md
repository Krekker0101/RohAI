# Datasets

Локальные датасеты и веса не входят в Git. Для оценки Vision Engine хранить здесь
манифесты источников, лицензии и разбиение train/validation/test по видео,
чтобы соседние кадры одного ролика не попадали в разные выборки.

`models/yolo26n.pt` загружается через `scripts/fetch_vision_demo.py --asset model`.
Официальный резервный источник включается флагом `--model-mirror`.
Весам сопутствует JSON с URL, размером и SHA-256. Сам downloader проверяет
опубликованный SHA-256 модели до перемещения файла из `.part` в `.pt`.
Источники и лицензии описаны в [VISION_ENGINE.md](../docs/VISION_ENGINE.md).

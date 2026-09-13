# Recordings

Каталог локальных видео и экспортов benchmark, исключённых из Git.
Пример: `uv run python scripts/compare.py > recordings/comparison.json`.

Vision demo создаёт видео `.mp4`, превью `.jpg`, покадровой журнал `.jsonl`
и итоговый `.report.json`. Последний дополнительный кадр видео показывает all-red.
`vision-backend.report.json` содержит результат проверки настоящего backend pipeline.

Входные `car-detection.mp4` и `person-bicycle-car-detection.mp4` происходят из
[Intel IoT sample-videos](https://github.com/intel-iot-devkit/sample-videos), CC-BY-4.0.
Видео с overlay — обработанные версии этих записей; атрибуцию нужно сохранять при публикации.
`synthetic-demo.mp4` генерируется локально и явно обозначен как SYNTHETIC FIXTURE.

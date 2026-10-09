# RohAI AI 2.0: результат Stage 1

Имя STAGE2_PROGRESS задано пользователем; это vision/data/training этап,
а не следующий RL этап.

Добавлены local image/video import с rights/privacy/lineage, YOLO/COCO interchange,
explicit attributed review, настоящая YOLO auto-proposal команда, synthetic
training purpose/deployment guard, HOTA, small-object и дополнительные strata.
Переиспользованы полноценные existing train/eval/package/registry и uncertainty.

Новые файлы: dataset/ingest.py, labels.py, proposals.py,
scripts/train_vision_fixture.py, evaluate_vision_fixture.py,
tests/test_ai20_dataset.py, пять документов и artifacts/rohai-ai-2.0/.
Изменены dataset schema/splits/validation, CLI, training config/runner/package,
evaluation metrics/vision, JSON schemas и соответствующие contract tests.
Ранее изменённые RohAI 1.0 файлы сохранены и не приписываются этому этапу.

Original pretrained weights остаются baseline. Размеченных независимых field
или human-reviewed physical MAKET release не обнаружено; четыре demo/rendered
видео не объявлены GT. Fixture содержит 18 generated кадров, split 6/6/6.
Отдельные auto-proposals UNVERIFIED. Actual CPU fine-tuning COMPLETED,
best SHA: `c94d42e03ced0a47a05d4ff5b415e8b79f02c81326e836003555b2d44d057bf1`.
Dataset SHA: `25be7ad60956d78ffd33799b1ce2e8cb1f736dfd6fce6c3a5a0a20fc9cdd0186`.
Fixture не имеет подтверждённой field accuracy и не может стать deployment package.

Regression: 487 Python passed, 8 ESP32 HIL skipped; full mypy/Ruff,
dashboard typecheck/lint/build, 12 Vitest, 11 Node overlay, 13 browser E2E.
Финальные proof/status — artifacts/rohai-ai-2.0/verification.json.
Hardware HIL не заменялся симуляцией. RL/аппаратный safety в этом этапе не менялись.

Следующее необходимое действие: разрешённые независимые road/MAKET sessions,
privacy processing, human/second-review bbox/track/traffic truth, frozen split/version
и original baseline, field fine-tuning, общий held-out protocol и downstream
safety error budget, затем registry approval. Toy-object domain требует своих
данных и оценки; перенос между макетом и дорогой без проверки не заявляется.

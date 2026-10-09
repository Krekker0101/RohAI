# Проверки зрения

Отдельный evaluator использует настоящие predictions, не подставляет GT.
Detection: P/R при operating confidence, IoU=.5, 101 recall levels,
mAP50/mAP50–95; отсутствие GT означает undefined, не выдуманный ноль.
Strata: camera/intersection/scene domain/day-night/weather/density/occlusion/
angle/lighting, per-class/confusion matrix. Small-object protocol: original
bbox area <1024 px²; larger GT ignored и larger predictions excluded.
Это заявленная внутренняя size-stratum метрика, не полный официальный COCO evaluator.

Global IDF1 с sequence identity namespaces, switches/fragmentations сохранён.
Добавлен HOTA: normalized global potential alignment, Hungarian frame assignment,
19 IoU thresholds .05–.95, среднее sqrt(DetA×AssA).
Reference: [TrackEval HOTA](https://github.com/JonathonLuiten/TrackEval/blob/master/trackeval/metrics/hota.py).
Analytical tests: perfect HOTA=1; два predicted ID для одного GT в двух кадрах:
HOTA=√.5, IDF1=.5. Без tracking GT метрика не измерена.
Count/queue/wait MAE только с настоящими FeatureTruth unit/method; иначе None.

**Независимого размеченного field release нет.** Дорожные precision/recall/mAP,
IDF1/HOTA и count/queue/wait MAE не измерены. Training val не заменяет TEST.
Fixture original/candidate сравнение: одинаковые 6 held-out TEST кадров,
imgsz=640, dataset/code hash совпадают; fixture-comparison.json.
Оба MEASURED_NON_INDEPENDENT/SYNTHETIC_SMOKE_ONLY, не field accuracy.
Численные значения, включая отсутствие улучшения, находятся в machine reports.

## Производительность

Реальный pretrained model, recorded car-detection.mp4 (12.5 FPS), CPU,
torch_threads=2, 8-second measurement; physical camera=false и GT unavailable.
scripts/benchmark_vision_stage1.py измеряет FPS, inference/analytics/JPEG p50/p95,
capture-to-publish, CPU (100%=один logical CPU), RSS и drops; GPU отсутствует.
Первый recorded-performance.json выполнялся одновременно с regression tests:
inference p50/p95 127.29/139.22 ms, publish p95 307.11 ms, ~7.98 FPS,
38 capture drops, RSS 465379328 bytes. Это нагрузочный, не изолированный замер.
Повтор без параллельных проверок: recorded-performance-isolated.json.
Capture/inference/analytics ~12.50/12.49/12.49 FPS, все drops=0;
inference p50/p95 41.06/45.63 ms, analytics 6.65/7.09 ms,
capture-to-publish 127.13/139.72 ms, JPEG 1.51/1.67 ms,
RSS 467226624 bytes (~445.58 MiB), CPU 751.22% по указанной convention.
Обе модели на fixture TEST имеют recall=0, mAP50=mAP50–95=0;
precision undefined (нет predicted positives). Улучшение не обнаружено.
Исторический RohAI 1.0 proof: artifacts/vision-stage1/recorded-performance.json,
inference p50/p95 35.04/39.84 ms, publish p95 129.31 ms, drops=0.
Короткие замеры этого ноутбука не гарантируют real-camera deadlines.
Fixture checkpoint в действующий stream не внедрялся.

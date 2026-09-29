# Vision Baseline Report — Data & Benchmark Foundation

**Статус независимого baseline: NOT MEASURED — отсутствует размеченный TEST.**
Обучение/fine-tuning не запускалось; numeric acceptance thresholds не назначены.
Наличие работающего detector, unit tests или synthetic benchmark не заменяет
измерение current pretrained model на реальных независимых данных.

## Проверенные доступные материалы

Current model: `datasets/models/yolo26n.pt`, 5 544 453 bytes.
SHA-256: `9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef`.
Семантика/порядок COCO классов и domain mapping задаются
`configs/models/yolo26n.json`; веса сверяются до baseline inference.

В `recordings/` обнаружены демонстрационные исходники и rendered/synthetic videos.
Существующие JSONL имеют поля traffic/tracks/detections: это outputs системы,
не ручная независимая разметка. Полная инвентаризация с hashes, resolutions,
FPS и длительностями: `datasets/manifests/local_asset_inventory.json`.
Проверенного dataset release с TEST в рабочем проекте нет. Пустой dataset draft
и coverage gaps опубликованы отдельно, без фиктивных annotations.

Машинные результаты фактического запуска CLI:

- [Vision baseline: BLOCKED](../datasets/manifests/yolo26n-baseline.json).
- [Downstream baseline: BLOCKED](../datasets/manifests/analytics-baseline.json).
- [Acceptance: WAITING_FOR_INDEPENDENT_BASELINE](../datasets/manifests/acceptance-proposal.json).
- [Пустой draft не проходит validation](../datasets/manifests/draft-validation.json).

| Направление оценки | Реальный TEST baseline |
|---|---|
| Precision, recall, mAP50, mAP50–95 | Недоступен: нет independent detection GT |
| Per-class metrics, confusion matrix | Недоступны по той же причине |
| IDF1, switches, fragmentations | Недоступны: нет reviewed dense identity sequences |
| Vehicle/arrival count error | Недоступны: нет согласованных GT samples/events |
| Queue count MAE, queue length error | Недоступны: нет reviewed queue state/survey |
| Waiting-time/throughput error | Недоступны: нет независимой временной разметки |
| Ошибки признаков контроллера | Не измерены; missing values не заменены нулями |
| Proposed acceptance thresholds | Ожидают baseline и use-case safety budget |

## Реализованный протокол измерения

```powershell
.venv/Scripts/python.exe -m traffic_vision.cli benchmark vision --release datasets/releases/rohai-road-cameras-v1 --split TEST --output runs/baseline/vision.json
.venv/Scripts/python.exe -m traffic_vision.cli benchmark analytics --release datasets/releases/rohai-road-cameras-v1 --predictions runs/baseline/vision.predictions.json --output runs/baseline/analytics.json
.venv/Scripts/python.exe -m traffic_vision.cli benchmark acceptance --vision runs/baseline/vision.json --analytics runs/baseline/analytics.json --output runs/baseline/acceptance.json
```

Vision benchmark использует отдельный detection pass с confidence floor 0.001
и текущий YOLO26n+ByteTrack pass с operating confidence 0.1. Input size 640,
IoU/NMS configuration и max detections берутся из VisionSettings и записываются
в report вместе с versions, seed, device, model hash, code hash, tracker hash,
dataset manifest hash и hash prediction run. Независимость требует field TEST,
audited_no_known_overlap и отсутствия unresolved near-duplicate candidates.
VALIDATION/CHALLENGE и fixtures никогда не открывают TEST baseline gate.

Detection: confidence-ordered one-to-one class-aware matching, IoU 0.50–0.95
с шагом 0.05, 101 recall levels и precision envelope. mAP усредняется по классам
с GT; absent-class AP остаётся null, FP такого класса остаются в operating metrics.
Confusion matrix: true rows/predicted columns плюс background; class-agnostic
IoU association для диагностирования неправильного класса. Ignore labels не
превращаются в FN; partial class annotations не считаются полными negatives.
Это явно описанный bbox protocol; здесь нет COCO crowd/area-bin/maxDet-100
агрегации, поэтому результат нельзя безоговорочно сравнивать с официальной COCO
таблицей. Определение AP проверялось по
[COCO evaluation implementation](https://raw.githubusercontent.com/cocodataset/cocoapi/master/PythonAPI/pycocotools/cocoeval.py).

Tracking: global IDF1 по IoU-qualified associations, namespaces по sequence,
one-to-one assignment; отдельно switches, matched→missed→matched fragmentation,
FP/FN и detection recall. HOTA не реализован и возвращается null с объяснением,
а не переименованным суррогатом. Сверка определения global identity assignment:
[TrackEval Identity](https://raw.githubusercontent.com/JonathonLuiten/TrackEval/master/trackeval/metrics/identity.py).
Неразмеченный context обрабатывается трекером, но не входит в GT denominators.

Результаты разделены по class, camera, day/night, weather, density и occlusion.
Per-class tracking сохраняет отрицательные наблюдения класса, а не выбирает
только кадры, где GT объект присутствует. Неподдерживаемые domain classes,
в частности самостоятельный emergency_vehicle, показываются явно.

## Downstream и acceptance

Analytics benchmark воспроизводит существующие TrafficAnalyzer, health monitor
и адаптер к TrafficState на сохранённых predictions. Calibration age оценивается
в момент записи видео, не по дате запуска offline benchmark. Alignment evidence
берётся из отдельного проверенного asset; ground-truth objects не подаются в
detector/estimator. Если geometry/alignment отсутствуют, физические оценки null,
coverage падает, а ошибка не объявляется нулевой.

По каждому входному признаку контроллера есть отдельная строка: queue count,
traffic_score, mean/oldest wait, arrival rate, counts.* и pedestrian counts/waits/
arrival/crossing. Выдаются GT vs estimate, absolute error, bias, MAE/RMSE,
percentiles и coverage. Неразмеченные признаки помечены NOT_ANNOTATED. Traffic
analytics дополнительно включает visible vehicle count, arrival count, physical
queue length и throughput. По размеченной observability считаются false-reliable
и false-unreliable observations.

Control sensitivity сравнивает green-duration formula для двух traffic scores
подходов фазы; показывает ошибку до и после clipping и observation reliability.
Текущая производная — 1.8 s/score, а не выбранный quality target. Ошибка одного
car-equivalent score может изменить unclipped green на 1.8 s; ошибка oldest wait
на 1 s при коэффициенте 0.05 даёт 0.09 s. Это анализ формулы, не испытание hardware.

После независимых vision **и** downstream measurements команда acceptance
запросит допустимый use-case budget ошибки green duration. Только с явным
`--max-green-error-seconds` и измеренной sensitivity появится предложение
score budget = green budget / seconds_per_score, со сравнением с baseline.
Это PROPOSED_REQUIRES_REVIEW. Pedestrian/missed-queue/visibility требования нужно
согласовать отдельно; высокий mAP не разрешает unsafe observations.
При отсутствии baseline thresholds остаются null.

## Техническая проверка этого этапа

Новый evaluator действительно загрузил текущий pinned YOLO26n и обработал три
кадра технического synthetic fixture через detection и tracking passes.
Статус — MEASURED_NON_INDEPENDENT, integrity prediction run проверена, gate закрыт.
Эти кадры проверяют работу кода; их accuracy намеренно не приводится как road baseline.

Unit/integration tests проверяют известные AP/IoU/confusion случаи, global IDF1
при switch/fragmentation, Hungarian assignment против brute-force, leakage,
missing/corrupt assets, schema drift, immutable release tampering, context frames,
training gate и downstream null/coverage. Итог: **193 Python tests passed**,
Ruff check/format passed, mypy passed (75 source files), `git diff --check` passed.
Остались два прежних deprecation warning Starlette/TestClient. Новых зависимостей
не добавлено. Frontend и обучение на этом этапе не изменялись.

Следующий необходимый вход — реальные видео с metadata и human-reviewed TEST,
включая tracking/analytics subset и независимую калибровку. До этого шага current
pretrained baseline остаётся неизмеренным и **fine-tuning запрещён**.

После этапа Data Foundation добавлен воспроизводимый training pipeline
([MODEL_TRAINING.md](MODEL_TRAINING.md)), но отсутствие измеренного baseline
по-прежнему блокирует его запуск. Новый comparison report:
`datasets/manifests/model-comparison.json`, статус `BLOCKED`; полевых результатов
обучения и кандидата для promotion нет.

# State Estimation Validation

Проверка этапа: **146 Python tests passed**, Ruff passed, mypy passed
(54 source files), `git diff --check` passed. Остались два существующих
deprecation warning Starlette/TestClient. Новые зависимости не добавлялись.

## Воспроизведение

```powershell
.venv/Scripts/python.exe scripts/benchmark_state_estimation.py
.venv/Scripts/pytest.exe -q
.venv/Scripts/ruff.exe check .
.venv/Scripts/mypy.exe
```

Результат: [state_estimation_benchmark.json](state_estimation_benchmark.json).
570 пар estimated/ground_truth: 24 траектории на известной перспективной плоскости
с детерминированным возмущением ground contact ±0.4 px, плюс маршрут с ненулевыми
arrival/departure/throughput и timestamp пересечений. Near/far обозначают две
области синтетической проекции. Это не видеодатасет и не оценка YOLO.

| Метрика | Near MAE | Far MAE | Near p95 | Far p95 |
|---|---:|---:|---:|---:|
| Distance to stop line, m | 0.018669 | 0.009715 | 0.029681 | 0.014977 |
| Queue length, m | 0.014818 | 0.011178 | 0.028175 | 0.014377 |
| Speed, m/s | 0.049581 | 0.031662 | 0.088981 | 0.053390 |

В этом сценарии count, wait, occupancy и rates имеют нулевую ошибку;
timestamp пересечений — ошибку менее 1e-6 s. Это ожидаемо для точно заданной
траектории и не означает нулевую ошибку на видео. Speed имеет положительный bias
около 0.040428/0.024023 m/s: норма шумного перемещения положительна даже на остановке.
Полный JSON содержит signed bias, RMSE, p50/p90/p95/p99 и coverage по каждой группе.

## Benchmark API

Python: `traffic_vision.validation.benchmark(BenchmarkRequest(...))`.
HTTP: `POST /api/v1/vision/benchmark`, до 10 000 samples, существующая operator auth.
Endpoint вычисляет отчёт и ничего не обучает/не сохраняет в контроллер.

```json
{"samples":[{"metric":"queue_length_m","unit":"m","stratum":"far","estimated":11,"ground_truth":10}]}
```

Ответ содержит исходную пару, absolute_error=1, relative_error=0.1,
signed_error=1 и агрегаты. Relative error = abs(error)/abs(truth); при truth=0
она null. Недоступные estimates также null и уменьшают coverage; их нельзя
выбрасывать из знаменателя coverage или подменять нулём. Разные units одного
metric отклоняются. Время/объекты/участки estimated и ground truth должен сопоставить
поставщик выборки: API не делает скрытого временного выравнивания.

`benchmark_identities` отдельно измеряет ID switches, fragmentation, duplicate
arrivals, lost/recovered по размеченным true identities. Runtime reconciliation
не подменяет эту оценку. Для полевой проверки нужны также precision/recall событий,
полнота обнаружения, matched trajectories и интервалы неопределённости по записям.

## Проверки

`tests/test_traffic_state_estimation.py` проверяет perspective, near/far speed,
несколько полос и границы, конечные ориентированные линии, stop/exit timestamps,
первое появление без arrival, large/partial bbox, occlusion/reconciliation,
неоднозначную идентичность, смену разрешения и latch после возврата, independent
holdout, singular H, freshness, camera identity, surveyed support, pedestrian
transitions, benchmark API и передачу метрических значений контроллеру.
`test_vision_pipeline.py` проверяет независимость аналитики от аварии overlay.

Старые тесты намеренно обновлены: ID creation больше не arrival; scalar scale
больше не metres; image-health без road calibration не делает состояние пригодным
для управления; JPEG готовится независимо от состояния. Проверки ожидания,
occlusion, all-red и ограниченных очередей pipeline сохранены.

## Повторный audit F05/F06/F07

| ID | Результат этого этапа | Что ещё не доказано |
|---|---|---|
| F05: перспективная геометрия | Исправлен глобальный масштаб; H, contact projection, surveyed support, holdout RMSE/max, metric lane distance; есть численные benchmark errors | Реальные survey points, объектив, уклон дороги, контакт для каждого класса автомобилей; точность камеры до полевой проверки неизвестна |
| F06: состояние и идентичность | Прибытия из crossing; wait/queue по метрическому движению; bounded reconciliation; отдельные runtime и ground-truth diagnostics | Perfect ReID не обещается; длительные пропуски, lane changes, перекрывающиеся объекты, повторный въезд и пропущенные события требуют размеченных видео |
| F07: уверенность/валидация | Null при недостаточных данных; calibration/alignment gate; метрики ошибки, bias, percentiles, coverage; контроллер не получает pixels | Detection/event confidence и image-health эвристики ещё не откалиброваны как вероятности; нужны независимые полевые confidence/evaluation datasets |

Итог: геометрические ошибки теперь измеримы воспроизводимым benchmark. Реальный
production rollout остаётся закрыт до калибровки камеры, подключения достоверных
alignment observations и прохождения полевого протокола. Обучение YOLO, RL и
новые нейросетевые архитектуры в этот этап не входят.

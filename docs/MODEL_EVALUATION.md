# Model evaluation и pre-registered gates

Рост общего mAP не является достаточным основанием для promotion. Команда
`rohai model evaluate` выполняет inference кандидата, ByteTrack, road geometry,
TrafficAnalyzer и преобразование в реальные controller observations. Она использует
общую реализацию с pretrained benchmark; отдельно переписанного candidate scorer нет.
GT никогда не подаётся detector/tracker как вход.

## Порядок

Сначала нужны `MEASURED` vision и analytics baseline на одном независимом TEST,
один prediction-run hash и неизменённый dataset release. Необходимы аннотации
критических условий, классов, всех входных признаков контроллера, geometry,
alignment evidence и observability. Пропуск обязательной baseline-метрики
блокирует фиксацию policy, а не превращается в успешный gate.

Пример фиксации (значение бюджета определяет safety/use-case review):

```powershell
rohai model freeze-policy --vision BASELINE_VISION.json --analytics BASELINE_ANALYTICS.json --config CONSERVATIVE.json ALTERNATIVE.json --destination POLICY.json --safety-rationale "Documented intersection use-case and control sensitivity review" --max-green-error-seconds APPROVED_BUDGET
```

`APPROVED_BUDGET` — обязательное числовое значение оператора, не готовая рекомендация.
Policy сохраняет baseline snapshots/hashes, dataset hash, evaluation settings/code
fingerprint, разрешённые training config hashes, safety rationale, green-error budget,
UTC freeze time и обязательные gates. Файл не перезаписывается.

```powershell
rohai model evaluate --package PACKAGE_DIR --policy POLICY.json --release RELEASE_DIR --destination NEW_EVALUATION_DIR
```

До открытия candidate TEST создаётся ticket, связывающий package, policy и release.
Evaluation directory нельзя использовать повторно. Изменение evaluation code после
baseline требует нового baseline до candidate TEST. Input size, tracker config,
confidence, device, dependencies и analytics settings должны совпадать; отличаются
только model-specific paths/mapping/hash и директория runtime.

Сохраняются `ticket.json`, `policy.json`, `predictions.json`, `vision.json`,
`analytics.json`, `comparison.json`. Report содержит baseline/candidate/delta,
unit и pass/fail для каждой проверяемой метрики. Failed/blocked evaluation не может
перевести модель в VALIDATED. Ошибка replay оставляет уже созданные доказательства,
но не создаёт успешный comparison.

## Обязательные gates

- Общий mAP50–95; recall и AP50–95 для pedestrian, bus и truck.
- Recall и mAP50–95 для night, heavy occlusion и high-density traffic.
- IDF1, ID switches, fragmentations.
- MAE и coverage каждого controller feature, queue length, throughput, vehicle
  count и arrival count. Значения сопровождаются физическими единицами.
- Совпадение paired GT samples: кандидат не может скрыть неудобные кадры как missing.
- Строгое улучшение MAE хотя бы одной из queue count, queue length или mean wait,
  при сохранении всех остальных обязательных gates.
- Наличие observability-разметки и ноль false-reliable observations.
- Измеренный maximum absolute green-time error в рамках заранее заданного бюджета.

Относительные regression limits в первой версии равны нулю: консервативное правило
«не ухудшать измеренный baseline», а не произвольная цель вроде mAP > 95%.
Код не предлагает статистически обоснованную допустимую деградацию без полевых данных.
Отсутствующая метрика не проходит gate. Рост detection accuracy без downstream
улучшения также не проходит. Полный отчёт сохраняет срезы camera, weather, class,
day/night, density и occlusion; обязательные promotion gates перечислены выше.

Gate policy не редактируется для улучшения уже увиденного TEST результата.
Новая policy означает новый review и новую кампанию, а не повторное принятие старого
кандидата. Локальные hashes фиксируют целостность, но не обеспечивают независимую
нотариальную временную метку и не мешают владельцу файлов подделать весь набор.
Защиту от намеренного переписывания обеспечивает организационное разделение доступа
к holdout/evidence и архивирование frozen policy до оценки кандидатов.

## Что ещё не измерено

`datasets/manifests/model-comparison.json` честно содержит `BLOCKED`, null metrics
и failed gates из-за отсутствия независимого release и кандидата. Это не baseline
модели, не улучшение качества и не разрешение deployment. Пока не получены реальные
измерения, никаких production acceptance claims делать нельзя.
Green-time sensitivity проверяет software observations/formula; она не заменяет
полевую проверку безопасности исполнительного оборудования.

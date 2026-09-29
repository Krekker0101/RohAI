# ActionV1 и SafetySupervisor

Gymnasium action space — `Discrete(9)`. Индекс декодируется как `phase = index // 3`, `duration = [8, 20, 45][index % 3]`.

| Индексы | Requested phase | Requested duration, s |
| --- | --- | --- |
| 0–2 | NS | 8 / 20 / 45 |
| 3–5 | EW | 8 / 20 / 45 |
| 6–8 | pedestrian | 8 / 20 / 45 |

Duration означает общий green budget с начала зелёной фазы. Повторный запрос не обнуляет clock и не добавляет бесконечное продление. Baselines используют тот же `ControlRequest`, но могут задавать промежуточные длительности: дискретизация не ослабляет non-learning сравнение. Формальная JSON schema: `configs/control/action-v1.schema.json`.

Supervisor ограничивает длительность настройками Timing. По умолчанию minimum vehicle green 8 s, maximum green 45 s, pedestrian green минимум 10 s, yellow 3 s, pedestrian clearance 5 s, all-red 2 s. Запрос до minimum green отклоняется; выбранный переход блокируется до завершения clearance. Лампы создаёт существующая safety state machine. Ни один action не задаёт lamp values.

При исчерпании green budget происходит безопасный переход. Дополнительно supervisor принудительно предоставляет давно не обслуженной фазе возможность обслуживания. Порог вычисляется из максимальных green и clearance, а не настраивается по TEST. Фактический gap включает завершение безопасного перехода. При outage или физическом downstream blockage обещать ограниченное время выезда нельзя.

При новом perception fault активная фаза проходит обязательный yellow / pedestrian clearance, затем система остаётся в all-red с fault latch. Автоматическое возобновление по единственному восстановленному кадру отсутствует. Startup без пригодного observation остаётся all-red. Episode report содержит число вмешательств; `RequestOutcome` содержит accepted, reason, requested/effective phase и duration.

Тесты проверяют противоречивые частые запросы, minimum green, yellow, all-red, отсутствие конфликта, ограничение постоянного запроса одной фазы и fault во время green. Эти гарантии относятся к реализованной модели трёх конфликтующих фаз; расширение movement graph требует отдельной проверки safety contract.

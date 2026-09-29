# Declarative scenario catalog

Файлы: `configs/simulation/scenarios/*.json`.
Контракт: `configs/simulation/scenario.schema.json`, `ScenarioDefinition`.
Каждый сценарий фиксирует ID, seed, traffic/perception profiles, duration, step и
expected invariants. Периоды demand должны покрывать duration без gaps/overlap.
Полный каталог: 11 сценариев, seed 42, duration 180 s, step 0,25 s.

| ID | Что проверяет |
|---|---|
| balanced | Симметричный demand 18 vehicle/min на approach |
| NS-heavy | 45/40 vehicle/min N/S против 8/8 E/W |
| EW-heavy | Обратная асимметрия |
| saturated | 120 vehicle/min на каждом approach; backlog и finite storage |
| pedestrian-heavy | 70 pedestrian/min, pedestrian service и clearance |
| burst-traffic | Пачки по 4, случайные интервалы между пачками |
| night-degraded-vision | Больше misses/noise/undercount, FPS 2, latency 0,3 s |
| partial-camera-failure | North camera outage между 1/3 и 3/4 duration |
| camera-outage | В тот же интервал пропадают все камеры |
| queue-spillback | Lanes 26 m, downstream capacity 2; south exit incident |
| rapid-demand-change | В середине сценария NS-heavy меняется на EW-heavy |

Balanced обозначает распределение спроса, а не гарантированную ненасыщенность.
Сценарии не содержат ожидаемого «победителя»: рост delay или худший результат adaptive
может быть корректным результатом evaluation. Invariant PASS не означает acceptable KPI.

Общие invariants: сохранение объектов, конечная storage, отсутствие конфликтующего
service в принятой модели, observation-only policy input, конечные неотрицательные KPI.
Camera-failure tests отдельно проверяют degraded/null features и существующий safety latch.

Запуск одного frozen JSON:

```powershell
python -m traffic_simulator.twin_cli --scenario configs/simulation/scenarios/queue-spillback.json --output .runtime/twin/spillback.json
```

Парный fixed/adaptive прогон всего каталога:

```powershell
python -m traffic_simulator.twin_cli --seed 42 --duration 180 --output .runtime/twin/catalog.json
```

После установки проекта тот же entrypoint доступен как `rohai-sim`; также есть wrapper
`scripts/evaluation/digital_twin.py`. При `--scenario` seed/duration берутся из JSON,
а не CLI defaults. Существующие reports не перезаписываются. Нет флага oracle state,
training или RL. Генератор JSON/schema: `scripts/generate_twin_catalog.py`; уже
существующие файлы требуют явного управления версией, не перезаписываются генератором.

Для regression suite используйте несколько заранее выбранных seeds и фиксированные
scenario snapshots. Одна удачная траектория недостаточна для статистического сравнения
policy. Текущий сохранённый report — engineering validation одного seed, не полевой baseline.

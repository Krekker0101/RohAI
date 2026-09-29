# Digital twin validation

Сохранённый machine-readable report: [digital_twin_validation_v2.json](digital_twin_validation_v2.json).
Выполнены 11 сценариев × 2 существующие policy, seed 42, duration 180 s, step 0,25 s.
Во всех парах совпал arrival-tape SHA256; invariants прошли. Никакого обучения controller
или RL не выполнялось. Установленные SUMO/TraCI/libsumo не обнаружены; integration не запускалась.

Каждый result сохраняет resolved scenario, timing, Python/platform, code SHA256,
arrival/trace hashes, truth KPI, observation errors и coverage. Ошибки измеряются
относительно **текущего** truth, поэтому включают camera/inference delay. Queue length
использует furthest contact point плюс явный class-length prior; единицы — метры.

Примеры измерений для adaptive, не acceptance targets:

| Scenario | Queue count MAE, vehicle | Coverage | Max upstream backlog, entities |
|---|---:|---:|---:|
| balanced | 0,678 | 1,000 | 11 |
| saturated | 1,663 | 1,000 | 1158 |
| pedestrian-heavy | 0,571 | 1,000 | 6 |
| night-degraded-vision | 2,711 | 0,998 | 55 |
| partial-camera-failure | 0,953 | 0,895 | 79 |
| camera-outage | 0,828 | 0,582 | 79 |
| queue-spillback | 0,440 | 1,000 | 591 |

При outage MAE по доступным измерениям может выглядеть меньше, хотя система видит
значительно меньше состояния. Поэтому coverage и unreliable samples сохраняются
рядом с error, а missing не считается нулевой ошибкой. Night/failure сценарии показали
существенное падение departures из-за существующего latched all-red; его нельзя скрывать
как «улучшение безопасности» без отдельного анализа operational consequences.

## Проверки

`tests/test_digital_twin.py` проверяет:

- Одинаковые seed, arrival tape и действия дают одинаковую траекторию.
- Одинаковые arrivals при all-red и NS-green дают разные departures и future queues:
  all-red не выпускает транспорт, green уменьшает total wait. Это physical closed loop.
- Truth/wait history/latent IDs не попадают в controller observation; runtime port
  отвергает `GroundTruthState` и старый ideal simulator snapshot.
- Camera faults, delay, frame drops, low FPS, calibration invalidity, ID switches,
  false positives, misses, undercount и bounded recovery действуют на measurements.
- Multiple lanes, buses, turn routing, blocked exits и finite storage меняют discharge.
- Pedestrian clearance блокирует vehicle service даже при поданном vehicle GREEN.
- Pedestrian-heavy и saturated regression cases сохраняют conservation; pedestrian
  service действительно происходит, saturation создаёт upstream backlog.
- SUMO mock adapter применяет action до simulation step и возвращает noisy observation.
- JSON roundtrip, clock monotonicity, immutable reports и paired-policy runner.

Предыдущие `tests/test_simulation.py` продолжают проверять исходный быстрый слой.

## Ограничения результатов

Это проверяемая очередь/конфликтная модель перекрёстка, а не field-validated digital twin.
Нет непрерывного car-following, lane changing, полноценной turning conflict geometry,
рендеринга видео или параметров, измеренных на реальных камерах. Spillback отражается
в конечных подходах и внешнем backlog, но не в общей городской сети. Delay KPI включает
ожидание upstream; throughput включает только полностью departed vehicles/pedestrians.
`spillback_lane_seconds` и `blocked_service_lane_seconds` могут превышать duration,
поскольку суммируются по полосам. Это разные величины от elapsed seconds.

Video replay не использовался как доказательство control improvement. Прогон даёт
engineering evidence, что action меняет будущий state и observation содержит
контролируемые perception imperfections. Для утверждения реалистичности нужны
калибровка по Vision Benchmark, multi-seed uncertainty analysis и SUMO/field validation.
STOP condition не обходит запрет пользователя: RL и обучение controller не начаты.

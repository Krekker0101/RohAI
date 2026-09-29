# Sim-to-real observation and domain randomization

Текущие noise/dynamics profiles — явно обозначенные engineering assumptions.
Они проверяют устойчивость evaluation pipeline и не являются измеренными параметрами
реальных камер. Независимый Vision Baseline всё ещё отсутствует; цифровой двойник
не снимает этот blocker и не разрешает обучение controller.

## ObservationModel

| Imperfection | Реализация |
|---|---|
| Missed detections | Bernoulli thinning видимых объектов |
| False positives | Вероятность одного дополнительного car на camera-frame |
| ID switches | Новый публичный track ID и сброс measured wait |
| Temporary occlusion | Интервал невидимости объекта; bounded track memory |
| Measurement noise | Gaussian distance noise, wait noise; отрицательные значения обрезаются |
| Queue undercount | Дополнительное thinning vehicle queue membership |
| Camera delay / inference latency | Отдельные значения seconds, суммируются при доставке |
| Frame drops / low FPS | Пропуск camera-frame, cadence и проверка nominal/delivered FPS |
| Camera freeze | Сохранение предыдущего кадра/его timestamp, затем health degradation |
| Camera outage | Нет новых кадров; affected-camera evidence становится unreliable |
| Calibration error | Multiplicative metric-distance bias; invalid calibration fault даёт SUSPECT |

Captured time отличается от delivery/control time. Очередь pending frames не выдаёт
будущие измерения. Уже доставленные данные стареют по своему capture timestamp,
а не по времени последнего вызова `observe()`. Часы не могут двигаться назад.
Latency — модель задержки pipeline; это не отдельная симуляция GPU job scheduling.

Track IDs не равны latent entity IDs. Wait начинается при первом наблюдении, а не
при latent arrival. Краткая occlusion может сохранить ID в пределах TTL; после
истечения TTL старый ID и время ожидания не восстанавливаются идеально.
Entry rate моделирует наблюдаемые crossing events с detection thinning и camera cadence.
Новый ID сам по себе не создаёт entry event. Переход между реальными image coordinates
и homography здесь не выполняется: это surrogate на выходе perception, не image renderer.

Нормальная ошибка detector может оставаться незамеченной при healthy camera. Поэтому
healthy observation не означает нулевую ошибку. Для явных failures контракт RohAI
очищает numeric control features до `null`, а не подставляет нули. Partial outage
портит только affected approach; существующая safety policy при этом может остановить
весь перекрёсток. Pedestrian crossing count не копируется из truth в observation:
суррогат пока оценивает waiting, crossing protection обеспечивается динамикой и safety phases.

## Параметры и их происхождение

`PerceptionProfile` сериализуется внутри `ScenarioDefinition`, с полями provenance,
benchmark report SHA256 и calibration notes. Значение `vision_benchmark_fit` требует
ссылку-хеш на benchmark. Это ссылка на доказательство, а не автоматическое доказательство
качества fit. Нельзя заменять engineering provenance на empirical без калибровочного анализа.

Будущий fit должен разделять camera, night, occlusion и density strata:

1. По независимому baseline получить misses/false positives с корректными exposures,
   ID switches на transitions, occlusion durations и track recovery distribution.
2. Из runtime timestamp logs получить capture FPS, frame drops, latency и freeze/outage
   intervals. Один mAP не определяет эти параметры.
3. Из paired traffic-state benchmark получить queue/wait residuals и calibration bias
   с единицами. Учитывать систематическую ошибку и временную корреляцию.
4. Fit делать на отдельном calibration subset; затем проверять распределения и coverage
   на независимом validation subset, не подгоняя policy по TEST.
5. Сохранить report hash, camera/conditions scope, procedure и uncertainty bounds в profile.

В текущем surrogate class/lane assignment остаются идеальными для видимых объектов;
пространственная корреляция детекторных ошибок и сложная re-identification не реализованы.
Номинальные class lengths используются для physical queue-length estimate и явно
отличаются от полноценной оценки длины по видео. Эти ограничения требуют последующей
валидации на реальном Vision Benchmark, прежде чем делать sim-to-real claims.

## DomainRandomizationProfile

По умолчанию randomization выключена. При включении один deterministic seed выбирает
arrival distribution и множители demand, headway, reaction, noise, latency, occlusion,
а также camera availability. Defaults — узкие sensitivity ranges: demand ±10%, headway
±5%, остальные multipliers ±10%, availability 0,995–1. Это не эмпирические доверительные
интервалы. Wide stress cases задаются отдельно в каталоге и имеют явное назначение.

Randomization RNG отделён от arrival tape и per-frame/entity observation RNG. Одинаковые
условия имеют одинаковые random keys при сравнении policy; разные траектории закономерно
дают разные visibility/track histories. В report сохраняются requested и realized scenario.
Invalid combinations диапазонов не обрезаются молча: повторная schema validation
отклоняет невозможные настройки. Это run-level sensitivity randomization, не обучающий
domain randomization loop и не автоматический поиск controller parameters.

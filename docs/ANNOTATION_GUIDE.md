# Annotation Guide

Цель — независимый ground truth для ошибок дорожных наблюдений, а не максимальное
число вручную обведённых кадров. Annotation schema: `datasets/schema/annotation-1.0.json`.
Версия domain mapping хранится в release; COCO class ID и RohAI class ID не взаимозаменяемы.

## Отбор и трудозатраты

Для detection отбирайте разнесённые по времени кадры, а также пустые дороги,
ночь, осадки, плотные очереди и перекрытия. Сохраняйте исходные video IDs и время.
Не отправляйте соседние кадры в разные splits. Покрытие условий проверяйте через
`dataset stats`, не только по общему числу bbox.

Для tracking выбирайте короткие полные sequences. Допустимы keyframes,
интерполяция и propagation разметочным инструментом с проверкой человеком:
`assisted_human_reviewed` и reviewer attribution. Ручной bbox на каждом кадре
не требуется, но экспорт должен содержать корректную identity/bbox разметку
на каждом оцениваемом кадре заявленного tracking stride. Участки полной
невидимости не требуют придуманного bbox; сохраните ту же истинную identity
после появления, если это подтверждено. При неопределённости исключите данный
интервал из dense identity benchmark отдельными sequences и документируйте причину.

Для traffic analytics запускайте inference на непрерывном контексте, а вручную
проверяйте события и выбранные timestamp. Контекст экспортируется автоматически
с `provenance: context_only`, без фиктивных отрицательных labels. В sequence
задавайте `score_from_seconds` после требуемого warmup. Для tracking layer нужны
полные labels; для analytics layer достаточно явно выбранных feature samples.

## Объекты

Bbox: `x1,y1,x2,y2` в пикселях исходного кадра, положительная площадь и границы
изображения. Размечается видимая часть; для обрезанных объектов указывается
truncated, для перекрытых — occluded. Не восстанавливайте неизвестные углы кузова.
Пример объекта:

```json
{
  "annotation_id":"object-12",
  "bbox":{"x1":120,"y1":180,"x2":200,"y2":260},
  "class_id":0,"domain_class":"car","track_id":"vehicle-7",
  "approach":"north","lane_id":"north-1",
  "queue_member":true,"stopped":true,
  "occluded":false,"truncated":false,"ignore":false
}
```

Class ID берётся из release class mapping. UNKNOWN не является положительным
классом для supervised benchmark: неидентифицируемый объект отмечается как ignore
с проверенной допустимой категорией либо направляется на adjudication; нельзя
произвольно присваивать car. Emergency_vehicle требует отдельного определения
видимых признаков и проверенной разметки. Current pretrained COCO model не имеет
такого самостоятельного класса; evaluator должен показывать этот пробел.

Identity уникальна в пределах sequence, не меняет класс и не используется дважды
на кадре. Это истинная идентичность из ручной проверки, не скопированный ByteTrack ID.
Lane/approach задаются в surveyed road model. Неопределённость принадлежности
полосе оставляется null; нахождение около границы не повод угадывать манёвр.

## События и очереди

Entry — физическое пересечение entry line, не первый кадр ID. Stop — начало
наблюдаемой остановки; joined_queue требует membership до стоп-линии. Crossing
stop line и exit — отдельные события. Укажите timestamp в media seconds,
identity, approach/lane и uncertainty_seconds. Уникальный event_id не дублируется
между annotation-файлами одной sequence.

Queue membership — фактическая принадлежность остановившемуся потоку в queue zone,
а не просто bbox внутри polygon. Длина очереди для сравнения с RohAI — расстояние
по centerline от stop line до наиболее далёкого ground contact среди членов
очереди, максимум по полосам подхода. Это не длина кузова и не сумма bbox heights.
Ground truth длины берётся из независимого survey/контрольного измерения;
не вычисляйте его той же непроверенной H, которую оцениваете.

Wait считается от размеченного начала текущего эпизода до sample timestamp.
Если остановка началась до доступного видео, полное ожидание неизвестно: не
назначайте ноль и не сравнивайте полное время с observed-only оценкой. Counts,
средние, максимумы и traffic_score допустимо вычислять из проверенных membership,
class и time annotations с `method: derived_reviewed`, а не размечать вручную
каждый производный признак.

Traffic feature sample:

```json
{"feature":"queue_count","approach":"north","value":4,"unit":"vehicle","method":"manual_count"}
```

Список и единицы — `FEATURE_UNITS` в executable schema. Arrival count/throughput
относятся к началу той же evaluated sequence; rate — к тому же окну наблюдения
(до 60 s). Начало окна и warmup нужно согласовать до сравнения. Контроллер получает
`counts.*` для queued objects, а vehicle_count — количество видимых автомобилей
подхода; эти величины нельзя смешивать.

## Пешеходы и калибровка

Пешеходные состояния: waiting в waiting zone, crossing внутри перехода, clearing
в exit zone, unknown при недостатке данных. Размечайте переходы и waiting time
на выбранных клипах. Стоящий у края человек не становится crossing автоматически.
`control_observable` заполняется только при отдельной проверке наблюдаемости всех
подходов; без проверки оставляется null, не true.

Calibration Dataset использует `SourceVideo.geometry`: существующий контракт
CameraCalibration/RoadIntersection с независимыми holdout points. Alignment log
— JSON array `AlignmentEvidence`, полученный из независимых неподвижных ориентиров,
а не из GT vehicle tracks. Не создавайте искусственно VALID alignment, чтобы
убрать null из benchmark. См. [CALIBRATION_PROTOCOL.md](CALIBRATION_PROTOCOL.md).

Каждый размеченный field sample должен иметь human/assisted_human_reviewed provenance
и reviewed_by. Необработанные predictions запрещены как ground truth. Ошибки и
спорные labels исправляются в новом release; immutable TEST не редактируется
во время обучения или подбора параметров.

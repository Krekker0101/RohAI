# RohAI AI 2.0 — Stage 1: архитектура зрения

## Аудит

Существующая архитектура — **Ultralytics YOLO26n**, а не собственная сеть RohAI.
Веса: `datasets/models/yolo26n.pt`; pinned контракт: `configs/models/yolo26n.json`.
SHA-256: `9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef`.
Manifest содержит 80 классов COCO и явное отображение в доменные классы;
нецелевые классы получают UNKNOWN. Версия manifest v8.4.0, установленная
библиотека Ultralytics 8.4.150, Python 3.14.5, torch 2.14.0+cpu.
История первоначального скачивания файла не доказана журналом: локальный
hash и имя версии не являются подтверждением внешнего происхождения.

Переиспользованы dataset/evaluation/training/registry, единый CLI, backend,
MAKET UI и VisionPipeline. Параллельный training backend не создавался.
Рабочий путь: camera → bounded latest-frame queues → YOLO26n → ByteTrack →
TrafficAnalyzer → perception health/confidence → Adaptive Controller → safety.
Изменения этого этапа находятся вне рабочего inference/controller/hardware тракта.

## Существующие признаки и неопределённость

ByteTrack ID сбрасываются при смене camera epoch. ROI/lane требуют проверенной
калибровки именно этой камеры. Visible count, line crossing и queue различаются.
Очередь требует подходящей зоны/направления и подтверждённого малого движения:
все автомобили очередью автоматически не становятся. Waiting time означает
наблюдаемое время остановок, не неизвестное время до первого появления в кадре.
Detection/tracking/lane/motion/queue/visibility confidence уже передаются вместе
с признаками; occlusion hold, lost track, stale/frozen/black кадры и неверная
калибровка ограничивают доверие. Queue/count/wait MAE требуют реальных GT.

## Происхождение и выбор модели

Различаются YOLO26n architecture, original pretrained checkpoint и fine-tuned
weights на данных RohAI. Выполненный generated fixture run — тест последнего
механизма, но не подтверждённая дорожная или игрушечная модель.
Ultralytics указывает AGPL-3.0/Enterprise варианты для кода и моделей:
[официальная лицензия](https://www.ultralytics.com/license).
Перед распространением закрытого продукта нужно установить применимый вариант;
права на записи не меняют лицензию исходной модели.

Deploy выбирает original pinned baseline либо проверенный package через registry.
Обязательны hash/class/input/domain compatibility, независимая acceptance evaluation
и явное APPROVED; rollback сохранён. Fixture отвергается seal_package/verify_package
до десериализации checkpoint. Hash обеспечивает целостность, не удостоверение автора;
PyTorch checkpoints загружаются только из доверенного локального run.
Новый RL и изменения аппаратного safety layer в этом этапе не выполнялись.

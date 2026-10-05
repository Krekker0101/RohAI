# Maket setup guide

Для CAMERA_ONLY достаточно ноутбука со встроенной камерой и макета. USB/RTSP и ESP32
не обязательны. Для optional physical output нужны ESP32 DevKit/WROOM-32, vehicle и
pedestrian LEDs, drivers/resistors, 74HC595 и 74HC165 с electrical readback;
см. HARDWARE_WIRING.md. Нельзя подключать дорожный cabinet.
По умолчанию используется текущая YOLO как COCO_BASELINE с неизмеренной toy accuracy.
Отдельная MAKET_MODEL подключается через ModelManifest/package/ModelRegistry со
строгим PHYSICAL_MAKET domain. COCO не переименовывается в Maket release. Learned
ACTIVE требует оценённого Maket model и offline/shadow/stand approval.

## Using RohAI with only a laptop camera

Из repository root в PowerShell (PlatformIO/upload для этого режима не нужны):

```powershell
uv sync --extra vision
.venv/Scripts/python.exe -m traffic_vision.cli maket start
# Optional diagnostics:
.venv/Scripts/python.exe -m traffic_vision.cli maket cameras
.venv/Scripts/python.exe -m traffic_vision.cli maket camera-test --index 0 --backend AUTO
```

Simple Mode по умолчанию: браузер открывается в интерактивном терминале, локальная
сессия подключается без token, единственная камера выбирается автоматически.
Пройдите графическую калибровку и нажмите «НАЧАТЬ». См. [MAKET_SIMPLE_MODE.md](MAKET_SIMPLE_MODE.md).
Для принудительного открытия используйте `maket start --open`, для headless — `--no-open`.

Следующие шаги относятся к **Engineer Mode** (`/maket/engineer`):

1. Откройте http://127.0.0.1:8001/maket/engineer. CLI local session подключает console
   автоматически. Explicit token из environment нужен при отключённых local sessions.
2. Discover cameras: проверяются индексы 0–10 и Windows DSHOW/MSMF. Наличие индекса
   без frame не означает AVAILABLE. Единственная working camera предлагается,
   несколько требуют выбора. Встроенная камера не обязана иметь индекс 0.
3. Preview/Test выбранного кандидата; тип BUILTIN_CAMERA. Friendly name может быть
   unavailable без optional `cv2-enumerate-cameras`; index/backend и frame доступны
   без этого дополнения. Не выдаём USB device name за integrated camera автоматически.
4. CAMERA_ONLY + preset LOW/BALANCED/HIGH, Select + Save. Основной canvas — live
   stream. Crop [x,y,width,height] в normalized raw coordinates, затем rotate и mirror;
   geometry рисуется после этих transforms. ROI — intersection detection_zone.
5. Calibrate все approach/lanes/stop/queue/entry/exit/pedestrian regions на live frame,
   подтвердите fixed pose и Save. Source identity + actual resolution привязывают файл.
6. Дождитесь VISION_READY/CONTROL_READY, Start Camera Only / Shadow. HARDWARE_READY
   остаётся FAIL, ESP32 NOT CONNECTED, hardware commands отсутствуют. Detection/tracking
   и preview работают до этого; невалидная calibration оставляет queue metrics unknown.
7. Перемещайте toy car: если COCO baseline обнаружил её, проверьте bbox/track/queue/wait.
   TOY ACCURACY: NOT VALIDATED. Включите Record video, сохраните session для review.
8. При moved/disabled camera demo остановится. Restart camera → Revalidate camera pose
   при fresh matching alignment или новая calibration → явный Start. Reconnect не Start.
9. Later ESP32: Stop, PHYSICAL_OUTPUT, обнаруженный COM, Save. После flashing/wiring
   нужен отдельный Hardware Test, preflight и local ARM; vision код менять не требуется.

Если CAMERA_NOT_FOUND/OPEN_FAILED: включите Windows Settings → Privacy & security →
Camera → доступ для desktop apps; закройте Teams/Zoom/browser, проверьте Device Manager,
camera toggle/shutter. FRAME_TIMEOUT означает отсутствие frame за deadline.
Permission/busy код используется только при подтверждении driver error, не по догадке.

## Optional ESP32 physical output

Из repository root в PowerShell:

```powershell
uv sync --extra vision --extra maket --extra control-training
uv venv .runtime/pio-venv
uv pip install --python .runtime/pio-venv/Scripts/python.exe platformio==6.1.18
$env:PLATFORMIO_CORE_DIR="$PWD/.runtime/platformio"
.runtime/pio-venv/Scripts/python.exe -m platformio run -d apps/esp32
.runtime/pio-venv/Scripts/python.exe -m platformio run -d apps/esp32 -t upload --upload-port COM7
.venv/Scripts/python.exe -m traffic_vision.cli maket ports
```

Выберите реально обнаруженный COM port вместо COM7. PlatformIO upload физически
прошивает только указанный board; здесь upload не выполнялся. Host GPIO/pin config
находится в apps/esp32/include/stand_config.h. Firmware timing и config.timing
должны совпасть; preflight проверяет это по ACK.

Запустите console без оборудования. Нажмите Discover devices в wizard, выберите
USB index/Serial port или optional RTSP URI. AUTO требует одного Serial кандидата;
identity проверяется после открытия. Save configuration перезапускает runtime
в stopped SHADOW. При смене camera endpoint новый source ID требует калибровки.
Исходники редактировать не требуется. Индекс вне диапазона 0–10 можно ввести явно.
Не помещайте RTSP password в version control. Приложение binds loopback;
operator token можно задать environment для explicit authentication;
CLI использует автоматическую процессную cookie session и не печатает secret.

```powershell
$env:ROHAI_MAKET_OPERATOR_TOKEN='replace-with-a-long-local-secret'
.venv/Scripts/python.exe -m traffic_vision.cli maket validate
.venv/Scripts/python.exe -m traffic_vision.cli maket start
```

Откройте http://127.0.0.1:8001/maket/engineer. Без ESP32 console доступна,
hardware/preflight FAIL; без camera frames live evidence отсутствует. Без compatible model
camera-only preview разрешён для setup, control preflight остаётся FAIL.
Model package/registry задайте в config или wizard; registry должен иметь approved
production entry. Integrity/domain/class mapping проверяются до checkpoint load.

Сначала нажмите Hardware Test arm, удерживая LOCAL_REARM button. Это ручная
проверка wiring без vision claims. NS/EW/pedestrian requests проходят minimum green,
yellow/all-red clearance; кнопка не обязана менять фазу немедленно. Yellow buttons
запрашивают переход к противоположной фазе, а не обход supervision. All Red — E-stop.
Проверьте каждую лампу глазами и electrical masks. Сломанная sense wiring блокирует ARM.

Остановите Hardware Test. Нарисуйте ВСЕ geometry fields по live frame, включая
pedestrian waiting/crosswalk/exit zones и ориентированные entry/stop/exit lines.
Подтвердите pose/resolution/scale и сохраните calibration. Backend автоматически
перезапускает analyzer, оставляя control stopped/SHADOW. Старые demo coordinates являются неподтверждённым
template; используйте их только как структуру, не как calibration своего макета.
Static reference features вне vehicle approaches нужны для camera movement check.

Для AI сначала select LEARNED_AI + SHADOW (если learned artifact имеется); иначе
Fixed/Adaptive. Start требует полного preflight и local rearm. Новая learned policy
не допускается в ACTIVE по одному флагу. Сначала соберите реальные shadow/stand
measurements, review approval и immutable hashes по PHYSICAL_MAKET_MODE.md.
Baseline можно проверить без learned checkpoint; это не доказательство AI advantage.

Сессия автоматически пишет ограниченный журнал; Record video включает raw sampled AVI.
Camera→ACK latency — не optical camera→light latency. При unplug/black/freeze/moved/
stale observations control останавливается. Для восстановления: Stop, Restart camera
и/или Reconnect ESP32, проверить preflight, local rearm, явный Start. SHADOW остаётся
default; reconnect сам не включает AI. После computer sleep повторите этот порядок.

```powershell
.venv/Scripts/python.exe -m traffic_vision.cli maket replay --session .runtime/maket/sessions/SESSION_ID
.venv/Scripts/python.exe scripts/maket_extract_frames.py .runtime/maket/sessions/SESSION_ID --output datasets/maket/review-001 --every 10
```

Replay console port 8002 не создаёт hardware adapter. Review inventory создаёт
UNANNOTATED candidates, не ground truth. Видео sampled; реальные timestamps
в events.jsonl. Перед длительной демонстрацией измерьте drops и recording limits.

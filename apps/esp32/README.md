# ESP32 — hardware boundary

В этом этапе работает Python MockHardwareController; прошивки пока нет.
Для MVP выбран USB Serial: один стенд, питание и канал связи по одному кабелю,
не нужны Wi-Fi provisioning и MQTT broker. Wi-Fi/MQTT — следующий адаптер того же порта.

План протокола: newline-delimited JSON, version, sequence, полный набор ламп,
TTL команды и ACK с тем же sequence. Firmware обязана проверять конфликтующие
комбинации, включать all-red при запуске, просрочке heartbeat или ошибке команды.
Python gateway должен ждать ACK с timeout и сообщать confirmed state отдельно
от commanded state. Повтор команды идемпотентен по sequence; ограничить размер строки.

Перед физическим этапом: схема GPIO и резисторов, тест watchdog при выдёргивании USB,
проверка brownout/reboot и hardware-in-the-loop тест всех переходов. Значения
таймингов и электрическая схема утверждаются для конкретного макета.

# Flutter Dashboard — граница следующего этапа

Flutter UI в этом этапе не реализуется. Цель: Flutter 3.47+ после проверки
доступности SDK, Dart, Material 3, desktop/tablet responsive layout.

План структуры: `lib/core/network`, `lib/features/telemetry/data`,
`lib/features/telemetry/domain`, `lib/features/telemetry/presentation`.
WebSocket repository принимает `Telemetry` версии 1.0; application controller
хранит состояние соединения и snapshot; widgets только отображают состояние.

Контракт: GET `/api/v1/state`, WS `/ws/telemetry`. При reconnect сначала использовать
полный snapshot, контролировать `sequence`; после перезапуска backend sequence
начинается заново. При потере связи показывать «данные устарели», не сохранять
зелёный сигнал как подтверждённое физическое состояние. Сейчас `lamps` — команды
MockHardwareController, а не подтверждения от настоящего устройства.

Экраны: схема перекрёстка, карточки очередей, графики KPI, paired comparison,
панель emergency с TTL. Ни алгоритмов светофора, ни геометрии внутри widgets.

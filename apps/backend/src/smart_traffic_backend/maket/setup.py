"""Bounded non-arming preparation using existing camera and serial adapters."""

import asyncio
import time
from pathlib import Path
from typing import Any

from smart_traffic_backend.maket.calibration import atomic_write
from smart_traffic_backend.maket.discovery import camera_indices, serial_ports, suggested_camera
from smart_traffic_backend.maket.runtime import MaketRuntime
from smart_traffic_backend.maket.serial import Esp32TrafficLightController

ISSUES = {
    "LOCAL_PERMISSION_REQUIRED": (
        "Локальное разрешение отсутствует или истекло.",
        "Нажмите Подключить; если запуску более 8 часов, перезапустите RohAI.",
    ),
    "PHYSICAL_CONFIRMATION_REQUIRED": (
        "Физический запуск не подтверждён.",
        "Проверьте защиту и подтвердите запуск учебного макета.",
    ),
    "SESSION_STORAGE_FULL": (
        "Хранилище записей заполнено.",
        "Откройте записи и удалите только ненужные закрытые сеансы после подтверждения.",
    ),
    "OPERATION_REJECTED": (
        "Операция недоступна в текущем состоянии.",
        "Остановите управление и проверьте диагностику перед повторением.",
    ),
    "CAMERA_NOT_FOUND": (
        "Камера не обнаружена.",
        "Подключите камеру и закройте приложения, использующие её.",
    ),
    "CAMERA_PERMISSION_DENIED": (
        "Доступ к камере запрещён.",
        "Разрешите доступ в параметрах конфиденциальности Windows.",
    ),
    "CAMERA_CHOICE_REQUIRED": (
        "Обнаружено несколько камер.",
        "Выберите нужную камеру в настройке.",
    ),
    "VIDEO_LOST": (
        "Видеопоток потерян.",
        "Проверьте подключение; после восстановления подтвердите положение камеры.",
    ),
    "ESP32_NOT_FOUND": (
        "ESP32 не подключён.",
        "Можно продолжить просмотр камеры или DEMO. Для макета подключите USB.",
    ),
    "ESP32_CHOICE_REQUIRED": (
        "Обнаружено несколько serial-устройств.",
        "Выберите порт устройства в настройках.",
    ),
    "FIRMWARE_INCOMPATIBLE": (
        "Устройство использует несовместимую прошивку.",
        "Установите RohAI firmware 1.0.1 для вашей схемы GPIO.",
    ),
    "ACK_TIMEOUT": (
        "Устройство не подтвердило команду вовремя.",
        "Управление остановлено. Проверьте USB и переподключите устройство.",
    ),
    "CALIBRATION_REQUIRED": (
        "Калибровка требует подтверждения.",
        "Проверьте разметку на живом изображении и сохраните калибровку.",
    ),
    "VISION_NOT_READY": (
        "Распознавание ещё не готово.",
        "Дождитесь загрузки или проверьте установленный профиль модели.",
    ),
    "PHYSICAL_PROTECTION_REQUIRED": (
        "Аппаратная защита не проверена.",
        "Попросите специалиста проверить защиту макета перед физическим запуском.",
    ),
    "SAFE_STATE": (
        "Система переведена в безопасный режим.",
        "Устраните причину, подтвердите проверки и используйте локальную кнопку ARM.",
    ),
    "PHASE_CONFLICT": (
        "Запрошен недопустимый переход фаз.",
        "Управление остановлено; проверьте интервалы и конфигурацию фаз.",
    ),
}


def issue(code: str) -> dict[str, str]:
    message, action = ISSUES.get(
        code,
        ("Операция не выполнена.", "Откройте диагностику и повторите после устранения причины."),
    )
    return {"code": code, "message": message, "action": action}


def diagnostic_issues(runtime: MaketRuntime) -> list[dict[str, str]]:
    result = []
    if not runtime.pipeline.source.connected:
        result.append(
            issue(
                "CAMERA_PERMISSION_DENIED"
                if runtime.camera_error == "CAMERA_PERMISSION_DENIED"
                else "CAMERA_NOT_FOUND"
            )
        )
    if runtime.model_error:
        result.append(issue("VISION_NOT_READY"))
    if not runtime.calibration_check():
        result.append(issue("CALIBRATION_REQUIRED"))
    if runtime.config.output_mode == "PHYSICAL_OUTPUT":
        if not runtime.hardware.connected:
            result.append(issue("ESP32_NOT_FOUND"))
        if not runtime.config.hardware.independent_protection_verified:
            result.append(issue("PHYSICAL_PROTECTION_REQUIRED"))
    if runtime.failure:
        result.append(issue("ACK_TIMEOUT" if "ACK" in runtime.failure else "SAFE_STATE"))
    return result


async def prepare(runtime: MaketRuntime, config_path: Path, state: dict[str, Any]) -> None:
    if runtime.running or runtime.demo_running:
        raise ValueError("Stop control before autosetup")
    state.update(state="RUNNING", step="CAMERA", started_at=time.time(), issues=[])
    if runtime.config.camera_selected:
        deadline = time.monotonic() + 6
        while not runtime.pipeline.source.connected and time.monotonic() < deadline:
            if runtime.camera_error or runtime.pipeline.status().state == "failed":
                break
            await asyncio.sleep(0.1)
    if not runtime.pipeline.source.connected:
        await asyncio.to_thread(runtime.pipeline.stop)
        cameras = await camera_indices(None)
        state["cameras"] = [{k: v for k, v in c.items() if k != "preview"} for c in cameras]
        selected = suggested_camera(cameras)
        if selected is None:
            state["issues"].append(
                issue(
                    "CAMERA_CHOICE_REQUIRED"
                    if any(c.get("available") for c in cameras)
                    else "CAMERA_NOT_FOUND"
                )
            )
        else:
            candidate = next(c for c in cameras if c["index"] == selected)
            camera = runtime.config.camera.model_copy(
                update={
                    "webcam_index": selected,
                    "source": "builtin_camera",
                    "camera_backend": candidate["backend"],
                }
            )
            runtime.config = runtime.config.model_copy(
                update={"camera": camera, "camera_selected": True}
            )
            await asyncio.to_thread(
                atomic_write, config_path, runtime.config.model_dump_json(indent=2).encode()
            )
            await runtime.reconnect_camera()
    state["step"] = "ESP32"
    ports = await asyncio.to_thread(serial_ports)
    state["serial_ports"] = ports
    if runtime.hardware.connected:
        state["device"] = {"verified": True, "connected": True, "ack": runtime.hardware.ack}
    else:
        candidates = [p["port"] for p in ports if p["esp32_candidate"]]
        chosen = (
            runtime.config.hardware.port
            if runtime.config.hardware.port != "AUTO"
            else (candidates[0] if len(candidates) == 1 else None)
        )
        if chosen is None:
            state["issues"].append(
                issue("ESP32_CHOICE_REQUIRED" if candidates else "ESP32_NOT_FOUND")
            )
        else:
            adapter = Esp32TrafficLightController(
                runtime.config.hardware.model_copy(update={"port": chosen})
            )
            try:
                await adapter.connect(read_only=True)
                state["device"] = {
                    "verified": True,
                    "connected": False,
                    "port": chosen,
                    "inspection_only": True,
                    "ack": adapter.ack,
                }
            except (OSError, ValueError, RuntimeError, TimeoutError, ImportError) as error:
                code = (
                    "ACK_TIMEOUT"
                    if isinstance(error, TimeoutError)
                    else (
                        "FIRMWARE_INCOMPATIBLE"
                        if isinstance(error, (ValueError, RuntimeError))
                        else "ESP32_NOT_FOUND"
                    )
                )
                state["issues"].append(issue(code))
            finally:
                await adapter.close()
    state.update(
        state="COMPLETE", step="PREFLIGHT", completed_at=time.time(), physical_control_started=False
    )

"""Explicit application adapter selection. Road actuation remains unavailable."""

from pathlib import Path

from fastapi import FastAPI
from traffic_core.runtime_modes import RuntimeMode


def application_for(mode: RuntimeMode, configuration: Path) -> FastAPI:
    if mode == RuntimeMode.REAL_ROAD:
        raise PermissionError("REAL_ROAD actuation blocked: road commissioning unavailable")
    if mode == RuntimeMode.PHYSICAL_MAKET:
        from smart_traffic_backend.maket.app import create_maket_app
        from smart_traffic_backend.maket.config import MaketConfig

        return create_maket_app(MaketConfig.load(configuration), configuration)
    from smart_traffic_backend.config import Settings
    from smart_traffic_backend.main import create_app

    settings = Settings.model_validate_json(configuration.read_text(encoding="utf-8"))
    if mode == RuntimeMode.SIMULATION and settings.mode != "simulation":
        raise ValueError("SIMULATION requires simulator input configuration")
    if mode == RuntimeMode.VIDEO_REPLAY and (
        settings.mode != "vision" or settings.vision.source != "file"
    ):
        raise ValueError("VIDEO_REPLAY requires explicit recorded-file input")
    return create_app(settings)

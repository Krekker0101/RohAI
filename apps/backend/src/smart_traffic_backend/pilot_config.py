from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class PilotSettings(BaseModel):
    enabled: bool = True
    provider: Literal["local", "ollama"] = "local"
    model_directory: Path = Path("datasets/models/pilot")
    manifest_directory: Path = Path("configs/models/pilot")
    knowledge_path: Path = Path("configs/pilot/knowledge.json")
    ollama_model: str = "qwen2.5:1.5b"
    timeout_seconds: float = Field(default=180, ge=10, le=180)
    cpu_threads: int = Field(default=4, ge=1, le=8)
    requests_per_minute: int = Field(default=20, ge=1, le=60)
    session_ttl_seconds: int = Field(default=1800, ge=60, le=3600)
    max_sessions: int = Field(default=64, ge=1, le=256)

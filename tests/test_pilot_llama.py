import asyncio
import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from smart_traffic_backend.pilot_config import PilotSettings
from smart_traffic_backend.pilot_llama import LlamaProvider, verified_runtime

from scripts import fetch_pilot_models


def test_http_cancellation_closes_connection_without_unloading_the_model() -> None:
    async def run() -> None:
        connected = asyncio.Event()
        disconnected = asyncio.Event()

        async def server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readuntil(b"\r\n\r\n")
            connected.set()
            await reader.read()
            disconnected.set()
            writer.close()
            await writer.wait_closed()

        listener = await asyncio.start_server(server, "127.0.0.1", 0)
        provider = LlamaProvider(PilotSettings())
        provider.port = listener.sockets[0].getsockname()[1]
        task = asyncio.create_task(provider.request("/health"))
        await asyncio.wait_for(connected.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(disconnected.wait(), 2)
        listener.close()
        await listener.wait_closed()

    asyncio.run(run())


def test_a_cancelled_cold_start_waits_for_the_existing_process_to_finish_loading() -> None:
    async def run() -> None:
        provider = LlamaProvider(PilotSettings())
        provider.llama = cast(asyncio.subprocess.Process, SimpleNamespace(returncode=None))
        statuses = iter(("loading", "ok"))

        async def health(path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
            assert path == "/health" and payload is None
            return {"status": next(statuses)}

        mocked: Any = provider
        mocked.request = health
        await provider.start()
        assert provider.ready

    asyncio.run(run())


def test_runtime_verifies_both_archive_and_extracted_executable(tmp_path: Path) -> None:
    manifests = tmp_path / "manifests"
    manifests.mkdir()
    root = tmp_path / "models"
    for identity, name, content in (
        ("qwen3", "model.gguf", b"not executed: fixture model"),
        ("llama-runtime", "runtime.zip", b""),
    ):
        target = root / identity
        target.mkdir(parents=True)
        path = target / name
        if identity == "llama-runtime":
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("llama-server.exe", b"not executed: fixture executable")
                archive.writestr("ggml.dll", b"not loaded: fixture library")
            with zipfile.ZipFile(path) as archive:
                archive.extractall(target)
        else:
            path.write_bytes(content)
        info = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": path.stat().st_size,
        }
        (manifests / f"{identity}.json").write_text(json.dumps({"files": {name: info}}))
    settings = PilotSettings(model_directory=root, manifest_directory=manifests)
    server, model = verified_runtime(settings)
    assert server.name == "llama-server.exe" and model.name == "model.gguf"
    server.write_bytes(b"tampered executable")
    with pytest.raises(ValueError, match="RUNTIME_INTEGRITY"):
        verified_runtime(settings)


def test_installer_rejects_archive_path_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fetch_pilot_models, "ROOT", tmp_path)
    archive_path = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../escape.exe", b"must not be extracted")
    receipt = tmp_path / "manifest.json"
    receipt.write_text(
        json.dumps(
            {
                "directory": ".",
                "files": {
                    "runtime.zip": {"sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest()}
                },
            }
        )
    )
    with pytest.raises(ValueError, match="Unsafe runtime archive path"):
        fetch_pilot_models.extract_runtime(receipt)
    assert not (tmp_path.parent / "escape.exe").exists()

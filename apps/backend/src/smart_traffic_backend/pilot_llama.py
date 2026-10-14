"""Owned, loopback-only llama.cpp process; no cloud or user API key."""

import asyncio
import contextlib
import hashlib
import json
import secrets
import socket
import sys
import zipfile
from pathlib import Path
from typing import Any

from smart_traffic_backend.pilot_config import PilotSettings
from smart_traffic_backend.pilot_provider import LocalProvider

SYSTEM = (
    "You are PILOT, the read-only guide to RohAI road traffic software. "
    "Answer in the requested language using ONLY the supplied facts. "
    "Use two concise sentences. Answer directly. Preserve limitations. "
    "UI labels are not object classes. Demand means waiting vehicles. "
    "Never invent causes, capabilities, accuracy, tests or actions. "
    "If evidence is insufficient, say so. No reasoning trace."
    " Use fluent prose in the requested language; technical identifiers may remain unchanged. "
    "Confidence is a detector score, not measured probability of correctness."
    " The language model generates answer TEXT; Piper synthesizes the speech from that text. "
    "Body clips are prerecorded SILENT animations selected by dialogue state; "
    "the separate mouth follows live PCM amplitude. "
    "SegFormer predicts the road; a separate heuristic refines asphalt and gaps."
)
PROSE_GRAMMAR = (
    r"root ::= [\x09\x0a\x0d\x20-\x7e\u0401\u0410-\u044f\u0451"
    r"\u2000-\u206f\u00b0\u00d7]+"
)


def installed(settings: PilotSettings) -> bool:
    if sys.platform != "win32":
        return False
    root = settings.model_directory
    return (
        (root / "qwen3/Qwen3-4B-Q4_K_M.gguf").is_file()
        and (root / "llama-runtime/llama-server.exe").is_file()
        and any((root / "llama-runtime").glob("llama-*-bin-win-cpu-x64.zip"))
    )


def verified_runtime(settings: PilotSettings) -> tuple[Path, Path]:
    paths: dict[str, Path] = {}
    for identity in ("qwen3", "llama-runtime"):
        manifest = json.loads(
            (settings.manifest_directory / f"{identity}.json").read_text(encoding="utf-8")
        )
        name, info = next(iter(manifest["files"].items()))
        target = (settings.model_directory / identity / name).resolve()
        with target.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if target.stat().st_size != info["size"] or digest != info["sha256"]:
            raise ValueError("PILOT_CHECKPOINT_INTEGRITY")
        paths[identity] = target
    runtime = paths["llama-runtime"].parent
    server: Path | None = None
    with zipfile.ZipFile(paths["llama-runtime"]) as archive:
        for entry in archive.infolist():
            target = (runtime / entry.filename).resolve()
            if not target.is_relative_to(runtime):
                raise ValueError("PILOT_RUNTIME_INTEGRITY")
            if target.suffix.lower() not in (".exe", ".dll"):
                continue
            with archive.open(entry) as source, target.open("rb") as actual:
                expected = hashlib.sha256()
                while block := source.read(1024 * 1024):
                    expected.update(block)
                if expected.digest() != hashlib.file_digest(actual, "sha256").digest():
                    raise ValueError("PILOT_RUNTIME_INTEGRITY")
            if target.name == "llama-server.exe":
                server = target
    if server is None:
        raise ValueError("PILOT_RUNTIME_UNAVAILABLE")
    return server, paths["qwen3"]


class LlamaProvider(LocalProvider):
    model_name = "Qwen3-4B-Q4_K_M"

    def __init__(self, settings: PilotSettings) -> None:
        super().__init__(settings)
        self.llama: asyncio.subprocess.Process | None = None
        self.generation_lock = asyncio.Lock()
        self.port = 0
        self.ready = False
        self.secret = secrets.token_urlsafe(32)

    async def request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port, limit=65536)
        try:
            body = json.dumps(payload, ensure_ascii=False).encode() if payload is not None else b""
            method = "POST" if payload is not None else "GET"
            headers = (
                f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{self.port}\r\n"
                f"Authorization: Bearer {self.secret}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
            )
            writer.write(headers.encode() + body)
            await writer.drain()
            raw_headers = await reader.readuntil(b"\r\n\r\n")
            lines = raw_headers.decode("ascii").split("\r\n")
            status = int(lines[0].split()[1])
            fields = {
                key.lower(): value.strip()
                for line in lines[1:]
                if ":" in line
                for key, value in [line.split(":", 1)]
            }
            length = int(fields.get("content-length", "-1"))
            if not 0 <= length <= 1024 * 1024:
                raise ValueError("PILOT_RUNTIME_RESPONSE")
            response: dict[str, Any] = json.loads(await reader.readexactly(length))
            if status != 200:
                raise ValueError("PILOT_RUNTIME_NOT_READY")
            return response
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    async def start(self) -> None:
        if self.llama is not None and self.llama.returncode is None:
            if not self.ready:
                await self.wait_ready()
            return
        self.ready = False
        executable, model = await asyncio.to_thread(verified_runtime, self.settings)
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            self.port = reservation.getsockname()[1]
        self.llama = await asyncio.create_subprocess_exec(
            str(executable),
            "--model",
            str(model),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--ctx-size",
            "4096",
            "--parallel",
            "1",
            "--threads",
            str(self.settings.cpu_threads),
            "--threads-batch",
            str(self.settings.cpu_threads),
            "--poll",
            "0",
            "--jinja",
            "--chat-template-kwargs",
            '{"enable_thinking":false}',
            "--api-key",
            self.secret,
            "--no-webui",
            "--n-gpu-layers",
            "0",
            cwd=executable.parent,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await self.wait_ready()

    async def wait_ready(self) -> None:
        process = self.llama
        assert process is not None
        for _ in range(240):
            if process.returncode is not None:
                raise ValueError("PILOT_RUNTIME_STOPPED")
            try:
                async with asyncio.timeout(1):
                    if (await self.request("/health")).get("status") == "ok":
                        self.ready = True
                        return
            except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError):
                pass
            await asyncio.sleep(0.25)
        raise ValueError("PILOT_RUNTIME_STARTUP_TIMEOUT")

    async def generate(self, prompt: str) -> str:
        try:
            await asyncio.wait_for(self.generation_lock.acquire(), timeout=2)
        except TimeoutError:
            raise ValueError("PILOT_BUSY") from None
        try:
            async with asyncio.timeout(self.settings.timeout_seconds):
                await self.start()
                data = json.loads(prompt)
                # Keep the fact prefix stable for llama.cpp's prompt cache.
                data = {
                    "language": data["language"],
                    "evidence": data["evidence"],
                    **{
                        key: value
                        for key, value in data.items()
                        if key not in ("language", "evidence")
                    },
                }
                result = await self.request(
                    "/v1/chat/completions",
                    {
                        "messages": [
                            {
                                "role": "system",
                                "content": SYSTEM,
                            },
                            {"role": "user", "content": json.dumps(data, ensure_ascii=False)},
                        ],
                        "temperature": 0,
                        "max_tokens": 160,
                        "stream": False,
                        "grammar": PROSE_GRAMMAR,
                        "chat_template_kwargs": {"enable_thinking": False},
                    },
                )
                answer = result["choices"][0]["message"]["content"]
                if result["choices"][0].get("finish_reason") == "length":
                    raise ValueError("PILOT_GENERATION_TRUNCATED")
                if not isinstance(answer, str) or not answer.strip():
                    raise ValueError("PILOT_EMPTY_RESPONSE")
                return answer.strip()
        finally:
            self.generation_lock.release()

    async def transcribe(self, audio: str, language: str) -> str:
        if self.voice_worker is None:
            self.voice_worker = LocalProvider(self.settings)
        return await self.voice_worker.transcribe(audio, language)

    async def close(self) -> None:
        self.ready = False
        await super().close()
        process, self.llama = self.llama, None
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()

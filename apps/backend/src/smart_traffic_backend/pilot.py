"""Project-only read-only assistant. Public sources, bounded ephemeral sessions."""

import asyncio
import base64
import hashlib
import io
import json
import re
import secrets
import time
import wave
from collections import OrderedDict, deque
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from smart_traffic_backend.config import Settings
from smart_traffic_backend.pilot_knowledge import (
    Fact,
    Knowledge,
    Language,
    Level,
    Page,
    topic_allowed,
)
from smart_traffic_backend.pilot_llama import LlamaProvider
from smart_traffic_backend.pilot_llama import installed as llama_installed
from smart_traffic_backend.pilot_provider import LocalProvider, OllamaProvider, Provider


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=1200)
    conversation_id: UUID | None = None
    page: Page = "overview"
    mode: str = Field(default="demo", pattern="^(demo|real)$")
    level: Level = "auto"
    language: Language = "ru"


class SessionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    conversation_id: UUID


class SpeechRequest(SessionRequest):
    response_id: UUID


@dataclass
class Session:
    owner: str
    touched: float
    last_ids: list[str] = field(default_factory=list)
    answers: OrderedDict[str, tuple[str, str]] = field(default_factory=OrderedDict)
    busy: bool = False
    task: asyncio.Task[Any] | None = None


class PilotService:
    def __init__(self, settings: Settings, provider: Provider | None = None) -> None:
        self.settings = settings
        self.config = settings.pilot
        self.knowledge = Knowledge(self.config.knowledge_path)
        self.provider: Provider = provider or (
            (
                LlamaProvider(self.config)
                if llama_installed(self.config)
                else LocalProvider(self.config)
            )
            if self.config.provider == "local"
            else OllamaProvider(self.config)
        )
        self.sessions: OrderedDict[str, Session] = OrderedDict()
        self.rates: OrderedDict[str, deque[float]] = OrderedDict()

    def authorize(self, request: Request) -> str:
        if not self.config.enabled:
            raise HTTPException(404, "PILOT_DISABLED")
        supplied = request.headers.get("x-operator-token", "")
        if self.settings.operator_token is not None:
            tokens = (self.settings.operator_token, self.settings.operations_viewer_token)
            if not any(
                token is not None
                and secrets.compare_digest(supplied.encode(), token.get_secret_value().encode())
                for token in tokens
            ):
                raise HTTPException(401, "PILOT_AUTH_REQUIRED")
        else:
            if request.client is None or request.client.host not in ("127.0.0.1", "::1"):
                raise HTTPException(403, "PILOT_LOCAL_ONLY")
            origin = request.headers.get("origin")
            if origin is not None and origin not in (
                str(request.base_url).rstrip("/"),
                *self.settings.cors_origins,
            ):
                raise HTTPException(403, "PILOT_ORIGIN_REJECTED")
        owner = hashlib.sha256(
            (supplied + "|" + (request.client.host if request.client else "")).encode()
        ).hexdigest()
        return owner

    def limit(self, owner: str) -> None:
        now = time.monotonic()
        stamps = self.rates.setdefault(owner, deque())
        while stamps and now - stamps[0] > 60:
            stamps.popleft()
        if len(stamps) >= self.config.requests_per_minute:
            raise HTTPException(429, "PILOT_RATE_LIMIT", headers={"Retry-After": "60"})
        stamps.append(now)
        self.rates.move_to_end(owner)
        while len(self.rates) > 256:
            self.rates.popitem(last=False)

    def session(self, identity: UUID | None, owner: str) -> tuple[str, Session]:
        now = time.monotonic()
        for key, value in list(self.sessions.items()):
            if now - value.touched > self.config.session_ttl_seconds and not value.busy:
                del self.sessions[key]
        key = str(identity) if identity else str(uuid4())
        if identity is not None:
            result = self.sessions.get(key)
            if result is None or result.owner != owner:
                raise HTTPException(404, "PILOT_SESSION_NOT_FOUND")
        else:
            if len(self.sessions) >= self.config.max_sessions:
                idle = next((k for k, value in self.sessions.items() if not value.busy), None)
                if idle is None:
                    raise HTTPException(429, "PILOT_BUSY")
                del self.sessions[idle]
            result = Session(owner, now)
            self.sessions[key] = result
        result.touched = now
        self.sessions.move_to_end(key)
        return key, result

    def capabilities(self) -> dict[str, Any]:
        root = self.config.model_directory
        using_llama = isinstance(self.provider, LlamaProvider)
        return {
            "enabled": self.config.enabled,
            "provider": self.config.provider,
            "chat_available": (
                llama_installed(self.config)
                if using_llama
                else (root / "qwen-answer/model.safetensors").is_file()
            )
            if self.config.provider == "local"
            else True,
            "chat_model": ("Qwen3-4B-Q4_K_M" if using_llama else "Qwen2.5-1.5B-Instruct")
            if self.config.provider == "local"
            else self.config.ollama_model,
            "stt_available": (root / "whisper/model.safetensors").is_file(),
            "tts_languages": [
                lang for lang in ("ru", "en") if any((root / f"voice-{lang}").glob("*.onnx"))
            ],
            "knowledge_version": self.knowledge.version,
            "knowledge_records": len(self.knowledge.facts),
            "grounding": "local neural generation grounded in selected public evidence",
            "generation_status": "local" if using_llama else "experimental",
            "audio_saved": False,
            "history_saved": False,
            "read_only": True,
            "languages": ["ru", "en"],
            "tajik_verified": False,
        }

    async def chat(self, body: ChatRequest, owner: str) -> dict[str, Any]:
        key, session = self.session(body.conversation_id, owner)
        if session.busy:
            previous = session.task
            if previous is None or previous is asyncio.current_task():
                raise HTTPException(429, "PILOT_BUSY")
            previous.cancel()
            try:
                await previous
            except asyncio.CancelledError:
                pass
        session.busy = True
        session.task = asyncio.current_task()
        started = time.monotonic()
        facts: list[Fact] = []
        try:
            if re.search(
                r"сколько сейчас|текущие значения|current (number|values)|сколько клиентов",
                body.question,
                re.I,
            ):
                answer = (
                    "Я не получаю текущую телеметрию или закрытые данные. "
                    "У меня пока нет подтверждённой информации об этих значениях."
                    if body.language == "ru"
                    else "I do not receive current telemetry or private data."
                )
                outcome = "unknown"
            elif re.search(
                r"\b(включи|переключи|измени|удали|запусти)\b|turn on|execute", body.question, re.I
            ):
                answer = (
                    "Я только объясняю RohAI и не выполняю команды управления."
                    if body.language == "ru"
                    else "I explain RohAI and do not execute control commands."
                )
                outcome = "out_of_scope"
            elif not topic_allowed(body.question):
                answer = (
                    "Я помогаю разобраться в RohAI. "
                    "Могу рассказать о его возможностях и использовании."
                    if body.language == "ru"
                    else "I help with RohAI. Ask about its features or how to use it."
                )
                facts = []
                outcome = "out_of_scope"
            else:
                candidates = self.knowledge.retrieve(body.question, body.page, session.last_ids)
                if not candidates:
                    answer = (
                        "У меня пока нет подтверждённой информации об этом в проекте."
                        if body.language == "ru"
                        else "I do not have verified project information about that yet."
                    )
                    facts = []
                    outcome = "unknown"
                else:
                    # Retrieval is deterministic; the model spends its CPU budget
                    # on the answer rather than a second neural source-selection pass.
                    facts = candidates[:1]
                    generation_prompt = json.dumps(
                        {
                            "question": body.question,
                            "language": "Russian" if body.language == "ru" else "English",
                            "explanation_level": body.level,
                            "current_page": body.page,
                            "mode": body.mode,
                            "evidence": [
                                {
                                    "title": fact.title,
                                    "status": fact.status,
                                    "content": self.knowledge.generation_content(
                                        fact, body.question, body.language, body.level
                                    ),
                                }
                                for fact in facts
                            ],
                        },
                        ensure_ascii=False,
                    )
                    generated = await self.provider.generate(generation_prompt)
                    try:
                        answer = self.knowledge.validate_generated(
                            generated, facts, body.language, body.question
                        )
                    except ValueError:
                        corrected = json.loads(generation_prompt)
                        corrected["correction"] = (
                            "The previous formulation failed validation. Use the requested "
                            "language and ONLY these facts. Avoid numbers and quoted names "
                            "unless they appear exactly in the facts. Write two short sentences."
                        )
                        generated = await self.provider.generate(
                            json.dumps(corrected, ensure_ascii=False)
                        )
                        answer = self.knowledge.validate_generated(
                            generated, facts, body.language, body.question
                        )
                    outcome = "grounded"
            response_id = str(uuid4())
            session.last_ids = [
                identity for fact in facts for identity in (fact.references or [fact.id])
            ]
            session.answers[response_id] = (answer, body.language)
            while len(session.answers) > 8:
                session.answers.popitem(last=False)
            return {
                "conversation_id": key,
                "response_id": response_id,
                "answer": answer,
                "outcome": outcome,
                "answer_origin": "local-neural-generation" if facts else "scope-policy",
                "emotion": "explaining" if facts else "confused",
                "language": body.language,
                "latency_ms": (time.monotonic() - started) * 1000,
                "knowledge_version": self.knowledge.version,
                "sources": [
                    {
                        "id": fact.id,
                        "title": fact.title,
                        "status": fact.status,
                        "files": [
                            {"path": path, "sha256": self.knowledge.source_hashes[path]}
                            for path in fact.sources
                        ],
                    }
                    for fact in facts
                ],
            }
        finally:
            if session.task is asyncio.current_task():
                session.busy = False
                session.task = None
                session.touched = time.monotonic()

    async def close(self) -> None:
        self.sessions.clear()
        self.rates.clear()
        await self.provider.close()


class ClientDisconnected(Exception):
    """The browser cancelled this request or replaced it with a newer turn."""


async def disconnected[T](request: Request, work: Awaitable[T]) -> T:
    task = asyncio.ensure_future(work)
    try:
        while not task.done():
            if await request.is_disconnected():
                raise ClientDisconnected()
            await asyncio.sleep(0.1)
        return await task
    except asyncio.CancelledError:
        current = asyncio.current_task()
        if current is not None and current.cancelling():
            raise
        raise ClientDisconnected() from None
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def pilot_router() -> APIRouter:
    router = APIRouter(prefix="/api/v1/pilot", tags=["pilot-assistant"])

    def service(request: Request, limited: bool = True) -> tuple[PilotService, str]:
        assistant: PilotService = request.app.state.pilot
        owner = assistant.authorize(request)
        if limited:
            assistant.limit(owner)
        return assistant, owner

    @router.get("/capabilities")
    def capabilities(request: Request) -> dict[str, Any]:
        assistant, _ = service(request, False)
        return assistant.capabilities()

    @router.post(
        "/chat",
        response_model=dict[str, Any],
        responses={
            204: {"description": "Client cancelled the request"},
            503: {"description": "Server stopping or provider unavailable"},
        },
    )
    async def chat(body: ChatRequest, request: Request) -> dict[str, Any] | Response:
        assistant, owner = service(request)
        try:
            return await disconnected(request, assistant.chat(body, owner))
        except ClientDisconnected:
            return Response(status_code=204)
        except asyncio.CancelledError:
            # Uvicorn cancels active requests when its shutdown deadline expires.
            # disconnected() has already cancelled and awaited the inference task.
            # Translate cancellation only at the HTTP boundary, after cleanup.
            return Response(status_code=503, headers={"Retry-After": "1"})
        except (ValueError, OSError, TimeoutError) as exc:
            code = str(exc) if str(exc).startswith("PILOT_") else "PILOT_PROVIDER_UNAVAILABLE"
            raise HTTPException(503, code) from None

    @router.post("/clear")
    def clear(body: SessionRequest, request: Request) -> Response:
        assistant, owner = service(request)
        key, session = assistant.session(body.conversation_id, owner)
        if session.task is not None:
            session.task.cancel()
        del assistant.sessions[key]
        return Response(status_code=204)

    @router.post(
        "/transcribe",
        response_model=dict[str, str],
        responses={
            204: {"description": "Client cancelled the request"},
            503: {"description": "Server stopping or provider unavailable"},
        },
    )
    async def transcribe(request: Request, language: Language = "ru") -> dict[str, str] | Response:
        assistant, _ = service(request)
        audio = bytearray()
        async for chunk in request.stream():
            audio.extend(chunk)
            if len(audio) > 650_044:
                raise HTTPException(413, "PILOT_AUDIO_LIMIT")
        try:
            with wave.open(io.BytesIO(audio)) as source:
                if (
                    source.getnchannels() != 1
                    or source.getsampwidth() != 2
                    or source.getframerate() != 16000
                    or not 0.2 <= source.getnframes() / 16000 <= 20
                ):
                    raise ValueError("PILOT_AUDIO_FORMAT")
        except (ValueError, wave.Error, EOFError):
            raise HTTPException(422, "PILOT_AUDIO_FORMAT") from None
        try:
            text = await disconnected(
                request, assistant.provider.transcribe(base64.b64encode(audio).decode(), language)
            )
            return {"text": text, "language": language, "provider": "local-whisper"}
        except ClientDisconnected:
            return Response(status_code=204)
        except asyncio.CancelledError:
            return Response(status_code=503, headers={"Retry-After": "1"})
        except (ValueError, OSError, TimeoutError):
            raise HTTPException(503, "PILOT_STT_UNAVAILABLE") from None

    @router.post("/speech")
    async def speech(body: SpeechRequest, request: Request) -> StreamingResponse:
        assistant, owner = service(request)
        _, session = assistant.session(body.conversation_id, owner)
        stored = session.answers.get(str(body.response_id))
        if stored is None:
            raise HTTPException(404, "PILOT_RESPONSE_NOT_FOUND")
        text, language = stored

        async def chunks() -> AsyncIterator[bytes]:
            try:
                async for event in assistant.provider.speech(text, language):
                    if await request.is_disconnected():
                        break
                    yield (json.dumps(event) + "\n").encode()
            except (ValueError, OSError, TimeoutError):
                yield b'{"error":"PILOT_TTS_UNAVAILABLE","done":true}\n'

        return StreamingResponse(
            chunks(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    return router

import asyncio
import base64
import io
import json
import wave
from collections.abc import AsyncIterator, MutableMapping
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
from smart_traffic_backend.config import Settings
from smart_traffic_backend.main import create_app
from smart_traffic_backend.pilot import ChatRequest, PilotService
from smart_traffic_backend.pilot_knowledge import Knowledge, topic_allowed
from smart_traffic_backend.pilot_provider import LocalProvider


class SoftwareSelector:
    """Policy test double. Native neural acceptance is a separate explicit run."""

    def __init__(self) -> None:
        self.calls = 0

    async def select(self, prompt: str) -> str:
        self.calls += 1
        passages = json.loads(prompt)["passages"]
        return json.dumps({"ids": [passages[0]["id"]]})

    async def generate(self, prompt: str) -> str:
        if json.loads(prompt)["language"] == "English":
            return (
                "A generated answer based on the project's evidence. "
                "Experimental results are not verified."
            )
        return (
            "Сформулированный моделью ответ по источникам проекта. "
            "Экспериментальные результаты не подтверждены."
        )

    async def transcribe(self, audio: str, language: str) -> str:
        assert base64.b64decode(audio).startswith(b"RIFF")
        return "software speech fixture"

    async def speech(self, text: str, language: str) -> AsyncIterator[dict[str, Any]]:
        yield {"audio": base64.b64encode(b"\0" * 1600).decode(), "sample_rate": 16000}
        yield {"done": True}

    async def close(self) -> None:
        pass


def config(**kwargs: Any) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[call-arg]


@pytest.mark.parametrize(
    "case", json.loads(Path("tests/fixtures/pilot_questions.json").read_text(encoding="utf-8"))
)
def test_policy_corpus_with_explicit_software_selector(case: dict[str, Any]) -> None:
    async def run() -> None:
        provider = SoftwareSelector()
        service = PilotService(config(), provider)
        body = ChatRequest(question=case["question"], page=case["page"], language=case["language"])
        result = await service.chat(body, "owner")
        assert result["outcome"] == case["expected_outcome"]
        if result["outcome"] == "grounded":
            retrieved = service.knowledge.retrieve(body.question, body.page, [])
            assert case["required_evidence"] in [fact.id for fact in retrieved]
            assert result["sources"]
        else:
            assert not provider.calls
            assert not result["sources"]
        await service.close()

    asyncio.run(run())


def test_second_capability_is_resolved_from_server_owned_context() -> None:
    async def run() -> None:
        service = PilotService(config(), SoftwareSelector())
        first = await service.chat(
            ChatRequest(question="Какие основные возможности проекта?"), "owner"
        )
        second = await service.chat(
            ChatRequest(
                question="А как работает вторая?", conversation_id=first["conversation_id"]
            ),
            "owner",
        )
        assert [source["id"] for source in second["sources"]] == ["demo"]
        await service.close()

    asyncio.run(run())


def test_generation_requires_valid_evidence_and_rejects_unsupported_numbers() -> None:
    kb = Knowledge(Path("configs/pilot/knowledge.json"))
    with pytest.raises(ValueError, match="GROUNDING"):
        kb.select_evidence('{"ids":["secret"]}', [kb.facts["video"]])
    facts = kb.select_evidence('{"ids":["video"]}', [kb.facts["video"]])
    with pytest.raises(ValueError, match="UNSUPPORTED"):
        kb.validate_generated("Проверенная точность составляет 100%.", facts, "ru")
    generated = "Вы можете выбрать сохранённую запись для анализа."
    assert kb.validate_generated(generated, facts, "ru") == generated
    assert generated != facts[0].simple
    with pytest.raises(ValueError, match="LANGUAGE"):
        kb.validate_generated(generated, facts, "en")
    with pytest.raises(ValueError, match="LANGUAGE"):
        kb.validate_generated("DEMO использует синтетику, while REAL читает backend.", facts, "ru")
    assert kb.validate_generated("Open «Анализ видео» and choose your recording.", facts, "en")
    with pytest.raises(ValueError, match="UNSUPPORTED"):
        kb.validate_generated("Загрузите видео через `ffmpeg`.", facts, "ru")
    with pytest.raises(ValueError, match="UNSUPPORTED"):
        kb.validate_generated(
            "Двигатель выделяет дорогу, ограничений нет.", [kb.facts["roads"]], "ru"
        )
    with pytest.raises(ValueError, match="UNSUPPORTED"):
        kb.validate_generated(
            "Этот режим позволяет более точно определять маленькие машины.",
            [kb.facts["aerial"]],
            "ru",
        )
    with pytest.raises(ValueError, match="UNSUPPORTED"):
        kb.validate_generated("ИИ показывает реальные светофоры.", [kb.facts["lights"]], "ru")
    assert kb.validate_generated(
        "Проверенного классификатора цветов светофора нет.", [kb.facts["lights"]], "ru"
    )
    assert kb.validate_generated(
        "There is no verified traffic-light classifier in uploaded-video analysis.",
        [kb.facts["lights"]],
        "en",
    )
    assert kb.validate_generated("Проверенной таджикской модели нет.", [kb.facts["pilot"]], "ru")
    with pytest.raises(ValueError, match="UNSUPPORTED"):
        kb.validate_generated(
            "Пилот умеет говорить на таджикском.",
            [kb.facts["pilot"]],
            "ru",
            "Пилот умеет говорить на таджикском?",
        )


@pytest.mark.parametrize(
    ("identity", "question", "answer"),
    [
        (
            "pilot",
            "Пилот умеет говорить на таджикском?",
            "Нет, Пилот не умеет говорить на таджикском.",
        ),
        (
            "lights",
            "ИИ показывает реальные светофоры на видео?",
            "ИИ не может распознать реальные светофоры на видео.",
        ),
        (
            "training",
            "Обучали ли модель на моём видео?",
            "Нет, модель не была обучена на этом видео.",
        ),
    ],
)
def test_verified_negative_answers_are_not_rejected(
    identity: str, question: str, answer: str
) -> None:
    kb = Knowledge(Path("configs/pilot/knowledge.json"))
    assert kb.validate_generated(answer, [kb.facts[identity]], "ru", question) == answer


def test_question_focus_keeps_speed_units_and_privacy_out_of_unrelated_facts() -> None:
    kb = Knowledge(Path("configs/pilot/knowledge.json"))
    facts = kb.retrieve("Скорость машин указана в км/ч?", "video", [])
    assert [fact.id for fact in facts] == ["tracking"]
    assert "без геометрической калибровки" in kb.generation_content(
        facts[0], "Скорость машин указана в км/ч?", "ru", "auto"
    )
    assert [
        fact.id for fact in kb.retrieve("Где хранятся история и аудио Пилота?", "overview", [])
    ] == ["auth"]


def test_project_control_policy_is_not_mistaken_for_politics() -> None:
    assert topic_allowed("Какая политика управления RohAI?")
    assert not topic_allowed("Кто президент Таджикистана?")


def test_auth_rate_limit_session_ownership_and_speech_access() -> None:
    settings = config(
        operator_token=SecretStr("operator-secret-123456"),
        operations_viewer_token=SecretStr("viewer-secret-123456"),
        pilot={"requests_per_minute": 4},
    )
    app = create_app(settings)
    with TestClient(app, client=("127.0.0.1", 123)) as client:
        app.state.pilot.provider = SoftwareSelector()
        assert client.get("/api/v1/pilot/capabilities").status_code == 401
        operator = {"X-Operator-Token": "operator-secret-123456"}
        viewer = {"X-Operator-Token": "viewer-secret-123456"}
        response = client.post(
            "/api/v1/pilot/chat", json={"question": "Как загрузить видео?"}, headers=operator
        )
        assert response.status_code == 200
        data = response.json()
        body = {"conversation_id": data["conversation_id"], "response_id": data["response_id"]}
        assert client.post("/api/v1/pilot/speech", json=body, headers=viewer).status_code == 404
        assert (
            client.post(
                "/api/v1/pilot/speech", json={**body, "text": "arbitrary"}, headers=operator
            ).status_code
            == 422
        )
        assert client.post("/api/v1/pilot/speech", json=body, headers=operator).status_code == 200
        assert (
            client.post(
                "/api/v1/pilot/clear",
                json={"conversation_id": data["conversation_id"]},
                headers=operator,
            ).status_code
            == 204
        )
        assert (
            client.post(
                "/api/v1/pilot/chat",
                json={"question": "test", "conversation_id": data["conversation_id"]},
                headers=operator,
            ).status_code
            == 404
        )
        assert (
            client.post(
                "/api/v1/pilot/chat", json={"question": "test"}, headers=operator
            ).status_code
            == 429
        )
        assert client.get("/api/v1/pilot/capabilities", headers=viewer).json()["read_only"] is True


def test_unauthed_access_is_loopback_and_same_origin_only() -> None:
    app = create_app(config())
    with TestClient(app, client=("127.0.0.1", 1)) as client:
        assert (
            client.get(
                "/api/v1/pilot/capabilities", headers={"Origin": "https://evil.invalid"}
            ).status_code
            == 403
        )
        assert client.get("/api/v1/pilot/capabilities").status_code == 200
    with TestClient(app, client=("192.0.2.1", 1)) as client:
        assert client.get("/api/v1/pilot/capabilities").status_code == 403


def test_invalid_audio_and_oversize_requests_are_rejected() -> None:
    app = create_app(config())
    with TestClient(app, client=("127.0.0.1", 1)) as client:
        app.state.pilot.provider = SoftwareSelector()
        assert client.post("/api/v1/pilot/transcribe", content=b"not wave").status_code == 422
        assert client.post("/api/v1/pilot/transcribe", content=b"x" * 650045).status_code == 413
        output = io.BytesIO()
        with wave.open(output, "wb") as audio:
            audio.setnchannels(1)
            audio.setsampwidth(2)
            audio.setframerate(16000)
            audio.writeframes(b"\0" * 16000)
        assert (
            client.post(
                "/api/v1/pilot/transcribe?language=tg", content=output.getvalue()
            ).status_code
            == 422
        )


def test_expired_and_cross_owner_sessions_are_not_disclosed() -> None:
    service = PilotService(config(pilot={"session_ttl_seconds": 60}), SoftwareSelector())
    identity, session = service.session(None, "one")
    with pytest.raises(HTTPException):
        service.session(uuid4(), "one")
    with pytest.raises(HTTPException):
        service.session(UUID(identity), "two")
    session.touched -= 61
    with pytest.raises(HTTPException):
        service.session(UUID(identity), "one")


def test_native_worker_protocol_keeps_process_for_next_request() -> None:
    # No neural checkpoint required: exercise lifecycle with a tiny local subprocess.
    async def run() -> None:
        provider = LocalProvider(config().pilot)
        provider.process = await asyncio.create_subprocess_exec(
            __import__("sys").executable,
            "-u",
            "-c",
            (
                "import sys,json;"
                '[(print(json.dumps({"text":"ok","done":True}),flush=True)) '
                "for line in sys.stdin]"
            ),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )
        original = provider.process
        assert await provider.select("first") == "ok"
        assert await provider.select("second") == "ok"
        assert provider.process is original and original.returncode is None
        await provider.close()
        assert original.returncode is not None

    asyncio.run(run())


def test_new_question_interrupts_inflight_answer_for_same_session() -> None:
    async def run() -> None:
        started = asyncio.Event()

        class SlowProvider(SoftwareSelector):
            async def generate(self, prompt: str) -> str:
                if not started.is_set():
                    started.set()
                    await asyncio.sleep(3600)
                return await super().generate(prompt)

        service = PilotService(config(), SlowProvider())
        identity, _ = service.session(None, "owner")
        first = asyncio.create_task(
            service.chat(
                ChatRequest(question="How do I upload a video?", conversation_id=UUID(identity)),
                "owner",
            )
        )
        await asyncio.wait_for(started.wait(), timeout=2)
        second = await service.chat(
            ChatRequest(question="What is Fixed-Time?", conversation_id=UUID(identity)), "owner"
        )
        with pytest.raises(asyncio.CancelledError):
            await first
        assert second["outcome"] == "grounded"
        assert [item["id"] for item in second["sources"]] == ["control"]
        await service.close()

    asyncio.run(run())


def test_speech_cancellation_preserves_the_loaded_chat_worker() -> None:
    async def run() -> None:
        class Pipe:
            def write(self, _data: bytes) -> None:
                pass

            async def drain(self) -> None:
                pass

        class Process:
            returncode: int | None = None
            killed = False

            def __init__(self) -> None:
                self.stdin = Pipe()
                self.stdout = asyncio.StreamReader()
                self.stdout.feed_data(b'{"audio":"AAAA","sample_rate":16000}\n')

            def kill(self) -> None:
                self.killed = True

            async def wait(self) -> int:
                self.returncode = 0
                return 0

        provider = LocalProvider(config().pilot)
        chat: Any = Process()
        provider.process = chat
        stream = provider.speech("Speech cancellation fixture", "en")
        assert provider.voice_worker is not None
        voice: Any = Process()
        provider.voice_worker.process = voice
        await anext(stream)
        await stream.aclose()  # type: ignore[attr-defined]
        assert voice.killed
        assert provider.process is chat and not chat.killed
        assert not provider.voice_worker.lock.locked()
        await provider.close()
        assert chat.killed

    asyncio.run(run())


@pytest.mark.parametrize("audio_request", [False, True])
@pytest.mark.parametrize("server_shutdown", [False, True])
def test_request_cancellation_ends_inference_without_asgi_exception(
    audio_request: bool, server_shutdown: bool
) -> None:
    async def run() -> None:
        inbound: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        cancelled = asyncio.Event()
        started = asyncio.Event()

        class BlockingProvider(SoftwareSelector):
            async def wait_for_disconnect(self) -> str:
                started.set()
                if not server_shutdown:
                    inbound.put_nowait({"type": "http.disconnect"})
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
                return ""

            async def select(self, prompt: str) -> str:
                return await self.wait_for_disconnect()

            async def generate(self, prompt: str) -> str:
                return await self.wait_for_disconnect()

            async def transcribe(self, audio: str, language: str) -> str:
                return await self.wait_for_disconnect()

        settings = config()
        app = create_app(settings)
        app.state.pilot = PilotService(settings, BlockingProvider())
        if audio_request:
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                output.writeframes(b"\0" * 16000)
            body = buffer.getvalue()
            path = "/api/v1/pilot/transcribe"
            content_type = b"audio/wav"
        else:
            body = json.dumps({"question": "How do I upload a video?"}).encode()
            path = "/api/v1/pilot/chat"
            content_type = b"application/json"
        inbound.put_nowait({"type": "http.request", "body": body, "more_body": False})
        outbound: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return await inbound.get()

        async def send(message: MutableMapping[str, Any]) -> None:
            outbound.append(dict(message))

        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "client": ("127.0.0.1", 12345),
            "server": ("127.0.0.1", 8000),
            "headers": [(b"host", b"127.0.0.1:8000"), (b"content-type", content_type)],
        }
        request_task = asyncio.create_task(app(scope, receive, send))
        if server_shutdown:
            await asyncio.wait_for(started.wait(), timeout=5)
            request_task.cancel("Task cancelled, timeout graceful shutdown exceeded")
        await asyncio.wait_for(request_task, timeout=5)
        assert cancelled.is_set()
        starts = [message for message in outbound if message["type"] == "http.response.start"]
        assert starts[0]["status"] == (503 if server_shutdown else 204)
        assert all(session.task is None and not session.busy for session in app.state.pilot.sessions.values())
        await app.state.pilot.close()

    asyncio.run(run())

"""Run real local source selection and answer generation on the question corpus."""

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from smart_traffic_backend.config import Settings
from smart_traffic_backend.pilot import ChatRequest, PilotService


async def evaluate(output: Path, retry_failed: bool = False) -> int:
    fixture = Path("tests/fixtures/pilot_questions.json")
    cases = json.loads(await asyncio.to_thread(fixture.read_text, encoding="utf-8"))
    service = PilotService(Settings(_env_file=None))  # type: ignore[call-arg]
    rows: list[dict[str, Any]] = []
    if retry_failed:
        previous = json.loads(await asyncio.to_thread(output.read_text, encoding="utf-8"))
        if (
            previous["knowledge_version"] != service.knowledge.version
            or previous["chat_model"] != service.capabilities()["chat_model"]
        ):
            raise ValueError("Cannot combine evaluations of different knowledge or models")
        rows = previous["results"]
    original_generate = service.provider.generate
    attempts: list[str] = []

    async def capture(prompt: str) -> str:
        text = await original_generate(prompt)
        attempts.append(text)
        return text

    audited: Any = service.provider
    audited.generate = capture
    try:
        for index, case in enumerate(cases):
            old = next((row for row in rows if row["question"] == case["question"]), None)
            if retry_failed and old is not None and old["passed"]:
                continue
            attempts.clear()
            try:
                body = ChatRequest(
                    question=case["question"], page=case["page"], language=case["language"]
                )
                actual = await service.chat(body, "native-corpus")
                sources = [item["id"] for item in actual["sources"]]
                passed = actual["outcome"] == case["expected_outcome"] and (
                    not case["required_evidence"] or case["required_evidence"] in sources
                )
                row = {**case, "passed": passed, "actual": actual}
            except Exception as exc:
                row = {
                    **case,
                    "passed": False,
                    "error": type(exc).__name__ + ": " + str(exc),
                    "generated_attempts": list(attempts),
                }
            if old is not None:
                rows[rows.index(old)] = row
            else:
                rows.append(row)
            status = "PASS" if row["passed"] else "FAIL"
            print(f"{index + 1}/{len(cases)} {status} {case['question']}", flush=True)
            output.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(
                output.write_text,
                json.dumps(
                    {
                        "provider": "actual local neural generation",
                        "chat_model": service.capabilities()["chat_model"],
                        "knowledge_version": service.knowledge.version,
                        "passed": sum(item["passed"] for item in rows),
                        "total": len(cases),
                        "results": rows,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    finally:
        await service.close()
    return 0 if all(item["passed"] for item in rows) else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/pilot-ai/neural-corpus.json")
    )
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(evaluate(args.output, args.retry_failed)))


if __name__ == "__main__":
    main()

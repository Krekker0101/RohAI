"""Single-writer local file store. Content hashes are integrity checks, not signatures."""

import json
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from traffic_vision.dataset.files import canonical_hash


def read_verified(path: Path, key: str = "report_sha256") -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if value.get(key) != canonical_hash({k: v for k, v in value.items() if k != key}):
        raise ValueError(f"Integrity failure: {path}")
    return value


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    data = json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def exclusive(root: Path) -> Iterator[None]:
    root.mkdir(parents=True, exist_ok=True)
    lock = root / ".writer.lock"
    with lock.open("x", encoding="ascii") as stream:
        stream.write(str(os.getpid()))
    try:
        yield
    finally:
        lock.unlink()

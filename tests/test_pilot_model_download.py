import hashlib
import io
import json
from pathlib import Path
from typing import Any
from urllib.error import URLError

import pytest

from scripts import fetch_pilot_models as downloader


class Reply(io.BytesIO):
    status = 206

    def __init__(self, payload: bytes, offset: int, size: int) -> None:
        super().__init__(payload)
        self.headers = {"Content-Range": f"bytes {offset}-{size - 1}/{size}"}


def receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, bytes, Path]:
    monkeypatch.setattr(downloader, "ROOT", tmp_path)
    monkeypatch.setattr(downloader.time, "sleep", lambda _: None)
    payload = b"verified local model fixture" * 20
    manifest = tmp_path / "receipt.json"
    manifest.write_text(
        json.dumps(
            {
                "name": "fixture",
                "directory": "models",
                "files": {
                    "weights.bin": {
                        "url": "https://example.invalid/weights",
                        "size": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                    }
                },
            }
        )
    )
    root = tmp_path / "models"
    root.mkdir()
    return manifest, payload, root


def test_checkpoint_download_resumes_after_a_broken_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest, payload, root = receipt(tmp_path, monkeypatch)
    staging = root / "weights.bin.part"
    staging.write_bytes(payload[:19])
    offsets: list[int] = []

    class BrokenReply(Reply):
        def read(self, size: int = -1) -> bytes:
            if self.tell():
                raise URLError("connection lost")
            return super().read(17)

    def open_reply(request: Any, timeout: int) -> Reply:
        assert timeout == 60
        offset = int(request.get_header("Range")[6:-1])
        offsets.append(offset)
        kind = BrokenReply if len(offsets) == 1 else Reply
        return kind(payload[offset:], offset, len(payload))

    monkeypatch.setattr(downloader.urllib.request, "urlopen", open_reply)
    downloader.fetch(manifest)
    assert offsets == [19, 36]
    assert (root / "weights.bin").read_bytes() == payload
    assert not staging.exists()


@pytest.mark.parametrize("failure", ["range", "checksum"])
def test_invalid_download_is_never_installed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    manifest, payload, root = receipt(tmp_path, monkeypatch)
    (root / "weights.bin.part").write_bytes(payload[:19])
    reply = Reply(payload[19:], 19, len(payload))
    if failure == "range":
        reply.headers["Content-Range"] = f"bytes 0-{len(payload) - 1}/{len(payload)}"
    else:
        reply = Reply(b"x" * (len(payload) - 19), 19, len(payload))
    monkeypatch.setattr(downloader.urllib.request, "urlopen", lambda *_args, **_kwargs: reply)
    with pytest.raises(ValueError, match="range|integrity"):
        downloader.fetch(manifest)
    assert not (root / "weights.bin").exists()

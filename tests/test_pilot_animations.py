import hashlib
import json
import struct
from pathlib import Path

import pytest


def handlers(data: bytes) -> list[bytes]:
    result = []
    offset = 0
    while offset + 8 <= len(data):
        size, kind = struct.unpack_from(">I4s", data, offset)
        header = 8
        if size == 1:
            size = struct.unpack_from(">Q", data, offset + 8)[0]
            header = 16
        if size < header or offset + size > len(data):
            raise ValueError("Invalid MP4 box")
        payload = data[offset + header : offset + size]
        if kind in (b"moov", b"trak", b"mdia"):
            result.extend(handlers(payload))
        elif kind == b"hdlr":
            result.append(payload[8:12])
        offset += size
    return result


@pytest.mark.parametrize("name", ["listening", "thinking", "explaining"])
def test_body_assets_have_only_video_and_valid_per_frame_mouth_geometry(name: str) -> None:
    root = Path("apps/dashboard/public/pilot")
    clips = json.loads((root / "animations.json").read_text(encoding="utf-8"))
    clip = clips[name]
    data = (root / clip["src"]).read_bytes()
    assert handlers(data) == [b"vide"]
    assert clip["audio_tracks"] == 0
    assert hashlib.sha256(data).hexdigest() == clip["sha256"]
    assert len(clip["mouth"]) == 190
    for mouth in clip["mouth"]:
        assert 0.2 < mouth["x"] < 0.8
        assert 0.2 < mouth["y"] < 0.5
        assert 0.03 < mouth["width"] < 0.2
        assert -45 < mouth["rotation"] < 45


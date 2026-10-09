"""Bounded read-only replay and explicitly confirmed deletion of one owned closed session."""

import json
import re
import time
from pathlib import Path
from typing import Any


def session_path(root: Path, session_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{32}", session_id):
        raise ValueError("Invalid session identifier")
    path = root / session_id
    if path.is_symlink() or path.resolve().parent != root.resolve() or not path.is_dir():
        raise ValueError("Session unavailable")
    return path


def asset(path: Path, name: str) -> Path:
    if name not in {"session.json", "events.jsonl", "video.avi"}:
        raise ValueError("Unsupported recorded asset")
    target = path / name
    if target.is_symlink() or target.resolve().parent != path.resolve() or not target.is_file():
        raise ValueError("Recorded asset unavailable")
    return target


def list_sessions(root: Path, days: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    if not root.exists():
        return result
    for candidate in sorted(root.iterdir(), key=lambda p: p.name):
        try:
            directory = session_path(root, candidate.name)
            manifest = json.loads(asset(directory, "session.json").read_text(encoding="utf-8"))
            result.append(
                {
                    "id": candidate.name,
                    "mode": manifest.get("mode"),
                    "created_at": manifest.get("created_at"),
                    "closed": "closed_at" in manifest,
                    "complete": manifest.get("complete", False),
                    "expired": time.time() - float(manifest["created_at"]) > days * 86400,
                    "video": (directory / "video.avi").is_file(),
                    "dropped_events": manifest.get("dropped_events"),
                    "physical_commands_replayed": False,
                }
            )
        except (ValueError, OSError, KeyError, TypeError):
            continue
    return sorted(result, key=lambda value: float(value["created_at"]), reverse=True)[:100]


def timeline(root: Path, session_id: str, offset: int = 0, limit: int = 200) -> dict[str, Any]:
    if offset < 0 or offset > 1_000_000 or not 1 <= limit <= 500:
        raise ValueError("Replay pagination out of range")
    directory = session_path(root, session_id)
    manifest = json.loads(asset(directory, "session.json").read_text(encoding="utf-8"))
    result: list[dict[str, Any]] = []
    base = None
    last_sequence = 0
    elapsed = 0.0
    more = False
    with asset(directory, "events.jsonl").open(encoding="utf-8") as stream:
        for index in range(offset + limit + 1):
            line = stream.readline(1_000_001)
            if not line:
                break
            if len(line) > 1_000_000:
                raise ValueError("Oversized recorded event")
            event = json.loads(line)
            sequence = event.get("sequence", index + 1)
            if not isinstance(sequence, int) or sequence <= last_sequence:
                raise ValueError("Invalid causal event sequence")
            stamp = float(event.get("monotonic", event["timestamp"]))
            if base is None:
                base = stamp
            elapsed = max(elapsed, (stamp - base) * 1000)
            gap = sequence - last_sequence - 1
            last_sequence = sequence
            if index >= offset:
                if len(result) == limit:
                    more = True
                    break
                result.append(
                    {
                        **event,
                        "sequence": sequence,
                        "offset_ms": elapsed,
                        "missing_before": gap,
                        "timing_kind": "MONOTONIC" if "monotonic" in event else "LEGACY_WALL_CLOCK",
                    }
                )
    return {
        "mode": "REPLAY",
        "physical_execution": False,
        "manifest": manifest,
        "events": result,
        "next_offset": offset + len(result) if more else None,
        "complete": manifest.get("complete", False),
    }


def delete_closed_session(root: Path, session_id: str, *, confirmed: bool, active_id: str) -> None:
    if not confirmed or session_id == active_id:
        raise ValueError("Confirm a closed historical session; active recording cannot be deleted")
    directory = session_path(root, session_id)
    manifest = json.loads(asset(directory, "session.json").read_text(encoding="utf-8"))
    if "closed_at" not in manifest:
        raise ValueError("Session is not closed; preserve incomplete evidence")
    if any(
        p.name not in {"session.json", "events.jsonl", "video.avi"} or p.is_symlink()
        for p in directory.iterdir()
    ):
        raise ValueError("Unexpected recording files; automatic cleanup refused")
    # Only this user-selected UUID, flat known files; no recursive or wildcard deletion.
    for name in ("video.avi", "events.jsonl", "session.json"):
        target = directory / name
        if target.exists():
            asset(directory, name).unlink()
    directory.rmdir()

"""Restore a checked observation backup to a NEW database; never overwrites active data."""

import argparse
import sqlite3
from pathlib import Path


def restore(source: Path, destination: Path) -> None:
    if not source.is_file() or source.is_symlink():
        raise ValueError("Source must be an existing regular backup file")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(
            "Restore destination must not exist; stop the service before switching"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as stream:
        if stream.read(16) != b"SQLite format 3\x00":
            raise ValueError("Invalid SQLite backup header")
    original = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    target: sqlite3.Connection | None = None
    try:
        if original.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("Backup integrity failure")
        columns = {row[1] for row in original.execute("PRAGMA table_info(samples)")}
        if columns != {
            "session",
            "sequence",
            "measured_at",
            "domain",
            "queue",
            "wait",
            "fps",
            "latency",
            "drops",
            "ready",
            "reason",
        }:
            raise ValueError("Unsupported observation backup schema")
        if original.execute("SELECT COUNT(*) FROM site").fetchone() != (1,):
            raise ValueError("Missing or ambiguous site identity")
        with destination.open("xb"):
            pass
        target = sqlite3.connect(destination)
        original.backup(target)
        if target.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("Restored database failed integrity check")
    except Exception:
        if target is not None:
            target.close()
            target = None
            destination.unlink(missing_ok=True)
        raise
    finally:
        original.close()
        if target is not None:
            target.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    restore(args.source, args.destination)
    print("Restore verified. Configure STA_OPERATIONS_DATABASE after stopping the service.")


if __name__ == "__main__":
    main()

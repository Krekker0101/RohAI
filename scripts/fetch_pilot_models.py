"""Fetch pinned public checkpoints; no provider keys, pickle or runtime downloads."""

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fetch(receipt: Path) -> None:
    data = json.loads(receipt.read_text(encoding="utf-8"))
    target = ROOT / data["directory"]
    target.mkdir(parents=True, exist_ok=True)
    for name, info in data["files"].items():
        if Path(name).name != name:
            raise ValueError("Invalid checkpoint filename")
        path = target / name
        verified = False
        if path.is_file():
            with path.open("rb") as existing:
                verified = hashlib.file_digest(existing, "sha256").hexdigest() == info["sha256"]
        if verified:
            print(f"Verified {path.name}", flush=True)
            continue
        temporary = path.with_suffix(path.suffix + ".part")
        for attempt in range(4):
            count = temporary.stat().st_size if temporary.is_file() else 0
            if count > info["size"]:
                raise ValueError("Partial checkpoint size exceeded")
            if count == info["size"]:
                break
            headers = {"User-Agent": "RohAI-Pilot-model-fetch/2"}
            if count:
                headers["Range"] = f"bytes={count}-"
            request = urllib.request.Request(info["url"], headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    if response.status == 206:
                        expected = f"bytes {count}-{info['size'] - 1}/{info['size']}"
                        if response.headers.get("Content-Range") != expected:
                            raise ValueError("Checkpoint range mismatch")
                    elif response.status == 200:
                        count = 0  # Server declined Range; consume the complete response.
                    else:
                        raise ValueError("Unexpected checkpoint HTTP response")
                    with temporary.open("ab" if count else "wb") as output:
                        while chunk := response.read(64 * 1024):
                            count += len(chunk)
                            if count > info["size"]:
                                raise ValueError("Checkpoint size exceeded")
                            output.write(chunk)
                if count == info["size"]:
                    break
                raise OSError("Checkpoint transfer ended early")
            except (OSError, urllib.error.URLError):
                if attempt == 3:
                    raise
                print(f"Transfer interrupted; resuming {name}", flush=True)
                time.sleep(2**attempt)
        with temporary.open("rb") as downloaded:
            digest = hashlib.file_digest(downloaded, "sha256").hexdigest()
        if temporary.stat().st_size != info["size"] or digest != info["sha256"]:
            raise ValueError(f"Checkpoint integrity failed: {name}")
        temporary.replace(path)
        print(f"Installed {data['name']}: {name} ({count} bytes)", flush=True)


def extract_runtime(receipt: Path) -> None:
    data = json.loads(receipt.read_text(encoding="utf-8"))
    target = (ROOT / data["directory"]).resolve()
    name, info = next(iter(data["files"].items()))
    archive_path = target / name
    with archive_path.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != info["sha256"]:
            raise ValueError("Runtime archive integrity failed")
    with zipfile.ZipFile(archive_path) as archive:
        for entry in archive.infolist():
            if not (target / entry.filename).resolve().is_relative_to(target):
                raise ValueError("Unsafe runtime archive path")
        archive.extractall(target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    receipts = (
        [args.manifest]
        if args.manifest
        else [
            path
            for path in sorted((ROOT / "configs/models/pilot").glob("*.json"))
            if path.stem not in ("qwen", "qwen-answer")
        ]
    )
    for receipt in receipts:
        fetch(receipt)
        if receipt.stem == "llama-runtime":
            extract_runtime(receipt)


if __name__ == "__main__":
    main()

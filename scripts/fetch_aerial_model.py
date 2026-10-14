"""Install official YOLO26s weights for the drone profile, with pinned SHA-256."""

import hashlib
import json
import urllib.request
from pathlib import Path

SHA256 = "646f8bc3fe0a656803d95c294f7852321748cb29d13466a1af8862e2db384a1b"
URLS = (
    "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26s.pt",
    "https://huggingface.co/Ultralytics/YOLO26/resolve/main/yolo26s.pt",
)


def fetch(target: Path = Path("datasets/models/yolo26s.pt")) -> None:
    if target.exists():
        with target.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != SHA256:
                raise ValueError("Existing aerial model SHA-256 mismatch; model not loaded")
        print("Official YOLO26s already installed and verified")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".part")
    for url in URLS:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "RohAI/1.0"})
            with (
                urllib.request.urlopen(request, timeout=60) as response,
                temporary.open("wb") as out,
            ):
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > 32 * 1024 * 1024:
                        raise ValueError("Unexpected model size")
                    out.write(chunk)
            with temporary.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != SHA256:
                    raise ValueError("Downloaded model SHA-256 mismatch; model not loaded")
            temporary.replace(target)
            target.with_suffix(".receipt.json").write_text(
                json.dumps({"url": url, "sha256": SHA256, "bytes": size}, indent=2),
                encoding="utf-8",
            )
            print("Official YOLO26s installed and verified")
            return
        except OSError:
            continue
    raise RuntimeError("Could not download official model; rerun scripts/fetch_aerial_model.py")


if __name__ == "__main__":
    fetch()

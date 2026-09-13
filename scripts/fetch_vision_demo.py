"""Fetch official YOLO26n weights and a recorded Intel sample video, with resumable IO."""

import argparse
import hashlib
import json
import logging
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

ASSETS = {
    "model": (
        "https://github.com/ultralytics/assets/releases/download/v8.4.0/yolo26n.pt",
        Path("datasets/models/yolo26n.pt"),
    ),
    "video": (
        "https://raw.githubusercontent.com/intel-iot-devkit/sample-videos/master/car-detection.mp4",
        Path("recordings/car-detection.mp4"),
    ),
    "traffic": (
        "https://raw.githubusercontent.com/intel-iot-devkit/sample-videos/master/person-bicycle-car-detection.mp4",
        Path("recordings/person-bicycle-car-detection.mp4"),
    ),
}
logger = logging.getLogger(__name__)
MODEL_SHA256 = "9b09cc8bf347f0fc8a5f7657480587f25db09b34bf33b0652110fb03a8ad4fef"
MODEL_MIRROR = "https://huggingface.co/Ultralytics/YOLO26/resolve/main/yolo26n.pt"


def fetch(asset: str, model_mirror: bool = False) -> None:
    url, target = ASSETS[asset]
    if asset == "model" and model_mirror:
        url = MODEL_MIRROR
    target.parent.mkdir(parents=True, exist_ok=True)
    receipt = target.with_suffix(target.suffix + ".json")
    if target.exists() and receipt.exists():
        expected = (
            MODEL_SHA256
            if asset == "model"
            else json.loads(receipt.read_text(encoding="utf-8"))["sha256"]
        )
        with target.open("rb") as cached:
            digest = hashlib.file_digest(cached, "sha256").hexdigest()
        if digest == expected:
            logger.info("Verified cached %s", target)
            return
        raise ValueError(f"Cached asset hash mismatch: {target}")
    partial = target.with_suffix(target.suffix + ".part")
    error: Exception | None = None
    for attempt in range(5):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "SmartTrafficAI/0.2", "Range": f"bytes={offset}-"}
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=60
            ) as response:
                if response.status == 206:
                    content_range = response.headers.get("Content-Range", "")
                    if not content_range.startswith(f"bytes {offset}-"):
                        raise OSError("Server returned an inconsistent byte range")
                elif response.status == 200:
                    offset = 0
                else:
                    raise OSError(f"Unexpected response: {response.status}")
                expected_bytes = response.headers.get("Content-Length")
                received = 0
                with partial.open("ab" if offset else "wb") as output:
                    while chunk := response.read(16384):
                        output.write(chunk)
                        received += len(chunk)
                if expected_bytes is not None and received != int(expected_bytes):
                    raise OSError("Incomplete download")
            with partial.open("rb") as downloaded:
                digest = hashlib.file_digest(downloaded, "sha256").hexdigest()
            if asset == "model" and digest != MODEL_SHA256:
                raise RuntimeError("Model SHA-256 mismatch; do not load this file")
            partial.replace(target)
            receipt.write_text(
                json.dumps(
                    {"url": url, "sha256": digest, "bytes": target.stat().st_size}, indent=2
                ),
                encoding="utf-8",
            )
            logger.info("Downloaded %s (SHA-256 %s)", target, digest)
            return
        except (OSError, ValueError) as exc:
            error = exc
            logger.warning("Retry %s for %s: %s", attempt + 1, asset, type(exc).__name__)
            time.sleep(min(attempt + 1, 5))
    raise RuntimeError(f"Could not download {asset}; rerun to resume") from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--asset", choices=("all", "model", "video", "traffic"), default="all")
    parser.add_argument(
        "--model-mirror",
        action="store_true",
        help="Use the official Ultralytics Hugging Face mirror; same pinned SHA-256",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    names = list(ASSETS) if args.asset == "all" else [args.asset]
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(partial(fetch, model_mirror=args.model_mirror), names))


if __name__ == "__main__":
    main()

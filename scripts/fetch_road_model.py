"""Install verified official NVIDIA road model; convert trusted weights to safetensors."""

import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(directory: Path = Path("datasets/models/road-segformer-city")) -> None:
    manifest: dict[str, Any] = json.loads(
        Path("configs/models/road-segformer-city.json").read_text(encoding="utf-8")
    )
    directory.mkdir(parents=True, exist_ok=True)
    if all(
        (directory / name).is_file() and digest(directory / name) == checksum
        for name, checksum in manifest["files"].items()
    ):
        print("Road model already installed and verified")
        return
    names = {**manifest["files"], "pytorch_model.bin": manifest["source_weights_sha256"]}
    names.pop("model.safetensors")
    for name, checksum in names.items():
        path = directory / name
        if path.is_file() and digest(path) == checksum:
            continue
        url = (
            f"https://huggingface.co/{manifest['repository']}/resolve/{manifest['revision']}/{name}"
        )
        temporary = path.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=60) as response, temporary.open("wb") as out:
            size = 0
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 32 * 1024 * 1024:
                    raise ValueError("Unexpected road-model file size")
                out.write(chunk)
        if digest(temporary) != checksum:
            raise ValueError("Road-model SHA-256 mismatch; installation refused")
        temporary.replace(path)
    import torch
    from safetensors.torch import save_file

    # Only verified official bytes reach the restricted weights-only loader.
    state = torch.load(directory / "pytorch_model.bin", map_location="cpu", weights_only=True)
    temporary = directory / "model.safetensors.part"
    save_file(state, str(temporary), metadata={"format": "pt"})
    if digest(temporary) != manifest["files"]["model.safetensors"]:
        raise ValueError("Converted safetensors SHA-256 mismatch; installation refused")
    temporary.replace(directory / "model.safetensors")
    print("Road model installed and verified")


if __name__ == "__main__":
    fetch()

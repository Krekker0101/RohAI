"""Regenerate portable annotation/release JSON schemas from the executable contract."""

import json
from pathlib import Path

from traffic_vision.dataset.schema import DatasetSpec, FrameAnnotation, ReleaseManifest


def main() -> None:
    root = Path("datasets/schema")
    root.mkdir(parents=True, exist_ok=True)
    for name, model in (
        ("dataset", DatasetSpec),
        ("annotation", FrameAnnotation),
        ("release", ReleaseManifest),
    ):
        (root / f"{name}-1.0.json").write_text(
            json.dumps(model.model_json_schema(), indent=2) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    main()

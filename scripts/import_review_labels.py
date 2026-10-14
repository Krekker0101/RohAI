"""Validate human review against immutable frame hashes; export native labels only."""

import argparse
import json
from pathlib import Path
from typing import Any

from traffic_core.models import ObjectKind
from traffic_vision.dataset.files import digest_file
from traffic_vision.dataset.labels import validate_boxes
from traffic_vision.dataset.schema import FrameAnnotation

CLASSES = ("car", "bus", "truck", "motorcycle", "bicycle", "pedestrian")


def convert(review: Path, labels: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise FileExistsError("Choose a new label output directory")
    if labels.stat().st_size > 10_000_000:
        raise ValueError("Label file exceeds 10 MB")
    manifest = json.loads((review / "review.json").read_text(encoding="utf-8"))
    payload = json.loads(labels.read_text(encoding="utf-8"))
    if (
        payload.get("schema_version") != "1.0"
        or payload.get("source_sha256") != manifest["source_sha256"]
        or payload.get("classes") != list(CLASSES)
        or not isinstance(payload.get("frames"), list)
        or len(payload["frames"]) != len(manifest["frames"])
    ):
        raise ValueError("Label schema, source identity or frame inventory mismatch")
    validated: list[FrameAnnotation] = []
    for frame, entry in zip(manifest["frames"], payload["frames"], strict=True):
        if (
            entry.get("frame_index") != frame["frame_index"]
            or entry.get("image_sha256") != frame["sha256"]
            or type(entry.get("exhaustive")) is not bool
        ):
            raise ValueError("Frame identity or review flag mismatch")
        image = (review / frame["image"]).resolve()
        if not image.is_relative_to(review.resolve()) or digest_file(image) != frame["sha256"]:
            raise ValueError("Frame path or image hash mismatch")
        if not isinstance(entry.get("objects"), list):
            raise ValueError("Object list required even for an empty frame")
        if not entry["exhaustive"]:
            continue  # Never turn an unreviewed empty list into a negative sample.
        annotation = FrameAnnotation.model_validate(
            {
                "frame_id": f"frame-{frame['frame_index']:08d}",
                "layers": ["detection"],
                "exhaustive_classes": list(range(len(CLASSES))),
                "provenance": "human",
                "reviewed_by": entry.get("reviewed_by"),
                "objects": entry["objects"],
            }
        )
        if not annotation.reviewed_by or not annotation.reviewed_by.strip():
            raise ValueError("Reviewer name required")
        validate_boxes(
            annotation,
            frame["width"],
            frame["height"],
            {index: ObjectKind(name) for index, name in enumerate(CLASSES)},
        )
        validated.append(annotation)
    if not validated:
        raise ValueError("No explicitly reviewed frames; nothing exported")
    # Validate the entire input before creating any output.
    output.mkdir(parents=True)
    for annotation in validated:
        (output / f"{annotation.frame_id}.json").write_text(
            annotation.model_dump_json(indent=2), encoding="utf-8"
        )
    result = {
        "status": "HUMAN_LABELS_NOT_DATASET_RELEASE",
        "source_sha256": manifest["source_sha256"],
        "classes": {str(index): name for index, name in enumerate(CLASSES)},
        "reviewed_frames": len(validated),
        "unreviewed_frames": len(manifest["frames"]) - len(validated),
        "training_started": False,
        "note": "Dataset metadata, rights, grouped splits and independent evaluation required",
    }
    (output / "import.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path)
    parser.add_argument("labels", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(convert(args.review, args.labels, args.output)))


if __name__ == "__main__":
    main()

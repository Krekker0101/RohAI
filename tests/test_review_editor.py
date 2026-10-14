import hashlib
import json
from pathlib import Path

import pytest
from traffic_vision.dataset.schema import FrameAnnotation

from scripts.import_review_labels import CLASSES, convert


def fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    review = tmp_path / "review"
    review.mkdir()
    raw = b"immutable frame fixture"
    (review / "image.png").write_bytes(raw)
    sha = hashlib.sha256(raw).hexdigest()
    frame = {"frame_index": 0, "image": "image.png", "sha256": sha, "width": 100, "height": 100}
    (review / "review.json").write_text(json.dumps({"source_sha256": "a" * 64, "frames": [frame]}))
    labels = tmp_path / "labels.json"
    labels.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "source_sha256": "a" * 64,
                "classes": list(CLASSES),
                "frames": [
                    {
                        "frame_index": 0,
                        "image_sha256": sha,
                        "objects": [],
                        "reviewed_by": "human-reviewer",
                        "exhaustive": True,
                    }
                ],
            }
        )
    )
    return review, labels, tmp_path / "output"


def test_reviewed_empty_frame_is_explicit_negative(tmp_path: Path) -> None:
    review, labels, output = fixture(tmp_path)
    assert convert(review, labels, output)["reviewed_frames"] == 1
    annotation = FrameAnnotation.model_validate_json((output / "frame-00000000.json").read_text())
    assert annotation.provenance == "human" and not annotation.objects
    assert annotation.exhaustive_classes == tuple(range(6))


@pytest.mark.parametrize("failure", ["unreviewed", "wrong_source", "tampered", "box", "reviewer"])
def test_invalid_review_creates_no_labels(tmp_path: Path, failure: str) -> None:
    review, labels, output = fixture(tmp_path)
    data = json.loads(labels.read_text())
    if failure == "unreviewed":
        data["frames"][0]["exhaustive"] = False
    elif failure == "wrong_source":
        data["source_sha256"] = "b" * 64
    elif failure == "tampered":
        (review / "image.png").write_bytes(b"changed")
    elif failure == "reviewer":
        data["frames"][0]["reviewed_by"] = " "
    else:
        data["frames"][0]["objects"] = [
            {
                "annotation_id": "car-1",
                "class_id": 0,
                "domain_class": "car",
                "bbox": {"x1": 0, "y1": 0, "x2": 101, "y2": 10},
            }
        ]
    labels.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        convert(review, labels, output)
    assert not output.exists()

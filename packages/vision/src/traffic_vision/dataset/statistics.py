from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal

from pydantic import Field
from traffic_core.models import Model

from traffic_vision.dataset.schema import DatasetSpec, Split
from traffic_vision.dataset.validation import read_annotations


class CoverageRequirement(Model):
    split: Split = Split.TEST
    filters: dict[str, str]
    minimum_sources: int = Field(default=1, ge=1)
    minimum_frames: int = Field(default=1, ge=1)
    purpose: Literal["coverage_inventory"] = "coverage_inventory"


def statistics(
    dataset: DatasetSpec, root: Path, requirements: tuple[CoverageRequirement, ...] = ()
) -> dict[str, object]:
    annotations = read_annotations(dataset, root)
    sources = {s.source_id: s for s in dataset.sources}
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    rows: list[dict[str, str]] = []
    for frame in dataset.frames:
        source = sources[frame.source_id]
        ann = annotations[frame.frame_id]
        values = {
            "split": dataset.split.assignments[frame.source_id].value,
            "camera": source.camera_id,
            "intersection": source.intersection_id,
            "day": str(source.captured_at.date()),
            **{k: str(v) for k, v in source.conditions.model_dump().items() if k != "tilt_degrees"},
            "resolution": f"{source.width}x{source.height}",
        }
        for key, value in values.items():
            counts[key][value] += 1
        for layer in ann.layers:
            counts["annotation_layers"][layer.value] += 1
        for obj in ann.objects:
            counts["class_objects"][obj.domain_class.value] += 1
        rows.append(
            {
                **values,
                "source_id": frame.source_id,
                "classes": ",".join(sorted({o.domain_class.value for o in ann.objects})),
                "layers": ",".join(ann.layers),
            }
        )
    gaps: list[dict[str, object]] = []
    allowed = {
        "camera",
        "intersection",
        "day",
        "day_night",
        "weather",
        "traffic_density",
        "occlusion",
        "camera_angle",
        "lighting",
        "resolution",
        "class",
        "layer",
    }
    for req in requirements:
        if not set(req.filters).issubset(allowed):
            raise ValueError("Unknown coverage dimension")

        def matches(row: dict[str, str], req: CoverageRequirement = req) -> bool:
            return row["split"] == req.split.value and all(
                value in row["classes"].split(",")
                if key == "class"
                else value in row["layers"].split(",")
                if key == "layer"
                else row[key] == value
                for key, value in req.filters.items()
            )

        selected = [row for row in rows if matches(row)]
        n_sources = len({row["source_id"] for row in selected})
        if n_sources < req.minimum_sources or len(selected) < req.minimum_frames:
            gaps.append(
                {
                    "requirement": req.model_dump(mode="json"),
                    "sources": n_sources,
                    "frames": len(selected),
                }
            )
    return {
        "sources": len(dataset.sources),
        "frames": len(dataset.frames),
        "sequences": len(dataset.sequences),
        "counts": {key: dict(sorted(values.items())) for key, values in sorted(counts.items())},
        "coverage_gaps": gaps,
        "unknown_conditions": {
            key: values["unknown"] for key, values in counts.items() if values["unknown"]
        },
        "empty_splits": [s.value for s in Split if not counts["split"][s.value]],
        "note": "Descriptive counts, not accuracy targets or independent sample sizes.",
    }

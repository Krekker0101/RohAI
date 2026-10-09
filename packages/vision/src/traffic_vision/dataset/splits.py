"""Connected source groups: no per-frame random split."""

import hashlib
from collections import defaultdict
from datetime import timedelta

from traffic_vision.dataset.schema import DatasetSpec, SourceVideo, Split, SplitDefinition


def source_groups(source: SourceVideo) -> dict[str, tuple[str, ...]]:
    start = source.captured_at.date()
    end = (source.captured_at + timedelta(seconds=source.duration_seconds)).date()
    days = tuple(str(start + timedelta(days=i)) for i in range((end - start).days + 1))
    return {
        "recording": (source.recording_id, source.video.sha256),
        "camera": (source.camera_id,),
        "day": days,
        "intersection": (source.intersection_id,),
    }


def overlaps(dataset: DatasetSpec) -> list[tuple[str, str, tuple[str, ...]]]:
    grouped: dict[tuple[str, str], set[str]] = defaultdict(set)
    for source in dataset.sources:
        for dimension, values in source_groups(source).items():
            for value in values:
                grouped[(dimension, value)].add(source.source_id)
    return [
        (dimension, value, tuple(sorted(ids)))
        for (dimension, value), ids in sorted(grouped.items())
        if len({dataset.split.assignments.get(i) for i in ids}) > 1
    ]


def plan_split(dataset: DatasetSpec, *, challenge_sources: tuple[str, ...] = ()) -> SplitDefinition:
    """Deterministic cohort allocation. Insufficient groups never causes frame splitting."""
    parent = {s.source_id: s.source_id for s in dataset.sources}
    if len(parent) != len(dataset.sources) or not set(challenge_sources).issubset(parent):
        raise ValueError("Duplicate/unknown source ID")

    def root(key: str) -> str:
        while parent[key] != key:
            parent[key] = parent[parent[key]]
            key = parent[key]
        return key

    owners: dict[tuple[str, str], str] = {}
    for source in dataset.sources:
        for dimension, values in source_groups(source).items():
            if dimension not in dataset.split.group_by:
                continue
            for value in values:
                key = dimension, value
                if key in owners:
                    parent[root(source.source_id)] = root(owners[key])
                owners[key] = source.source_id
    groups: dict[str, list[str]] = defaultdict(list)
    for source in dataset.sources:
        groups[root(source.source_id)].append(source.source_id)
    ordered = sorted(
        groups.values(),
        key=lambda ids: hashlib.sha256(
            f"{dataset.split.seed}:{','.join(sorted(ids))}".encode()
        ).hexdigest(),
    )
    ordinary = [ids for ids in ordered if not set(ids).intersection(challenge_sources)]
    if len(ordinary) < 3:
        raise ValueError("Need three independent cohorts; no frame-level split fallback.")
    assignments: dict[str, Split] = {}
    for ids in ordered:
        if set(ids).intersection(challenge_sources):
            assignments.update(dict.fromkeys(ids, Split.CHALLENGE))
    # Hold out whole cohorts: approximately 15% validation and 15% test, at least one each.
    held = max(1, round(len(ordinary) * 0.15))
    for index, ids in enumerate(ordinary):
        split = (
            Split.TEST if index < held else Split.VALIDATION if index < held * 2 else Split.TRAIN
        )
        assignments.update(dict.fromkeys(ids, split))
    return dataset.split.model_copy(update={"assignments": assignments})

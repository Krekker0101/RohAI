"""Local-only, licensed media ingestion into the existing immutable dataset workflow."""

import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

import cv2
import numpy as np
from pydantic import Field
from traffic_core.models import Model, ObjectKind

from traffic_vision.dataset.files import digest_file, inside, write_new_json
from traffic_vision.dataset.labels import import_coco, import_yolo, validate_boxes
from traffic_vision.dataset.schema import (
    Asset,
    Conditions,
    DatasetSpec,
    FrameAnnotation,
    FrameRecord,
    Identifier,
    Layer,
    SequenceSpec,
    SourceVideo,
    SplitDefinition,
    UsageRights,
)


class MediaSource(Model):
    source_id: Identifier
    recording_id: Identifier
    camera_id: Identifier
    intersection_id: Identifier
    kind: Literal["video", "images"]
    paths: tuple[str, ...] = Field(min_length=1, max_length=100000)
    captured_at: datetime
    conditions: Conditions
    rights: UsageRights
    scene_domain: Literal["REAL_ROAD", "PHYSICAL_MAKET", "SYNTHETIC"]
    provenance: str = Field(min_length=1)
    stride: int = Field(default=1, ge=1, le=10000)
    labels: dict[int, str] = Field(default_factory=dict)
    label_format: Literal["yolo", "coco", "rohai"] = "yolo"
    coco_category_mapping: dict[int, int] = Field(default_factory=dict)
    coco_image_ids: dict[int, int] = Field(default_factory=dict)


class ImportSpec(Model):
    dataset_id: Identifier
    version: Identifier
    purpose: Literal["field", "synthetic_fixture"] = "field"
    class_mapping: dict[int, ObjectKind]
    sources: tuple[MediaSource, ...] = Field(min_length=1, max_length=10000)
    max_sampled_frames: int = Field(default=10000, ge=1, le=1000000)


def import_media(spec_path: Path, destination: Path) -> Path:
    spec = ImportSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    if destination.exists():
        raise FileExistsError("Import destination must be new; use another dataset version")
    source_root = spec_path.parent
    if len({s.source_id for s in spec.sources}) != len(spec.sources):
        raise ValueError("Duplicate source IDs")
    for source in spec.sources:
        if not source.rights.training_allowed or not source.rights.privacy_reviewed:
            raise PermissionError(
                "Explicit training rights and privacy review required before copying media"
            )
        if source.captured_at.tzinfo is None:
            raise ValueError("Acquisition time needs timezone")
        if (source.scene_domain == "SYNTHETIC") != (spec.purpose == "synthetic_fixture"):
            raise ValueError("Synthetic and collected field media require separate datasets")
        for relative in (*source.paths, *source.labels.values()):
            if not inside(source_root, relative).is_file():
                raise ValueError("Local source asset unavailable")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".rohai-import-", dir=destination.parent) as temporary:
        root = Path(temporary) / "draft"
        root.mkdir()
        frames: list[FrameRecord] = []
        sources: list[SourceVideo] = []
        sequences: list[SequenceSpec] = []
        lineage = []

        def asset(relative: str) -> Asset:
            return Asset(path=relative, sha256=digest_file(inside(root, relative)))

        for source in spec.sources:
            original_hashes = {
                p: digest_file(inside(source_root, p))
                for p in (*source.paths, *source.labels.values())
            }
            if source.label_format == "coco" and not set(source.labels).issubset(
                source.coco_image_ids
            ):
                raise ValueError("COCO labels require explicit image IDs")
            source_layers: set[Layer] = set()
            first_source_frame = len(frames)
            capture = None
            writer = None
            media = f"{source.source_id}.avi"
            width = height = count = 0
            fps = 1.0
            if source.kind == "video":
                if len(source.paths) != 1:
                    raise ValueError("One original video per source; clips must share recording_id")
                original = inside(source_root, source.paths[0])
                media = source.source_id + original.suffix.lower()
                shutil.copyfile(original, root / media)
                capture = cv2.VideoCapture(str(root / media))
                fps = capture.get(cv2.CAP_PROP_FPS)
                if not capture.isOpened() or not 0 < fps <= 240:
                    capture.release()
                    raise ValueError("Invalid video/FPS")
            try:
                while True:
                    if capture is not None:
                        ok, image = capture.read()
                        if not ok:
                            break
                    elif count < len(source.paths):
                        decoded = cv2.imdecode(
                            np.frombuffer(
                                inside(source_root, source.paths[count]).read_bytes(),
                                dtype=np.uint8,
                            ),
                            cv2.IMREAD_COLOR,
                        )
                        if decoded is None:
                            raise ValueError("Corrupt image")
                        image = decoded
                    else:
                        break
                    h, w = image.shape[:2]
                    if width == 0:
                        width, height = w, h
                        if capture is None:
                            writer = cv2.VideoWriter(
                                str(root / media), cv2.VideoWriter.fourcc(*"FFV1"), 1, (w, h)
                            )
                            if not writer.isOpened():
                                raise RuntimeError("Lossless image-sequence adapter unavailable")
                    if (width, height) != (w, h):
                        raise ValueError("Resolution changes require separate sources")
                    if writer is not None:
                        writer.write(image)
                    if count % source.stride == 0:
                        if len(frames) >= spec.max_sampled_frames:
                            raise ValueError(
                                "Import exceeds frame limit; increase stride or explicit limit"
                            )
                        fid = f"{source.source_id}-{count}"
                        image_name, label_name = f"{fid}.png", f"{fid}.json"
                        if not cv2.imwrite(str(root / image_name), image):
                            raise RuntimeError("Image write failed")
                        ann = FrameAnnotation(frame_id=fid, provenance="context_only")
                        if count in source.labels:
                            label = inside(source_root, source.labels[count]).read_text(
                                encoding="utf-8"
                            )
                            if source.label_format == "yolo":
                                ann = import_yolo(
                                    label,
                                    frame_id=fid,
                                    width=w,
                                    height=h,
                                    classes=spec.class_mapping,
                                )
                            elif source.label_format == "coco":
                                ann = import_coco(
                                    json.loads(label),
                                    image_id=source.coco_image_ids[count],
                                    frame_id=fid,
                                    width=w,
                                    height=h,
                                    classes=spec.class_mapping,
                                    category_mapping=source.coco_category_mapping,
                                )
                            else:
                                imported = FrameAnnotation.model_validate_json(label)
                                ann = FrameAnnotation.model_validate(
                                    {
                                        **imported.model_dump(),
                                        "frame_id": fid,
                                        "provenance": "model_predictions",
                                        "reviewed_by": None,
                                    }
                                )
                                validate_boxes(ann, w, h, spec.class_mapping)
                        source_layers.update(ann.layers)
                        write_new_json(root / label_name, ann.model_dump(mode="json"))
                        frames.append(
                            FrameRecord(
                                frame_id=fid,
                                source_id=source.source_id,
                                frame_index=count,
                                timestamp=count / fps,
                                image=asset(image_name),
                                annotation=asset(label_name),
                            )
                        )
                    count += 1
            finally:
                if capture is not None:
                    capture.release()
                if writer is not None:
                    writer.release()
            if count == 0:
                raise ValueError("Empty media source")
            if any(
                digest_file(inside(source_root, p)) != digest
                for p, digest in original_hashes.items()
            ):
                raise ValueError("Source media or labels changed during import")
            if source_layers:
                for index in range(first_source_frame, len(frames)):
                    frames[index] = frames[index].model_copy(
                        update={"sequence_id": source.source_id}
                    )
                sequences.append(
                    SequenceSpec(
                        sequence_id=source.source_id,
                        source_id=source.source_id,
                        start_frame=0,
                        end_frame=frames[-1].frame_index,
                        stride=source.stride,
                        layers=tuple(sorted(source_layers, key=lambda item: item.value)),
                    )
                )
            source_lineage = {
                "source_id": source.source_id,
                "recording_id": source.recording_id,
                "original_assets": [
                    {"path": p, "sha256": original_hashes[p]} for p in source.paths
                ],
                "original_labels": [
                    {"frame_index": i, "sha256": original_hashes[p]}
                    for i, p in source.labels.items()
                ],
                "media_kind": source.kind,
                "image_adapter": "FFV1 container, not acquired video"
                if source.kind == "images"
                else None,
            }
            sources.append(
                SourceVideo(
                    source_id=source.source_id,
                    recording_id=source.recording_id,
                    camera_id=source.camera_id,
                    intersection_id=source.intersection_id,
                    video=asset(media),
                    captured_at=source.captured_at,
                    duration_seconds=count / fps,
                    fps=fps,
                    width=width,
                    height=height,
                    conditions=source.conditions,
                    rights=source.rights,
                    scene_domain=source.scene_domain,
                    media_kind="video" if source.kind == "video" else "image_sequence",
                    provenance=json.dumps(
                        {"description": source.provenance, "lineage": source_lineage},
                        sort_keys=True,
                    ),
                    usage=spec.purpose,
                    pretrained_exposure="unknown",
                )
            )
            lineage.append(source_lineage)
        from datetime import UTC

        dataset = DatasetSpec(
            dataset_id=spec.dataset_id,
            version=spec.version,
            created_at=datetime.now(UTC),
            purpose=spec.purpose,
            class_mapping=spec.class_mapping,
            sources=tuple(sources),
            frames=tuple(frames),
            sequences=tuple(sequences),
            split=SplitDefinition(
                group_by=("recording", "camera", "day", "intersection"),
                independence_exceptions="Extra intersection grouping; draft assignments",
            ),
        )
        write_new_json(root / "dataset.json", dataset.model_dump(mode="json"))
        write_new_json(root / "lineage.json", {"status": "DRAFT_UNVERIFIED", "sources": lineage})
        root.rename(destination)
    return destination / "dataset.json"

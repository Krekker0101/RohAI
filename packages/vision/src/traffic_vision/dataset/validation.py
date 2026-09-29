"""Dataset integrity, semantic checks and cross-split leakage detection."""

import json
from collections import defaultdict
from pathlib import Path
from typing import Literal

from pydantic import ValidationError
from traffic_core.models import Model, ObjectKind

from traffic_vision.dataset.files import digest_file, inside
from traffic_vision.dataset.schema import Asset, DatasetSpec, FrameAnnotation, Layer, Split
from traffic_vision.dataset.splits import overlaps


class Issue(Model):
    code: str
    location: str
    detail: str
    severity: Literal["error", "warning"] = "error"


class ValidationReport(Model):
    valid: bool
    issues: tuple[Issue, ...]
    images_checked: int
    annotations_checked: int
    near_duplicate_candidates: int


def read_annotations(dataset: DatasetSpec, root: Path) -> dict[str, FrameAnnotation]:
    return {
        f.frame_id: FrameAnnotation.model_validate_json(
            inside(root, f.annotation.path).read_text(encoding="utf-8")
        )
        for f in dataset.frames
    }


def validate_dataset(
    dataset: DatasetSpec, root: Path, *, decode_videos: bool = True
) -> ValidationReport:
    import cv2
    import numpy as np

    issues: list[Issue] = []

    def add(
        code: str, where: str, detail: str, severity: Literal["error", "warning"] = "error"
    ) -> None:
        issues.append(Issue(code=code, location=where, detail=detail, severity=severity))

    if not dataset.frames or not dataset.sources:
        add("empty_dataset", "dataset", "No labeled sources/frames")
    sources = {s.source_id: s for s in dataset.sources}
    if len(sources) != len(dataset.sources):
        add("duplicate_source", "sources", "Source IDs must be unique")
    if set(dataset.split.assignments) != set(sources):
        add(
            "split_assignment",
            "split",
            "Assign every source exactly once; no frame IDs or unknown sources",
        )
    paths: dict[str, str] = {}
    checked: dict[str, bool] = {}

    def check(asset: Asset, kind: str) -> bool:
        if asset.path in paths and paths[asset.path] != asset.sha256:
            add("conflicting_hash", asset.path, "One asset path has multiple hashes")
            return False
        paths[asset.path] = asset.sha256
        if asset.path in checked:
            return checked[asset.path]
        try:
            path = inside(root, asset.path)
            if not path.is_file():
                add(f"missing_{kind}", asset.path, "Declared asset is missing")
                checked[asset.path] = False
                return False
            if digest_file(path) != asset.sha256:
                add("hash_mismatch", asset.path, "Asset content changed")
                checked[asset.path] = False
                return False
        except (OSError, ValueError) as exc:
            add("unsafe_or_unreadable_asset", asset.path, str(exc))
            checked[asset.path] = False
            return False
        checked[asset.path] = True
        return True

    for video_source in dataset.sources:
        if check(video_source.video, "video") and decode_videos:
            capture = cv2.VideoCapture(str(inside(root, video_source.video.path)))
            try:
                count = 0
                while True:
                    ok, video_image = capture.read()
                    if not ok:
                        break
                    if video_image.shape[:2] != (video_source.height, video_source.width):
                        add("video_resolution", video_source.source_id, "Decoded dimensions differ")
                        break
                    count += 1
                expected = video_source.duration_seconds * video_source.fps
                if count == 0 or abs(count - expected) > max(2, expected * 0.01):
                    add(
                        "corrupt_or_truncated_video",
                        video_source.source_id,
                        f"Decoded {count}; declared approximately {expected} frames",
                    )
                actual_fps = capture.get(cv2.CAP_PROP_FPS)
                declared_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
                if declared_frames > 0 and declared_frames != count:
                    add(
                        "corrupt_or_truncated_video",
                        video_source.source_id,
                        "Decoded frame count differs from container",
                    )
                if actual_fps <= 0 or abs(actual_fps - video_source.fps) > 0.1:
                    add("video_fps", video_source.source_id, "Decode FPS disagrees with metadata")
            finally:
                capture.release()
        if video_source.geometry is not None and check(video_source.geometry, "calibration"):
            try:
                from traffic_vision.geometry import load_geometry

                geometry = load_geometry(inside(root, video_source.geometry.path))
                if (
                    geometry.source_id != video_source.camera_id
                    or geometry.camera_calibration is None
                    or geometry.road is None
                ):
                    raise ValueError(
                        "Calibration must match camera and include metric road geometry"
                    )
                from traffic_vision.calibration import CalibrationStatus, CalibrationValidator

                calibration_check = CalibrationValidator.validate(
                    geometry.camera_calibration,
                    camera_id=video_source.camera_id,
                    width=video_source.width,
                    height=video_source.height,
                    now=video_source.captured_at,
                )
                if calibration_check.status != CalibrationStatus.VALID:
                    raise ValueError(
                        f"Calibration invalid at acquisition: {calibration_check.reasons}"
                    )
            except (OSError, ValueError) as exc:
                add("invalid_calibration", video_source.source_id, str(exc))
        if video_source.alignment is not None and check(video_source.alignment, "alignment"):
            try:
                from traffic_vision.alignment import AlignmentEvidence

                rows = json.loads(
                    inside(root, video_source.alignment.path).read_text(encoding="utf-8")
                )
                if not isinstance(rows, list):
                    raise ValueError("Alignment log must be an array")
                previous = -1.0
                for row in rows:
                    observation = AlignmentEvidence.model_validate(row)
                    if len(observation.reference) != len(observation.observed):
                        raise ValueError("Alignment correspondence counts differ")
                    if (
                        observation.timestamp <= previous
                        or observation.timestamp >= video_source.duration_seconds
                    ):
                        raise ValueError("Alignment timestamps must advance within the video")
                    if observation.camera_id != video_source.camera_id or (
                        observation.width,
                        observation.height,
                    ) != (video_source.width, video_source.height):
                        raise ValueError("Alignment camera/resolution mismatch")
                    previous = observation.timestamp
            except (OSError, ValueError) as exc:
                add("invalid_alignment", video_source.source_id, str(exc))
        if dataset.split.assignments.get(video_source.source_id) in (Split.TEST, Split.CHALLENGE):
            if video_source.usage == "demo" or video_source.pretrained_exposure == "known_overlap":
                add(
                    "ineligible_holdout",
                    video_source.source_id,
                    "Demo or known pretrained overlap cannot be independent TEST/CHALLENGE",
                )
            if video_source.pretrained_exposure == "unknown":
                add(
                    "pretrained_exposure_unknown",
                    video_source.source_id,
                    "Independence from upstream pretraining is not established",
                    "warning",
                )
    for dimension, value, ids in overlaps(dataset):
        add(
            "split_overlap" if dimension in dataset.split.group_by else "independence_gap",
            value,
            f"{dimension} shared across splits: {ids}",
            "error" if dimension in dataset.split.group_by else "warning",
        )
    seen_frames: set[str] = set()
    seen_indices: set[tuple[str, int]] = set()
    image_hashes: dict[str, tuple[str, Split | None]] = {}
    pixel_hashes: dict[str, tuple[str, Split | None]] = {}
    perceptual: dict[int, list[tuple[str, Split | None]]] = defaultdict(list)
    track_classes: dict[tuple[str, str], ObjectKind] = {}
    sequence_frames: dict[str, list[int]] = defaultdict(list)
    events: set[tuple[str, str]] = set()
    annotations: dict[str, FrameAnnotation] = {}
    images_checked = annotations_checked = candidates = 0
    for frame in dataset.frames:
        source = sources.get(frame.source_id)
        if source is None:
            add("unknown_source", frame.frame_id, frame.source_id)
            continue
        if frame.frame_id in seen_frames or (frame.source_id, frame.frame_index) in seen_indices:
            add("duplicate_frame", frame.frame_id, "Frame ID/source index repeated")
        seen_frames.add(frame.frame_id)
        seen_indices.add((frame.source_id, frame.frame_index))
        if (
            frame.timestamp >= source.duration_seconds
            or abs(frame.timestamp - frame.frame_index / source.fps) > 0.5 / source.fps + 1e-6
        ):
            add(
                "broken_sequence",
                frame.frame_id,
                "Timestamp outside source or inconsistent with frame index/FPS",
            )
        split = dataset.split.assignments.get(frame.source_id)
        if frame.sequence_id:
            sequence_frames[frame.sequence_id].append(frame.frame_index)
        if check(frame.image, "image"):
            image = cv2.imdecode(
                np.frombuffer(inside(root, frame.image.path).read_bytes(), dtype=np.uint8),
                cv2.IMREAD_COLOR,
            )
            if image is None:
                add("corrupt_image", frame.image.path, "Image decoder failed")
            else:
                import hashlib

                images_checked += 1
                if image.shape[:2] != (source.height, source.width):
                    add("image_resolution", frame.frame_id, "Image/source dimensions differ")
                pixel_hash = hashlib.sha256(image.tobytes()).hexdigest()
                for digest, registry in (
                    (frame.image.sha256, image_hashes),
                    (pixel_hash, pixel_hashes),
                ):
                    if digest in registry and registry[digest][1] != split:
                        add(
                            "image_leakage",
                            frame.frame_id,
                            f"Same bytes/decoded pixels in another split: {registry[digest][0]}",
                        )
                    registry[digest] = frame.frame_id, split
                gray = cv2.resize(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), (9, 8))
                bits = gray[:, 1:] > gray[:, :-1]
                signature = int.from_bytes(np.packbits(bits).tobytes(), "big")
                # Exact dHash collisions are review candidates, not proof of identity.
                prior = perceptual[signature]
                if any(old_split != split for _, old_split in prior):
                    candidates += 1
                    add(
                        "near_duplicate_review",
                        frame.frame_id,
                        "Same perceptual signature across splits; review provenance",
                        "warning",
                    )
                prior.append((frame.frame_id, split))
        if not check(frame.annotation, "annotation"):
            continue
        try:
            ann = FrameAnnotation.model_validate_json(
                inside(root, frame.annotation.path).read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            add("invalid_annotation", frame.annotation.path, str(exc))
            continue
        annotations_checked += 1
        annotations[frame.frame_id] = ann
        if ann.frame_id != frame.frame_id:
            add("annotation_frame_mismatch", frame.frame_id, ann.frame_id)
        if ann.provenance == "model_predictions":
            add(
                "prediction_as_ground_truth",
                frame.frame_id,
                "Unreviewed predictions are not annotations",
            )
        if dataset.purpose == "field" and (
            ann.provenance == "synthetic_fixture" or source.usage == "synthetic_fixture"
        ):
            add(
                "synthetic_as_field", frame.frame_id, "Fixture data cannot establish field baseline"
            )
        if any(c not in dataset.class_mapping for c in ann.exhaustive_classes):
            add("out_of_range_class", frame.frame_id, "Unknown exhaustive class ID")
        if Layer.TRACKING in ann.layers and frame.sequence_id is None:
            add(
                "broken_sequence", frame.frame_id, "Tracking requires an explicit complete sequence"
            )
        if Layer.CALIBRATION in ann.layers and source.geometry is None:
            add(
                "missing_calibration",
                frame.frame_id,
                "Calibration layer requires a surveyed geometry asset",
            )
        if Layer.ANALYTICS in ann.layers and not ann.traffic:
            add(
                "missing_analytics",
                frame.frame_id,
                "Selected analytics sample has no feature ground truth",
            )
        object_ids: set[str] = set()
        boxes: set[tuple[int, float, float, float, float]] = set()
        track_ids: set[str] = set()
        for obj in ann.objects:
            box = obj.bbox
            box_key = obj.class_id, box.x1, box.y1, box.x2, box.y2
            if obj.annotation_id in object_ids or box_key in boxes:
                add("duplicate_label", frame.frame_id, obj.annotation_id)
            object_ids.add(obj.annotation_id)
            boxes.add(box_key)
            if (
                dataset.class_mapping.get(obj.class_id) != obj.domain_class
                or obj.domain_class == ObjectKind.UNKNOWN
            ):
                add("unknown_or_out_of_range_class", frame.frame_id, str(obj.class_id))
            if box.x2 > source.width or box.y2 > source.height:
                add("invalid_bbox", frame.frame_id, "BBox extends beyond image")
            if (
                Layer.TRACKING in ann.layers
                and obj.class_id in ann.exhaustive_classes
                and not obj.ignore
                and obj.track_id is None
            ):
                add("impossible_track_id", frame.frame_id, "Tracking object lacks identity")
            if obj.track_id is not None:
                key = frame.sequence_id or frame.source_id, obj.track_id
                if obj.track_id in track_ids or (
                    key in track_classes and track_classes[key] != obj.domain_class
                ):
                    add(
                        "impossible_track_id",
                        frame.frame_id,
                        "Repeated ID in frame or class change in sequence",
                    )
                track_ids.add(obj.track_id)
                track_classes[key] = obj.domain_class
            if obj.pedestrian_state is not None and obj.domain_class != ObjectKind.PEDESTRIAN:
                add("pedestrian_class_mismatch", frame.frame_id, obj.annotation_id)
            if (
                Layer.PEDESTRIAN in ann.layers
                and obj.domain_class == ObjectKind.PEDESTRIAN
                and obj.pedestrian_state is None
            ):
                add("missing_pedestrian_state", frame.frame_id, obj.annotation_id)
            if obj.lane_id is not None and obj.approach is None:
                add("lane_without_approach", frame.frame_id, obj.annotation_id)
        feature_keys = [(f.approach, f.feature) for f in ann.traffic]
        if len(set(feature_keys)) != len(feature_keys):
            add("duplicate_feature", frame.frame_id, "Duplicate traffic feature ground truth")
        for event in ann.events:
            key = frame.sequence_id or frame.source_id, event.event_id
            if key in events:
                add("duplicate_event", frame.frame_id, event.event_id)
            events.add(key)
            if event.timestamp >= source.duration_seconds or frame.sequence_id is None:
                add(
                    "invalid_event", frame.frame_id, "Event needs a sequence and in-range timestamp"
                )
    sequences = {s.sequence_id: s for s in dataset.sequences}
    if len(sequences) != len(dataset.sequences):
        add("duplicate_sequence", "sequences", "Sequence IDs must be unique")
    for sequence_key, indices in sequence_frames.items():
        sequence = sequences.get(sequence_key)
        if sequence is None:
            add("broken_sequence", sequence_key, "Undeclared sequence")
            continue
        available_layers = {
            layer
            for item in dataset.frames
            if item.sequence_id == sequence_key
            for layer in (annotations[item.frame_id].layers if item.frame_id in annotations else ())
        }
        if not set(sequence.layers).issubset(available_layers):
            add(
                "missing_annotation_layer", sequence_key, "No samples for a declared sequence layer"
            )
        expected_indices = list(
            range(sequence.start_frame, sequence.end_frame + 1, sequence.stride)
        )
        if sorted(indices) != expected_indices:
            add(
                "broken_sequence",
                sequence_key,
                "Missing/repeated frames in declared sequence interval",
            )
        for frame in (f for f in dataset.frames if f.sequence_id == sequence_key):
            if frame.source_id != sequence.source_id:
                add("broken_sequence", sequence_key, "Sequence crosses source videos")
            seq_ann = annotations.get(frame.frame_id)
            if seq_ann is not None and not (
                {Layer.TRACKING} if Layer.TRACKING in sequence.layers else set()
            ).issubset(seq_ann.layers):
                add("missing_annotation_layer", frame.frame_id, "Sequence layer missing on frame")
    for sequence_key in set(sequences) - set(sequence_frames):
        add("broken_sequence", sequence_key, "Sequence contains no frames")
    for frame in dataset.frames:
        seq_ann = annotations.get(frame.frame_id)
        if seq_ann is not None:
            for event in seq_ann.events:
                if (frame.sequence_id or frame.source_id, event.track_id) not in track_classes:
                    add("impossible_event_track", frame.frame_id, event.track_id)
    return ValidationReport(
        valid=not any(i.severity == "error" for i in issues),
        issues=tuple(issues),
        images_checked=images_checked,
        annotations_checked=annotations_checked,
        near_duplicate_candidates=candidates,
    )


def validate_path(path: Path, *, decode_videos: bool = True) -> ValidationReport:
    try:
        dataset = DatasetSpec.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        return ValidationReport(
            valid=False,
            issues=(Issue(code="invalid_dataset_schema", location=str(path), detail=str(exc)),),
            images_checked=0,
            annotations_checked=0,
            near_duplicate_candidates=0,
        )
    return validate_dataset(dataset, path.parent, decode_videos=decode_videos)

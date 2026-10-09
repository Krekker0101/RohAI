"""Detection interchange. Imports are UNVERIFIED proposals until explicitly reviewed.

COCO/YOLO cannot carry all traffic truth: retain the RohAI JSON sidecar for tracks,
lanes, direction, queue membership, events and measured waiting-time annotations.
"""

import math
from typing import Any

from traffic_core.models import ObjectKind

from traffic_vision.contracts import BoundingBox
from traffic_vision.dataset.schema import FrameAnnotation, Layer, ObjectAnnotation


def validate_boxes(
    annotation: FrameAnnotation, width: int, height: int, classes: dict[int, ObjectKind]
) -> None:
    if width <= 0 or height <= 0:
        raise ValueError("Positive image dimensions required")
    identifiers = set()
    for obj in annotation.objects:
        b = obj.bbox
        if obj.annotation_id in identifiers:
            raise ValueError("Duplicate annotation identity")
        identifiers.add(obj.annotation_id)
        if obj.class_id not in classes or classes[obj.class_id] != obj.domain_class:
            raise ValueError("Unknown or incompatible class")
        if not all(math.isfinite(v) for v in (b.x1, b.y1, b.x2, b.y2)):
            raise ValueError("Nonfinite bounding box")
        if not 0 <= b.x1 < b.x2 <= width or not 0 <= b.y1 < b.y2 <= height:
            raise ValueError("Bounding box outside image")


def import_yolo(
    text: str, *, frame_id: str, width: int, height: int, classes: dict[int, ObjectKind]
) -> FrameAnnotation:
    objects = []
    for index, row in enumerate(text.splitlines()):
        if not row.strip():
            continue
        values = row.split()
        if len(values) != 5:
            raise ValueError("Detection YOLO labels require class xc yc width height")
        class_id = int(values[0])
        if class_id not in classes:
            raise ValueError("Unknown class")
        x, y, w, h = (float(v) for v in values[1:])
        if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
            raise ValueError("Invalid normalized bounding box")
        objects.append(
            ObjectAnnotation(
                annotation_id=f"box-{index}",
                class_id=class_id,
                domain_class=classes[class_id],
                bbox=BoundingBox(
                    x1=(x - w / 2) * width,
                    y1=(y - h / 2) * height,
                    x2=(x + w / 2) * width,
                    y2=(y + h / 2) * height,
                ),
            )
        )
    result = FrameAnnotation(
        frame_id=frame_id,
        layers=(Layer.DETECTION,),
        provenance="model_predictions",
        objects=tuple(objects),
    )
    validate_boxes(result, width, height, classes)
    return result


def export_yolo(
    annotation: FrameAnnotation, *, width: int, height: int, classes: dict[int, ObjectKind]
) -> str:
    validate_boxes(annotation, width, height, classes)
    if any(obj.ignore for obj in annotation.objects):
        raise ValueError("YOLO detection export cannot preserve ignored regions")
    rows = []
    for obj in annotation.objects:
        b = obj.bbox
        values = (
            (b.x1 + b.x2) / (2 * width),
            (b.y1 + b.y2) / (2 * height),
            (b.x2 - b.x1) / width,
            (b.y2 - b.y1) / height,
        )
        rows.append(str(obj.class_id) + " " + " ".join(f"{v:.12g}" for v in values))
    return "\n".join(rows) + ("\n" if rows else "")


def import_coco(
    payload: dict[str, Any],
    *,
    image_id: int,
    frame_id: str,
    width: int,
    height: int,
    classes: dict[int, ObjectKind],
    category_mapping: dict[int, int],
) -> FrameAnnotation:
    images = payload.get("images")
    if not isinstance(images, list) or not isinstance(payload.get("annotations"), list):
        raise ValueError("COCO images and annotations lists required")
    matching = [row for row in images if isinstance(row, dict) and row.get("id") == image_id]
    if len(matching) != 1 or (matching[0].get("width"), matching[0].get("height")) != (
        width,
        height,
    ):
        raise ValueError("COCO image identity or dimensions do not match imported media")
    objects = []
    for index, row in enumerate(payload.get("annotations", [])):
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("bbox"), list)
            or len(row["bbox"]) != 4
        ):
            raise ValueError("Malformed COCO annotation")
        if row.get("image_id") != image_id:
            continue
        category = row.get("category_id")
        if category not in category_mapping or category_mapping[category] not in classes:
            raise ValueError("Unknown COCO category")
        class_id = category_mapping[category]
        x, y, w, h = row["bbox"]
        if w <= 0 or h <= 0:
            raise ValueError("Invalid COCO bounding box")
        objects.append(
            ObjectAnnotation(
                annotation_id=f"box-{index}",
                class_id=class_id,
                domain_class=classes[class_id],
                ignore=bool(row.get("iscrowd", False)),
                bbox=BoundingBox(x1=x, y1=y, x2=x + w, y2=y + h),
            )
        )
    result = FrameAnnotation(
        frame_id=frame_id,
        layers=(Layer.DETECTION,),
        provenance="model_predictions",
        objects=tuple(objects),
    )
    validate_boxes(result, width, height, classes)
    return result


def review(
    annotation: FrameAnnotation,
    *,
    reviewer: str,
    exhaustive_classes: tuple[int, ...],
    width: int,
    height: int,
    classes: dict[int, ObjectKind],
) -> FrameAnnotation:
    if not reviewer.strip() or not set(exhaustive_classes).issubset(classes):
        raise ValueError("Reviewer and explicit valid class coverage required")
    validate_boxes(annotation, width, height, classes)
    return FrameAnnotation.model_validate(
        {
            **annotation.model_dump(),
            "provenance": "assisted_human_reviewed",
            "reviewed_by": reviewer.strip(),
            "exhaustive_classes": exhaustive_classes,
        }
    )


def export_coco(
    annotation: FrameAnnotation,
    *,
    image_id: int,
    file_name: str,
    width: int,
    height: int,
    classes: dict[int, ObjectKind],
) -> dict[str, Any]:
    validate_boxes(annotation, width, height, classes)
    rows = []
    for index, obj in enumerate(annotation.objects):
        b = obj.bbox
        rows.append(
            {
                "id": index + 1,
                "image_id": image_id,
                "category_id": obj.class_id,
                "bbox": [b.x1, b.y1, b.x2 - b.x1, b.y2 - b.y1],
                "area": (b.x2 - b.x1) * (b.y2 - b.y1),
                "iscrowd": int(obj.ignore),
            }
        )
    return {
        "images": [{"id": image_id, "file_name": file_name, "width": width, "height": height}],
        "categories": [{"id": key, "name": value.value} for key, value in sorted(classes.items())],
        "annotations": rows,
        "rohai_sidecar": annotation.model_dump(mode="json"),
        "status": "UNVERIFIED"
        if annotation.provenance == "model_predictions"
        else annotation.provenance.upper(),
        "note": "Retain rohai_sidecar to preserve traffic and track truth.",
    }

"""Unverified image-derived candidates for an axis-aligned four-arm maket."""

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from traffic_vision.geometry import IntersectionGeometry


def project_geometry(frame: Any, source_id: str, corners: list[dict[str, float]]) -> dict[str, Any]:
    """Four clockwise outer view anchors: assisted projective draft, never road truth."""
    if len(corners) != 4:
        raise ValueError(
            "Select four outer road-view corners: top-left, top-right, bottom-right, bottom-left"
        )
    target = np.asarray([[p["x"], p["y"]] for p in corners], dtype=np.float32)
    if not np.isfinite(target).all() or (target < 0).any() or (target > 1).any():
        raise ValueError("Anchors must be finite normalized image coordinates")
    cross = []
    for i in range(4):
        a, b = target[(i + 1) % 4] - target[i], target[(i + 2) % 4] - target[(i + 1) % 4]
        cross.append(float(a[0] * b[1] - a[1] * b[0]))
    if not all(value > 0.001 for value in cross):
        raise ValueError("Anchors must form a nondegenerate clockwise convex quadrilateral")
    matrix = cv2.getPerspectiveTransform(
        np.asarray([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32), target
    )
    geometry = json.loads(Path("configs/maket/geometry-draft.json").read_text(encoding="utf-8"))

    def project(value: Any) -> None:
        if isinstance(value, dict):
            if set(value) == {"x", "y"}:
                point = matrix @ np.asarray([value["x"], value["y"], 1.0])
                if abs(point[2]) < 1e-8:
                    raise ValueError("Perspective horizon crosses the configured road")
                value.update(x=float(point[0] / point[2]), y=float(point[1] / point[2]))
            else:
                for child in value.values():
                    project(child)
        elif isinstance(value, list):
            for child in value:
                project(child)

    project(geometry)
    h, w = frame.shape[:2]
    geometry.update(
        source_id=source_id, reference_width=w, reference_height=h, calibration_validated_at=None
    )
    geometry = IntersectionGeometry.model_validate(geometry).model_dump(mode="json")
    return {
        "available": True,
        "geometry": geometry,
        "verified": False,
        "provenance": "OPERATOR_ANCHORED_TEMPLATE_HYPOTHESIS",
        "confidence": None,
        "reason": "Perspective seed only. Verify directions, lanes and every counting line.",
    }


def suggest_geometry(frame: Any, source_id: str) -> dict[str, Any]:
    if (
        not isinstance(frame, np.ndarray)
        or frame.dtype != np.uint8
        or frame.ndim != 3
        or frame.shape[2] != 3
        or not frame.size
    ):
        raise ValueError("Valid live BGR frame required")
    h, w = frame.shape[:2]
    edges = cv2.Canny(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), 60, 150)
    lines = cv2.HoughLinesP(
        edges, 1, np.pi / 180, 60, minLineLength=min(h, w) * 0.25, maxLineGap=15
    )
    xs: list[float] = []
    ys: list[float] = []
    if lines is not None:
        for x1, y1, x2, y2 in lines[:, 0]:
            if abs(x2 - x1) < 0.03 * w and 0.15 < x1 / w < 0.85:
                xs.append(float((x1 + x2) / (2 * w)))
            if abs(y2 - y1) < 0.03 * h and 0.15 < y1 / h < 0.85:
                ys.append(float((y1 + y2) / (2 * h)))
    bounds = []
    for values in (xs, ys):
        low = [v for v in values if v < 0.48]
        high = [v for v in values if v > 0.52]
        if not low or not high:
            return {"available": False, "reason": "Road edges uncertain. Draw zones manually."}
        bounds.append((float(np.median(low)), float(np.median(high))))
    if any(high - low < 0.08 or high - low > 0.65 for low, high in bounds):
        return {
            "available": False,
            "verified": False,
            "reason": "Inconsistent road width. Use four assisted anchors or draw zones.",
        }
    g = json.loads(Path("configs/maket/geometry-draft.json").read_text(encoding="utf-8"))

    def transform(value: Any) -> None:
        if isinstance(value, dict):
            if set(value) == {"x", "y"}:
                for axis, (low, high) in zip(("x", "y"), bounds, strict=True):
                    value[axis] = float(
                        np.interp(
                            value[axis],
                            [0, 0.36, 0.5, 0.64, 1],
                            [0, low, (low + high) / 2, high, 1],
                        )
                    )
            else:
                for child in value.values():
                    transform(child)
        elif isinstance(value, list):
            for child in value:
                transform(child)

    transform(g)
    g.update(
        source_id=source_id, reference_width=w, reference_height=h, calibration_validated_at=None
    )
    return {
        "available": True,
        "geometry": g,
        "verified": False,
        "provenance": "IMAGE_EDGES_PLUS_TEMPLATE_HYPOTHESIS",
        "confidence": None,
        "edge_evidence": {"vertical_segments": len(xs), "horizontal_segments": len(ys)},
        "orientation_degrees": 0,
        "reason": "Image edges suggest an axis-aligned intersection. Verify every zone and line.",
    }

"""Unverified image-derived candidates for an axis-aligned four-arm maket."""

import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def suggest_geometry(frame: Any, source_id: str) -> dict[str, Any]:
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
        "orientation_degrees": 0,
        "reason": "Image edges suggest an axis-aligned intersection. Verify every zone and line.",
    }

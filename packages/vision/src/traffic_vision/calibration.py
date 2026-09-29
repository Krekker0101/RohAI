"""Versioned planar calibration with independent metric validation; no px/m fallback."""

import math
from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field, model_validator
from traffic_core.models import Model

from traffic_vision.models import Point


class CalibrationStatus(StrEnum):
    VALID = "VALID"
    SUSPECT = "SUSPECT"
    INVALID = "INVALID"
    MISSING = "MISSING"
    STALE = "STALE"


class CalibrationReference(Model):
    point_id: str = Field(min_length=1)
    image: Point
    world: Point  # surveyed local road plane, metres


class CalibrationMetadata(Model):
    camera_id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    timestamp: datetime
    valid_for_seconds: float = Field(default=2592000, gt=0)
    image_model: Literal["rectified", "distortion_negligible_verified"]
    world_unit: Literal["m"] = "m"
    world_frame: str = Field(min_length=1)
    validation_error_m: float = Field(ge=0)
    validation_max_error_m: float = Field(ge=0)
    maximum_rmse_m: float = Field(default=0.25, gt=0)
    maximum_point_error_m: float = Field(default=0.5, gt=0)
    allow_uniform_resize: bool = False
    contact_error_px: float = Field(default=2, ge=0)

    @model_validator(mode="after")
    def timezone(self) -> "CalibrationMetadata":
        if self.timestamp.tzinfo is None:
            raise ValueError("Calibration timestamp requires timezone")
        return self


class CameraCalibration(Model):
    source_width: int = Field(ge=2, le=16384)
    source_height: int = Field(ge=2, le=16384)
    reference_points: tuple[CalibrationReference, ...] = Field(min_length=4, max_length=256)
    validation_points: tuple[CalibrationReference, ...] = Field(min_length=4, max_length=256)
    homography: tuple[
        tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]
    ]
    metadata: CalibrationMetadata

    @model_validator(mode="after")
    def independent_points(self) -> "CameraCalibration":
        all_points = (*self.reference_points, *self.validation_points)
        if len({p.point_id for p in all_points}) != len(all_points):
            raise ValueError("Fit and validation point IDs must be independent")
        if len({(p.image.x, p.image.y) for p in all_points}) != len(all_points):
            raise ValueError("Do not reuse fitting image points as validation points")
        if len({(p.world.x, p.world.y) for p in all_points}) != len(all_points):
            raise ValueError("World reference points must be distinct")
        if any(
            not (0 <= p.image.x <= self.source_width and 0 <= p.image.y <= self.source_height)
            for p in all_points
        ):
            raise ValueError("Reference point outside source image")
        return self


class CalibrationValidation(Model):
    status: CalibrationStatus
    rmse_m: float | None = None
    max_error_m: float | None = None
    reasons: tuple[str, ...] = ()


class RoadPlaneTransform:
    def __init__(self, calibration: CameraCalibration) -> None:
        self.calibration = calibration
        points = sorted({(p.image.x, p.image.y) for p in calibration.reference_points})
        lower: list[tuple[float, float]] = []
        upper: list[tuple[float, float]] = []

        def turn(a: tuple[float, float], b: tuple[float, float], p: tuple[float, float]) -> float:
            return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])

        for collection, ordered in ((lower, points), (upper, list(reversed(points)))):
            for point in ordered:
                while len(collection) >= 2 and turn(collection[-2], collection[-1], point) <= 0:
                    collection.pop()
                collection.append(point)
        self.support = lower[:-1] + upper[:-1]

    def dimensions_valid(self, width: int, height: int) -> bool:
        c = self.calibration
        return (width == c.source_width and height == c.source_height) or (
            c.metadata.allow_uniform_resize
            and width > 0
            and height > 0
            and abs(width / height - c.source_width / c.source_height) < 1e-6
        )

    def project(self, point: Point, width: int, height: int) -> Point:
        if not self.dimensions_valid(width, height):
            raise ValueError("Resolution/crop differs from calibrated camera")
        if not (0 <= point.x <= width and 0 <= point.y <= height):
            raise ValueError("Ground contact outside image")
        c = self.calibration
        x, y = point.x * c.source_width / width, point.y * c.source_height / height
        if len(self.support) < 3 or any(
            (b[0] - a[0]) * (y - a[1]) - (b[1] - a[1]) * (x - a[0]) < -1e-6
            for a, b in zip(self.support, self.support[1:] + self.support[:1], strict=True)
        ):
            raise ValueError("Ground contact outside surveyed support")
        h = c.homography
        w = h[2][0] * x + h[2][1] * y + h[2][2]
        if abs(w) < 1e-9:
            raise ValueError("Ground point lies on projective horizon")
        return Point(
            x=(h[0][0] * x + h[0][1] * y + h[0][2]) / w, y=(h[1][0] * x + h[1][1] * y + h[1][2]) / w
        )

    def positional_error_bound(self, point: Point, width: int, height: int) -> float:
        """Local sensitivity bound, not a calibrated probability interval."""
        c = self.calibration
        p = self.project(point, width, height)
        error = 0.0
        for dx, dy in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
            q = Point(
                x=min(
                    width,
                    max(0, point.x + dx * c.metadata.contact_error_px * width / c.source_width),
                ),
                y=min(
                    height,
                    max(0, point.y + dy * c.metadata.contact_error_px * height / c.source_height),
                ),
            )
            world = self.project(q, width, height)
            error = max(error, math.hypot(world.x - p.x, world.y - p.y))
        return error + c.metadata.validation_max_error_m


class CalibrationValidator:
    @staticmethod
    def validate(
        c: CameraCalibration,
        *,
        camera_id: str,
        width: int,
        height: int,
        now: datetime | None = None,
    ) -> CalibrationValidation:
        import numpy as np

        now = now or datetime.now(UTC)
        reasons: list[str] = []
        status = CalibrationStatus.INVALID
        h = np.asarray(c.homography, dtype=float)
        if np.linalg.matrix_rank(h) != 3 or np.linalg.cond(h) > 1e12:
            return CalibrationValidation(
                status=status, reasons=("singular_or_ill_conditioned_homography",)
            )
        for points in (c.reference_points, c.validation_points):
            image = np.asarray([(p.image.x, p.image.y, 1) for p in points])
            world = np.asarray([(p.world.x, p.world.y, 1) for p in points])
            if np.linalg.matrix_rank(image) < 3 or np.linalg.matrix_rank(world) < 3:
                return CalibrationValidation(status=status, reasons=("collinear_references",))
        transform = RoadPlaneTransform(c)
        errors = []
        try:
            denominators = [
                h[2] @ (x, y, 1)
                for x, y in (
                    (0, 0),
                    (c.source_width, 0),
                    (0, c.source_height),
                    (c.source_width, c.source_height),
                )
            ]
            if min(denominators) * max(denominators) <= 0:
                raise ValueError("projective_horizon_in_image")
            for p in c.validation_points:
                result = transform.project(p.image, c.source_width, c.source_height)
                errors.append(math.hypot(result.x - p.world.x, result.y - p.world.y))
            for p in c.reference_points:
                result = transform.project(p.image, c.source_width, c.source_height)
                if (
                    math.hypot(result.x - p.world.x, result.y - p.world.y)
                    > c.metadata.maximum_point_error_m
                ):
                    reasons.append("fitting_residual_exceeded")
        except ValueError:
            return CalibrationValidation(status=status, reasons=("invalid_projection",))
        rmse = math.sqrt(sum(e * e for e in errors) / len(errors))
        maximum = max(errors)
        if rmse > c.metadata.maximum_rmse_m or maximum > c.metadata.maximum_point_error_m:
            reasons.append("validation_error_exceeded")
        if not math.isclose(rmse, c.metadata.validation_error_m, abs_tol=1e-6) or not math.isclose(
            maximum, c.metadata.validation_max_error_m, abs_tol=1e-6
        ):
            reasons.append("validation_metadata_mismatch")
        if camera_id != c.metadata.camera_id:
            reasons.append("camera_id_mismatch")
        if reasons:
            return CalibrationValidation(
                status=status, rmse_m=rmse, max_error_m=maximum, reasons=tuple(reasons)
            )
        age = (now - c.metadata.timestamp).total_seconds()
        if age < 0 or age > c.metadata.valid_for_seconds:
            return CalibrationValidation(
                status=CalibrationStatus.STALE,
                rmse_m=rmse,
                max_error_m=maximum,
                reasons=("calibration_expired",),
            )
        if not transform.dimensions_valid(width, height):
            return CalibrationValidation(
                status=CalibrationStatus.SUSPECT,
                rmse_m=rmse,
                max_error_m=maximum,
                reasons=("resolution_changed",),
            )
        return CalibrationValidation(
            status=CalibrationStatus.VALID, rmse_m=rmse, max_error_m=maximum
        )

    @staticmethod
    def fit(
        *,
        width: int,
        height: int,
        references: tuple[CalibrationReference, ...],
        validation: tuple[CalibrationReference, ...],
        metadata: CalibrationMetadata,
    ) -> CameraCalibration:
        import cv2
        import numpy as np

        if len(references) < 4:
            raise ValueError("At least four fitting points required")
        matrix, _ = cv2.findHomography(
            np.asarray([(p.image.x, p.image.y) for p in references], dtype=float),
            np.asarray([(p.world.x, p.world.y) for p in references], dtype=float),
            method=0,
        )
        if matrix is None:
            raise ValueError("Cannot fit planar homography")
        c = CameraCalibration(
            source_width=width,
            source_height=height,
            reference_points=references,
            validation_points=validation,
            homography=matrix.tolist(),
            metadata=metadata,
        )
        errors = []
        for p in validation:
            q = RoadPlaneTransform(c).project(p.image, width, height)
            errors.append(math.hypot(q.x - p.world.x, q.y - p.world.y))
        updated = metadata.model_copy(
            update={
                "validation_error_m": math.sqrt(sum(e * e for e in errors) / len(errors)),
                "validation_max_error_m": max(errors),
            }
        )
        c = c.model_copy(update={"metadata": updated})
        result = CalibrationValidator.validate(
            c, camera_id=metadata.camera_id, width=width, height=height, now=metadata.timestamp
        )
        if result.status != CalibrationStatus.VALID:
            raise ValueError(f"Calibration rejected: {result.reasons}")
        return c

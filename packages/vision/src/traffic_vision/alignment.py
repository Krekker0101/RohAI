"""Conservative alignment gate; absence of fresh independent evidence is SUSPECT."""

import math
from typing import Protocol

from pydantic import Field
from traffic_core.models import Model

from traffic_vision.calibration import CalibrationStatus, CameraCalibration
from traffic_vision.models import Point


class AlignmentEvidence(Model):
    camera_id: str
    calibration_version: str
    timestamp: float = Field(ge=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    # Correspondences must be independently identified STATIC landmarks, not vehicle tracks.
    reference: tuple[Point, ...] = Field(min_length=4, max_length=256)
    observed: tuple[Point, ...] = Field(min_length=4, max_length=256)
    source: str = Field(min_length=1)


class AlignmentResult(Model):
    status: CalibrationStatus
    reason: str
    maximum_displacement_px: float | None = None


class CameraAlignmentMonitor(Protocol):
    def check(
        self, calibration: CameraCalibration, timestamp: float, width: int, height: int
    ) -> AlignmentResult: ...


class LandmarkAlignmentMonitor:
    """Accepts upstream landmark observations; never adopts the first frame as a baseline.

    A failed check latches SUSPECT. Reverification requires a new monitor and evidence.
    No feature detector or perfect automatic movement detection is implied.
    """

    def __init__(self, *, maximum_displacement_px: float = 2, ttl_seconds: float = 0.5) -> None:
        if maximum_displacement_px <= 0 or ttl_seconds <= 0:
            raise ValueError("Alignment thresholds must be positive")
        self.maximum_displacement_px = maximum_displacement_px
        self.ttl_seconds = ttl_seconds
        self.evidence: AlignmentEvidence | None = None
        self.latched = False

    def submit(self, evidence: AlignmentEvidence) -> None:
        if self.evidence is not None and evidence.timestamp <= self.evidence.timestamp:
            raise ValueError("Alignment evidence must advance")
        self.evidence = evidence

    def check(
        self, calibration: CameraCalibration, timestamp: float, width: int, height: int
    ) -> AlignmentResult:
        e = self.evidence
        reason = "alignment_reverification_required"
        maximum = None
        if not self.latched and e is not None:
            if (
                e.camera_id != calibration.metadata.camera_id
                or e.calibration_version != calibration.metadata.version
            ):
                reason = "alignment_identity_mismatch"
            elif (e.width, e.height) != (width, height) or (width, height) != (
                calibration.source_width,
                calibration.source_height,
            ):
                reason = "alignment_resolution_changed"
            elif not 0 <= timestamp - e.timestamp <= self.ttl_seconds:
                reason = "alignment_evidence_stale"
            elif len(e.reference) != len(e.observed):
                reason = "alignment_correspondence_mismatch"
            else:
                import numpy as np

                ref = np.array([(p.x / width, p.y / height, 1) for p in e.reference])
                spread = np.ptp(ref[:, :2], axis=0)
                if np.linalg.matrix_rank(ref) < 3 or min(spread) < 0.5:
                    reason = "alignment_landmarks_insufficient_spread"
                elif any(
                    not (0 <= p.x <= width and 0 <= p.y <= height)
                    for p in (*e.reference, *e.observed)
                ):
                    reason = "alignment_landmark_outside_image"
                else:
                    maximum = max(
                        math.hypot(a.x - b.x, a.y - b.y)
                        for a, b in zip(e.reference, e.observed, strict=True)
                    )
                    if maximum <= self.maximum_displacement_px:
                        return AlignmentResult(
                            status=CalibrationStatus.VALID,
                            reason="static_landmarks_verified",
                            maximum_displacement_px=maximum,
                        )
                    reason = "camera_movement_detected"
        # Even evidence that resumes later cannot silently restore validity.
        self.latched = True
        return AlignmentResult(
            status=CalibrationStatus.SUSPECT, reason=reason, maximum_displacement_px=maximum
        )

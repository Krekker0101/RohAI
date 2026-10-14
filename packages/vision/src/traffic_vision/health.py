"""Conservative image/stage diagnostics; heuristics are not proof of visibility."""

from datetime import UTC, datetime

import cv2
import numpy as np
from numpy.typing import NDArray
from traffic_core.models import ObservationEvidence, ObservationStatus, Uncertainty

from traffic_vision.geometry import IntersectionGeometry, Polygon
from traffic_vision.settings import PerceptionHealthSettings
from traffic_vision.sources import Image, VideoFrame


class PerceptionHealthMonitor:
    def __init__(self, settings: PerceptionHealthSettings) -> None:
        self.settings = settings
        self.previous: dict[str, tuple[Image, float, float]] = {}
        self.baseline_texture: dict[str, float] = {}
        self.epoch: int | None = None

    def inspect(
        self,
        packet: VideoFrame,
        geometry: IntersectionGeometry,
        now: float,
        *,
        inference_alive: bool = True,
        analytics_alive: bool = True,
        camera_connected: bool = True,
        wall_time: datetime | None = None,
    ) -> dict[str, ObservationEvidence]:
        if self.epoch != packet.stream_epoch:
            self.previous.clear()
            self.baseline_texture.clear()
            self.epoch = packet.stream_epoch
        wall_time = wall_time or datetime.now(UTC)
        calibration = "missing"
        if geometry.camera_calibration is not None:
            from traffic_vision.calibration import CalibrationValidator

            calibration = CalibrationValidator.validate(
                geometry.camera_calibration,
                camera_id=packet.source_id,
                width=packet.frame.shape[1],
                height=packet.frame.shape[0],
                now=wall_time,
            ).status.value.lower()
        elif geometry.calibration_validated_at is not None:
            stamp = geometry.calibration_validated_at
            if stamp.tzinfo is None:
                calibration = "invalid"
            else:
                age = (wall_time - stamp).total_seconds()
                calibration = (
                    "valid" if 0 <= age <= geometry.calibration_valid_for_seconds else "stale"
                )
        age_ms = max(0, now - packet.captured_at) * 1000
        gray = np.asarray(cv2.cvtColor(packet.frame, cv2.COLOR_BGR2GRAY), dtype=np.uint8)
        gray = np.asarray(cv2.resize(gray, (320, 180)), dtype=np.uint8)
        # Every ROI uses the same image transforms. Compute these once per frame,
        # retaining the exact original pixels and blur/freeze thresholds.
        laplacian = np.asarray(cv2.Laplacian(gray, cv2.CV_64F), dtype=np.float64)
        small = np.asarray(cv2.resize(gray, (64, 64)), dtype=np.uint8)
        result = {}
        for approach in geometry.approaches:
            key = approach.direction.value
            # All calibrated task ROIs matter, including pedestrian waiting/crossing areas.
            zones = [approach.incoming, approach.queue_zone]
            zones.extend(
                z
                for z in (
                    approach.waiting_zone,
                    approach.crosswalk_zone,
                    approach.exit_zone,
                    approach.pedestrian_crossing,
                )
                if z is not None
            )
            checks: dict[str, bool | None] = {
                "camera_connected": camera_connected,
                "frame_fresh": age_ms <= self.settings.stale_seconds * 1000,
                "inference_alive": inference_alive,
                "analytics_alive": analytics_alive,
                "calibration_available": calibration != "missing",
                "calibration_fresh": calibration == "valid",
            }
            reasons: list[str] = []
            for index, zone in enumerate(zones):
                zone_checks = self._image_checks(
                    gray,
                    zone,
                    f"{key}:{index}",
                    packet.timestamp,
                    laplacian=laplacian,
                    small=small,
                )
                for name, value in zone_checks.items():
                    checks[name] = bool(checks.get(name, True)) and value
            if not approach.enabled:
                reasons.append("direction_not_covered")
            if calibration != "valid":
                reasons.append(f"calibration_{calibration}")
            if not all((approach.waiting_zone, approach.crosswalk_zone, approach.exit_zone)):
                reasons.append("pedestrian_calibration_incomplete")
            reasons.extend(name for name, value in checks.items() if value is False)
            status = ObservationStatus.OBSERVED
            if reasons:
                status = ObservationStatus.DEGRADED
            if not approach.enabled:
                status = ObservationStatus.UNOBSERVED
            if not checks["frame_fresh"]:
                status = ObservationStatus.STALE
            if not camera_connected or not inference_alive or not analytics_alive:
                status = ObservationStatus.FAILED
            confidence = 1.0 if status == ObservationStatus.OBSERVED else 0.0
            result[key] = ObservationEvidence(
                observation_status=status,
                measurement_timestamp=packet.timestamp,
                measurement_age_ms=age_ms,
                source_camera_ids=(packet.source_id,),
                camera_health=checks,
                visibility_score=confidence,
                detection_coverage=confidence,
                tracking_quality=1,
                calibration_status=calibration,
                measurement_confidence=confidence,
                failure_reasons=tuple(dict.fromkeys(reasons)),
                uncertainty=Uncertainty(observation_confidence=confidence),
            )
        return result

    def _image_checks(
        self,
        gray: Image,
        zone: Polygon,
        key: str,
        timestamp: float,
        *,
        laplacian: NDArray[np.float64] | None = None,
        small: Image | None = None,
    ) -> dict[str, bool]:
        height, width = gray.shape[:2]
        mask = np.zeros(gray.shape, dtype=np.uint8)
        points = np.asarray(
            [(p.x * (width - 1), p.y * (height - 1)) for p in zone.points], dtype=np.int32
        )
        cv2.fillPoly(mask, [points], 255)
        pixels = gray[mask != 0]
        visible = pixels.size >= 9
        if not visible:
            return {"roi_visible": False}
        if laplacian is None:
            laplacian = np.asarray(cv2.Laplacian(gray, cv2.CV_64F), dtype=np.float64)
        lap = laplacian[mask != 0]
        texture = float(lap.var())
        if small is None:
            small = np.asarray(cv2.resize(gray, (64, 64)), dtype=np.uint8)
        small_mask = cv2.resize(mask, (64, 64), interpolation=cv2.INTER_NEAREST) != 0
        signature = np.where(small_mask, small, 0).astype(np.uint8)
        previous = self.previous.get(key)
        since = timestamp
        fps_ok = False  # A single frame cannot establish processing cadence.
        if previous is not None:
            old, last, since = previous
            dt = timestamp - last
            fps_ok = 0 < dt <= 1 / self.settings.minimum_fps
            difference = float(
                np.abs(signature.astype(float) - old.astype(float))[small_mask].mean()
            )
            if difference > self.settings.frozen_difference:
                since = timestamp
        self.previous[key] = (signature, timestamp, since)
        frozen = timestamp - since >= self.settings.frozen_seconds
        baseline = self.baseline_texture.get(key)
        checks = {
            "frame_rate_adequate": fps_ok,
            "image_not_frozen": not frozen,
            "not_black": float(pixels.mean()) > self.settings.black_level,
            "not_underexposed": float((pixels < self.settings.underexposure_level).mean())
            < self.settings.bad_pixel_fraction,
            "not_overexposed": float((pixels > self.settings.overexposure_level).mean())
            < self.settings.bad_pixel_fraction,
            "not_blurred": texture >= self.settings.minimum_laplacian_variance,
            "no_major_occlusion_suspected": baseline is None
            or texture >= baseline * self.settings.occlusion_texture_ratio,
            "roi_visible": visible,
        }
        if all(v for k, v in checks.items() if k not in ("frame_rate_adequate",)):
            self.baseline_texture.setdefault(key, texture)
        return checks

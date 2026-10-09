from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from traffic_core.models import Model


class AnalyticsSettings(Model):
    coordinate_domain: Literal["road", "maket_relative"] = "road"
    slow_speed_px_per_second: float = Field(default=12, gt=0)
    motion_window_seconds: float = Field(default=0.5, gt=0, le=5)
    stop_confirmation_seconds: float = Field(default=0.2, gt=0)
    max_motion_gap_seconds: float = Field(default=1.5, gt=0)
    occlusion_hold_seconds: float = Field(default=1.5, gt=0)
    prediction_after_seconds: float = Field(default=0.5, ge=0)
    lost_uncertainty_seconds: float = Field(default=5, gt=0)
    id_recovery_distance_px: float = Field(default=25, gt=0)
    min_detection_confidence: float = Field(default=0.25, gt=0, le=1)
    jitter_tolerance_px: float = Field(default=2, ge=0)
    slow_frames: int = Field(default=3, ge=2, le=120)
    max_observation_gap_seconds: float = Field(default=0.75, gt=0, le=10)
    lost_track_seconds: float = Field(default=3, gt=0, le=60)
    trajectory_length: int = Field(default=32, ge=2, le=256)
    speed_smoothing: float = Field(default=0.6, gt=0, le=1)
    arrival_window_seconds: float = Field(default=60, ge=1)
    max_session_tracks: int = Field(default=100000, ge=10, le=1000000)
    waiting_score_per_second: float = Field(default=0.05, ge=0)
    moderate_queue: int = Field(default=5, ge=1)
    high_queue: int = Field(default=15, ge=2)

    @model_validator(mode="after")
    def coherent(self) -> "AnalyticsSettings":
        if self.lost_track_seconds < self.max_observation_gap_seconds:
            raise ValueError("Track TTL must cover the maximum observation gap")
        if self.high_queue <= self.moderate_queue:
            raise ValueError("high_queue must exceed moderate_queue")
        if self.lost_track_seconds < self.occlusion_hold_seconds:
            raise ValueError("Track lifetime must cover occlusion hold")
        if self.prediction_after_seconds > self.occlusion_hold_seconds:
            raise ValueError("Prediction delay must not exceed hold")
        return self


class PerceptionHealthSettings(Model):
    stale_seconds: float = Field(default=3, gt=0)
    minimum_fps: float = Field(default=0.8, gt=0)
    frozen_seconds: float = Field(default=3, gt=0)
    frozen_difference: float = Field(default=0.05, ge=0)
    black_level: float = Field(default=3, ge=0, le=255)
    underexposure_level: float = Field(default=12, ge=0, le=255)
    overexposure_level: float = Field(default=250, ge=0, le=255)
    bad_pixel_fraction: float = Field(default=0.98, gt=0, le=1)
    minimum_laplacian_variance: float = Field(default=2, ge=0)
    occlusion_texture_ratio: float = Field(default=0.15, gt=0, le=1)
    stage_timeout_seconds: float = Field(default=3, gt=0)


class VisionSettings(Model):
    deployment_domain: Literal["UNSPECIFIED", "PHYSICAL_MAKET", "REAL_ROAD"] = "UNSPECIFIED"
    source: Literal["builtin_camera", "usb_camera", "webcam", "file", "rtsp", "synthetic"] = (
        "synthetic"
    )
    camera_backend: Literal["AUTO", "DSHOW", "MSMF", "ANY"] = "AUTO"
    camera_width: int = Field(default=1280, ge=160, le=3840)
    camera_height: int = Field(default=720, ge=120, le=2160)
    camera_rotation: Literal[0, 90, 180, 270] = 0
    camera_mirror: bool = False
    camera_crop: tuple[float, float, float, float] = (0, 0, 1, 1)
    inference_hz: float | None = Field(default=None, gt=0, le=120)

    @model_validator(mode="after")
    def camera_transform(self) -> "VisionSettings":
        x, y, w, h = self.camera_crop
        if not (0 <= x < 1 and 0 <= y < 1 and w > 0 and h > 0 and x + w <= 1 and y + h <= 1):
            raise ValueError("Camera crop must be a normalized rectangle inside the frame")
        return self

    source_id: str = Field(default="demo", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    uri: str = ""
    webcam_index: int = Field(default=0, ge=0)
    geometry_path: Path = Path("configs/cameras/demo.json")
    calibration_directory: Path = Path("configs/cameras")
    manifest_path: Path = Path("configs/models/yolo26n.json")
    health: PerceptionHealthSettings = Field(default_factory=PerceptionHealthSettings)
    model_path: Path = Path("datasets/models/yolo26n.pt")
    model_package: Path | None = None
    model_registry: Path | None = None
    runtime_directory: Path = Path(".runtime")
    device: str = "cpu"
    image_size: int = Field(default=640, ge=256, le=1536, multiple_of=32)
    confidence: float = Field(default=0.1, ge=0.01, le=0.5)
    iou: float = Field(default=0.7, gt=0, le=1)
    max_detections: int = Field(default=200, ge=1, le=1000)
    torch_threads: int = Field(default=2, ge=1, le=32)
    tracker_reset_gap_seconds: float = Field(default=3, gt=0, le=60)
    capture_timeout_ms: int = Field(default=1500, ge=100, le=10000)
    camera_fps: float = Field(default=30, ge=1, le=120)
    reconnect_attempts: int = Field(default=3, ge=0, le=20)
    reconnect_delay_seconds: float = Field(default=0.5, ge=0.1, le=10)
    startup_timeout_seconds: float = Field(default=90, ge=5, le=300)
    stale_seconds: float = Field(default=3, ge=0.5, le=30)
    max_frame_age_seconds: float = Field(default=1.5, ge=0.1, le=30)
    shutdown_timeout_seconds: float = Field(default=5, ge=1, le=30)
    synthetic_frames: int = Field(default=600, ge=1, le=100000)
    synthetic_fps: float = Field(default=15, ge=1, le=60)
    jpeg_quality: int = Field(default=85, ge=40, le=100)
    debug_overlay: bool = False
    analytics: AnalyticsSettings = Field(default_factory=AnalyticsSettings)

    @model_validator(mode="after")
    def source_uri(self) -> "VisionSettings":
        if self.model_package is not None and self.model_registry is not None:
            raise ValueError("Select model_package or model_registry, not both")
        if self.source in ("file", "rtsp") and not self.uri:
            raise ValueError("File and RTSP sources require a uri")
        if self.source == "rtsp" and not self.uri.lower().startswith(("rtsp://", "rtsps://")):
            raise ValueError("RTSP source requires an rtsp:// or rtsps:// URI")
        return self

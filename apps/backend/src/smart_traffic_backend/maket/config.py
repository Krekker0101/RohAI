from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from traffic_core.models import Model
from traffic_core.runtime_modes import RuntimeMode
from traffic_core.settings import Timing
from traffic_vision.geometry import IntersectionGeometry
from traffic_vision.settings import VisionSettings


class MaketScaleConfig(Model):
    coordinate_unit: Literal["relative", "cm"] = "relative"
    model_width_cm: float | None = Field(default=None, gt=0)
    model_height_cm: float | None = Field(default=None, gt=0)
    road_equivalent_ratio: float | None = Field(default=None, gt=0)
    normalized_to_model_homography: (
        tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
        | None
    ) = None

    @model_validator(mode="after")
    def units(self) -> "MaketScaleConfig":
        if self.coordinate_unit == "cm" and (
            self.model_width_cm is None or self.model_height_cm is None
        ):
            raise ValueError("Centimeter coordinates require measured model dimensions")
        if self.normalized_to_model_homography is not None:
            if self.coordinate_unit != "cm":
                raise ValueError("Maket homography output unit must be explicit cm")
            a, b, c = self.normalized_to_model_homography
            determinant = (
                a[0] * (b[1] * c[2] - b[2] * c[1])
                - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0])
            )
            if abs(determinant) < 1e-12:
                raise ValueError("Singular maket homography")
        return self

    def model_point(self, x: float, y: float) -> tuple[float, float]:
        if self.coordinate_unit != "cm":
            return x, y
        matrix = self.normalized_to_model_homography
        if matrix is None:
            assert self.model_width_cm is not None and self.model_height_cm is not None
            return x * self.model_width_cm, y * self.model_height_cm
        denominator = matrix[2][0] * x + matrix[2][1] * y + matrix[2][2]
        if abs(denominator) < 1e-9:
            raise ValueError("Point on maket homography horizon")
        return (
            (matrix[0][0] * x + matrix[0][1] * y + matrix[0][2]) / denominator,
            (matrix[1][0] * x + matrix[1][1] * y + matrix[1][2]) / denominator,
        )


class MaketCalibration(Model):
    camera_signature: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    intersection_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    created_at: datetime
    orientation_degrees: float = Field(ge=0, lt=360)
    camera_pose_verified: bool = False
    scale: MaketScaleConfig = MaketScaleConfig()
    geometry: IntersectionGeometry
    reference_image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    max_alignment_shift_px: float = Field(default=8, gt=0, le=50)

    @model_validator(mode="after")
    def physical_units(self) -> "MaketCalibration":
        if self.created_at.tzinfo is None:
            raise ValueError("Calibration requires timezone")
        if self.geometry.road is not None or any(
            a.meters_per_pixel is not None for a in self.geometry.approaches
        ):
            raise ValueError("Road-meter geometry is not a maket centimeter calibration")
        return self


class SerialSettings(Model):
    port: str = Field(default="AUTO", min_length=1)
    baudrate: Literal[115200] = 115200
    timeout_seconds: float = Field(default=0.25, ge=0.05, le=0.5)
    ttl_ms: int = Field(default=1200, ge=500, le=2000)
    device_id: str = "rohai-maket-1"
    safe_mode: Literal["MAKET_ALL_RED"] = "MAKET_ALL_RED"
    independent_protection_verified: bool = False


class MaketConfig(Model):
    output_mode: Literal["CAMERA_ONLY", "PHYSICAL_OUTPUT"] = "CAMERA_ONLY"
    camera_selected: bool = True
    performance_preset: Literal["LOW", "BALANCED", "HIGH", "CUSTOM"] = "BALANCED"
    vision_profile: Literal["COCO_BASELINE", "MAKET_MODEL"] = "MAKET_MODEL"
    mode: Literal[RuntimeMode.PHYSICAL_MAKET] = RuntimeMode.PHYSICAL_MAKET
    camera: VisionSettings
    hardware: SerialSettings
    timing: Timing = Timing()
    calibration_path: Path = Path("configs/maket/calibration.json")
    reference_image_path: Path = Path("configs/maket/reference.png")
    session_directory: Path = Path(".runtime/maket/sessions")
    control_hz: float = Field(default=5, ge=1, le=10)
    policy_timeout_s: float = Field(default=0.1, gt=0, le=0.25)
    preview_hz: float = Field(default=10, ge=1, le=20)
    host: Literal["127.0.0.1"] = "127.0.0.1"
    port: int = Field(default=8001, ge=1024, le=65535)
    learned_package: Path | None = None
    operator_token_env: str = "ROHAI_MAKET_OPERATOR_TOKEN"
    max_recording_seconds: int = Field(default=1800, ge=1, le=7200)
    max_session_bytes: int = Field(default=536870912, ge=1048576)
    max_sessions: int = Field(default=20, ge=1, le=100)
    retention_days: int = Field(default=7, ge=1, le=365)

    @model_validator(mode="after")
    def domain(self) -> "MaketConfig":
        if self.camera.source not in ("webcam", "builtin_camera", "usb_camera", "rtsp"):
            raise ValueError("PHYSICAL_MAKET requires live built-in/USB/RTSP camera")
        expected = "UNSPECIFIED" if self.vision_profile == "COCO_BASELINE" else "PHYSICAL_MAKET"
        if self.camera.deployment_domain != expected:
            raise ValueError(f"Camera/model domain must be {expected} for {self.vision_profile}")
        if self.vision_profile == "COCO_BASELINE" and (
            self.camera.model_package is not None
            or self.camera.model_registry is not None
            or self.camera.manifest_path != Path("configs/models/yolo26n.json")
            or self.camera.model_path != Path("datasets/models/yolo26n.pt")
        ):
            raise ValueError("COCO baseline uses the pinned upstream manifest and weights only")
        if self.camera.analytics.coordinate_domain != "maket_relative":
            raise ValueError("Maket analytics require explicit relative coordinate domain")
        if (
            1000 / self.control_hz
            + self.policy_timeout_s * 1000
            + (self.hardware.timeout_seconds * 1000)
            >= self.hardware.ttl_ms
        ):
            raise ValueError("Control/ACK budget must fit device TTL")
        margin = 2 * (
            1 / self.control_hz + self.policy_timeout_s + self.hardware.timeout_seconds + 0.05
        )
        if self.timing.max_green_seconds - margin < max(
            self.timing.min_green_seconds,
            self.timing.pedestrian_green_seconds,
            self.timing.fixed_green_seconds,
        ):
            raise ValueError("Green budget lacks margin before independent device maximum")
        return self

    @classmethod
    def load(cls, path: Path) -> "MaketConfig":
        return cls.model_validate_json(path.read_text(encoding="utf-8"))

    def with_preset(self) -> "MaketConfig":
        if self.performance_preset == "CUSTOM":
            return self
        width, height, hz = {
            "LOW": (640, 480, 5),
            "BALANCED": (1280, 720, 10),
            "HIGH": (3840, 2160, 20),
        }[self.performance_preset]
        camera = self.camera.model_copy(
            update={"camera_width": width, "camera_height": height, "inference_hz": hz}
        )
        return self.model_copy(update={"camera": camera})

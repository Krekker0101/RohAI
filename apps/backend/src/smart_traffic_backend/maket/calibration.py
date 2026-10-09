"""Camera-bound, atomic calibration persistence; hypotheses never gain authority here."""

import hashlib
import json
import os
import tempfile
from pathlib import Path

from traffic_vision.settings import VisionSettings

from smart_traffic_backend.maket.config import MaketCalibration, MaketConfig


def camera_signature(settings: VisionSettings) -> str:
    fields = (
        "source",
        "source_id",
        "webcam_index",
        "uri",
        "camera_backend",
        "camera_width",
        "camera_height",
        "camera_rotation",
        "camera_mirror",
        "camera_crop",
    )
    data = settings.model_dump(mode="json")
    payload = json.dumps({key: data[key] for key in fields}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save_calibration(config: MaketConfig, calibration: MaketCalibration, reference: bytes) -> None:
    if not calibration.camera_pose_verified:
        raise ValueError("Explicit operator confirmation required")
    if calibration.camera_signature != camera_signature(config.camera):
        raise ValueError("Camera settings changed during calibration")
    if hashlib.sha256(reference).hexdigest() != calibration.reference_image_sha256:
        raise ValueError("Reference image hash mismatch")
    # JSON is the commit marker. An interrupted two-file update fails closed on hash mismatch.
    atomic_write(config.reference_image_path, reference)
    atomic_write(config.calibration_path, calibration.model_dump_json(indent=2).encode())

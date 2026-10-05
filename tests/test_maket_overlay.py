"""Runtime overlay provenance and live camera validation regressions."""

import time
from types import SimpleNamespace

import numpy as np
import pytest
from smart_traffic_backend.maket.runtime import MaketRuntime


def runtime():
    r = object.__new__(MaketRuntime)
    geometry = SimpleNamespace(source_id="live", reference_width=1280, reference_height=720)
    geometry.model_dump = lambda **kwargs: {"source_id": geometry.source_id}
    r.calibration = SimpleNamespace(camera_pose_verified=True, geometry=geometry)
    r.config = SimpleNamespace(camera=SimpleNamespace(source_id="live"))
    frame = np.zeros((720, 1280, 3), dtype=np.uint8)
    r.pipeline = SimpleNamespace(
        latest_capture=SimpleNamespace(frame=frame),
        latest_analysis=SimpleNamespace(video=SimpleNamespace(frame=frame)),
    )
    r.revalidation_required = False
    r.alignment = "VALID"
    r.alignment_updated_at = time.monotonic()
    r.preflight = lambda: {"calibration_complete": True}
    return r


@pytest.mark.parametrize(
    "reason", ["missing", "camera", "resolution", "pose", "moved", "stale", "incomplete"]
)
def test_unverified_runtime_never_exports_geometry(reason):
    r = runtime()
    if reason == "missing":
        r.calibration = None  # Analyzer template is deliberately irrelevant.
    elif reason == "camera":
        r.config.camera.source_id = "other"
    elif reason == "resolution":
        r.pipeline.latest_capture.frame = np.zeros((480, 640, 3), dtype=np.uint8)
    elif reason == "pose":
        r.calibration.camera_pose_verified = False
    elif reason == "moved":
        r.alignment = "SUSPECT_CAMERA_MOVED"
    elif reason == "stale":
        r.alignment_updated_at = time.monotonic() - 10
    elif reason == "incomplete":
        r.preflight = lambda: {"calibration_complete": False}
    assert r.overlay_calibration()["geometry"] is None
    assert r.overlay_calibration()["state"] == (
        "NO_CALIBRATION" if reason == "missing" else "CALIBRATION_INVALID"
    )


def test_verified_runtime_exports_only_saved_geometry():
    r = runtime()
    assert r.overlay_calibration() == {
        "state": "CALIBRATED",
        "source_id": "live",
        "geometry": {"source_id": "live"},
    }

from pathlib import Path

import cv2
import numpy as np
from traffic_vision.geometry import load_geometry
from traffic_vision.health import PerceptionHealthMonitor
from traffic_vision.settings import PerceptionHealthSettings

from scripts.benchmark_health import benchmark


def test_shared_transforms_match_reference_for_exposure_freeze_and_motion() -> None:
    geometry = load_geometry(Path("configs/cameras/demo.json"))
    reference = PerceptionHealthMonitor(PerceptionHealthSettings())
    optimized = PerceptionHealthMonitor(PerceptionHealthSettings())
    rng = np.random.default_rng(42)
    noise = rng.integers(0, 256, (180, 320), dtype=np.uint8)
    frames = [
        noise,
        noise,
        noise,
        np.zeros_like(noise),
        np.full_like(noise, 255),
        rng.integers(0, 256, noise.shape, dtype=np.uint8),
    ]
    for index, gray in enumerate(frames):
        laplacian = np.asarray(cv2.Laplacian(gray, cv2.CV_64F), dtype=np.float64)
        small = np.asarray(cv2.resize(gray, (64, 64)), dtype=np.uint8)
        for approach in geometry.approaches:
            zone = approach.incoming
            key = approach.direction.value
            assert reference._image_checks(gray, zone, key, index * 3.0) == optimized._image_checks(
                gray, zone, key, index * 3.0, laplacian=laplacian, small=small
            )
    assert benchmark(iterations=2)["identical_checks"] is True

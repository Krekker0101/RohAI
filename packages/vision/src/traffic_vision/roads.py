"""Verified segmentation with explicitly labelled aerial surface refinement."""

import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray


class RoadSegmenter:
    name = "NVIDIA SegFormer-B0 / Cityscapes"

    def __init__(self, directory: Path, manifest: Path, aerial: bool = False) -> None:
        self.directory, self.manifest = directory, manifest
        self.model: Any = None
        self.processor: Any = None
        self.road_id: int | None = None
        self.revision: str | None = None
        self.aerial = aerial
        self.method = "semantic segmentation"
        if aerial:
            self.method = "semantic seed + connected surface heuristic (experimental aerial)"

    def open(self) -> None:
        if not self.directory.is_dir():
            raise ValueError("ROAD_MODEL_MISSING")
        receipt = json.loads(self.manifest.read_text(encoding="utf-8"))
        for name in ("config.json", "preprocessor_config.json", "model.safetensors"):
            path = self.directory / name
            if path.is_symlink():
                raise ValueError("ROAD_MODEL_INTEGRITY")
            with path.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            if digest != receipt["files"][name]:
                raise ValueError("ROAD_MODEL_INTEGRITY")
        try:
            from transformers import AutoImageProcessor, SegformerForSemanticSegmentation
        except ImportError as exc:
            raise ValueError("ROAD_DEPENDENCIES_MISSING") from exc

        processor_factory: Any = AutoImageProcessor
        self.processor = processor_factory.from_pretrained(
            str(self.directory),
            local_files_only=True,
            trust_remote_code=False,
            backend="pil",
        )
        self.model = SegformerForSemanticSegmentation.from_pretrained(
            str(self.directory),
            local_files_only=True,
            trust_remote_code=False,
            use_safetensors=True,
        )
        self.model.eval()
        candidates = [
            int(key)
            for key, name in self.model.config.id2label.items()
            if name.strip().lower() == "road"
        ]
        if len(candidates) != 1:
            raise ValueError("ROAD_CLASS_MAPPING")
        self.road_id = candidates[0]
        self.revision = receipt["revision"]

    def predict(self, image: NDArray[np.uint8]) -> NDArray[np.uint8]:
        if self.model is None or self.road_id is None:
            raise RuntimeError("Road segmenter is not open")
        import torch

        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        inputs = self.processor(images=rgb, return_tensors="pt")
        with torch.inference_mode():
            probabilities = self.model(**inputs).logits.softmax(dim=1)
            mask = (
                (
                    (probabilities.argmax(dim=1) == self.road_id)
                    & (probabilities[:, self.road_id] >= 0.35)
                )[0]
                .cpu()
                .numpy()
            )
        height, width = image.shape[:2]
        semantic = np.asarray(
            cv2.resize(
                mask.astype(np.uint8) * 255, (width, height), interpolation=cv2.INTER_NEAREST
            ),
            dtype=np.uint8,
        )
        return refine_aerial_roads(image, semantic) if self.aerial else semantic

    def close(self) -> None:
        self.model = self.processor = None


def refine_aerial_roads(image: NDArray[np.uint8], semantic: NDArray[np.uint8]) -> NDArray[np.uint8]:
    # Explicit heuristic for distant overhead imagery: extend a neural road seed
    # through the largest connected asphalt-like surface, without inventing a
    # fallback when the network finds no road. This is not a trained aerial model.
    height, width = image.shape[:2]
    scale = min(1.0, 960 / width)
    size = (round(width * scale), round(height * scale))
    small = cv2.resize(image, size)
    seed = cv2.resize(semantic, size, interpolation=cv2.INTER_NEAREST)
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    channels = small.astype(np.int16)
    shadow_asphalt = (
        (hsv[:, :, 0] >= 85)
        & (hsv[:, :, 0] <= 125)
        & (hsv[:, :, 1] < 180)
        & (hsv[:, :, 2] > 15)
        & (hsv[:, :, 2] < 100)
    )
    candidate = (
        (channels[:, :, 0] - channels[:, :, 2] >= 5)
        & (channels[:, :, 1] >= channels[:, :, 2])
        & (hsv[:, :, 1] < 75)
        & (hsv[:, :, 2] > 20)
        & (hsv[:, :, 2] < 170)
    ).astype(np.uint8) * 255
    candidate = np.asarray(
        cv2.morphologyEx(candidate, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8)), dtype=np.uint8
    )
    count, labels, stats, _ = cv2.connectedComponentsWithStats(candidate)
    eligible = [
        i
        for i in range(1, count)
        if stats[i, cv2.CC_STAT_AREA] > candidate.size * 0.015
        and np.count_nonzero((labels == i) & (seed > 0)) > stats[i, cv2.CC_STAT_AREA] * 0.01
    ]
    if not eligible:
        return semantic
    largest = max(eligible, key=lambda index: stats[index, cv2.CC_STAT_AREA])
    mask = (labels == largest).astype(np.uint8) * 255
    # Bounded geodesic growth recovers adjacent dark asphalt without admitting
    # disconnected roofs/parking lots selected by a relaxed global color threshold.
    relaxed = (
        shadow_asphalt
        | (
            (channels[:, :, 0] >= channels[:, :, 2])
            & (channels[:, :, 1] >= channels[:, :, 2] - 2)
            & (hsv[:, :, 1] < 85)
            & (hsv[:, :, 2] > 15)
            & (hsv[:, :, 2] < 170)
        )
    ).astype(np.uint8) * 255
    relaxed = np.asarray(
        cv2.morphologyEx(relaxed, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)), dtype=np.uint8
    )
    kernel = np.ones((3, 3), np.uint8)
    for _ in range(24):
        grown = cv2.bitwise_or(mask, cv2.bitwise_and(cv2.dilate(mask, kernel), relaxed))
        if np.array_equal(grown, mask):
            break
        mask = np.asarray(grown, dtype=np.uint8)
    # Fill enclosed vehicle/paint gaps, preserving large islands and exterior land.
    holes_count, holes, hole_stats, _ = cv2.connectedComponentsWithStats(255 - mask)
    border_labels = set(np.concatenate((holes[0], holes[-1], holes[:, 0], holes[:, -1])))
    maximum_hole = max(9, round(mask.size * 0.0006))
    for index in range(1, holes_count):
        if index not in border_labels and hole_stats[index, cv2.CC_STAT_AREA] <= maximum_hole:
            mask[holes == index] = 255
    return np.asarray(
        cv2.resize(mask, (width, height), interpolation=cv2.INTER_NEAREST), dtype=np.uint8
    )


def overlay_roads(image: NDArray[np.uint8], mask: NDArray[np.uint8]) -> NDArray[np.uint8]:
    if mask.shape != image.shape[:2]:
        raise ValueError("Road mask shape must match image")
    result = image.copy()
    pixels = mask > 0
    tint = np.empty_like(image)
    tint[:] = (220, 190, 35)
    blended = cv2.addWeighted(image, 0.7, tint, 0.3, 0)
    result[pixels] = blended[pixels]
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    cv2.drawContours(result, contours, -1, (240, 220, 60), 2, cv2.LINE_AA)
    return result

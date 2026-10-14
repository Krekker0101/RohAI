import json
from pathlib import Path

import numpy as np
import pytest
from traffic_vision.roads import RoadSegmenter, overlay_roads, refine_aerial_roads


def test_overlay_preserves_pixels_outside_mask_and_does_not_mutate_input() -> None:
    image = np.full((80, 100, 3), 70, dtype=np.uint8)
    mask = np.zeros((80, 100), dtype=np.uint8)
    mask[20:60, 20:80] = 255
    result = overlay_roads(image, mask)
    assert np.all(image == 70)
    assert np.array_equal(result[:10], image[:10])
    assert not np.array_equal(result[40, 50], image[40, 50])
    with pytest.raises(ValueError, match="shape"):
        overlay_roads(image, mask[:20])


def test_no_network_road_seed_does_not_invent_a_surface() -> None:
    image = np.full((200, 300, 3), [90, 85, 75], dtype=np.uint8)
    empty = np.zeros((200, 300), dtype=np.uint8)
    assert not np.any(refine_aerial_roads(image, empty))


def test_aerial_refinement_keeps_connected_seeded_road_not_separate_roof() -> None:
    image = np.full((200, 300, 3), [45, 65, 90], dtype=np.uint8)
    image[80:120, :] = [90, 85, 75]
    image[145:175, 210:260] = [90, 85, 75]
    seed = np.zeros((200, 300), dtype=np.uint8)
    seed[90:110, 30:70] = 255
    mask = refine_aerial_roads(image, seed)
    assert mask[100, 200] == 255 and mask[160, 235] == 0


def test_integrity_failure_happens_before_native_model_loading(tmp_path: Path) -> None:
    model = tmp_path / "model"
    model.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"files": {"config.json": "0" * 64}}))
    (model / "config.json").write_text("tampered")
    with pytest.raises(ValueError, match="ROAD_MODEL_INTEGRITY"):
        RoadSegmenter(model, manifest).open()


def test_real_pretrained_model_outputs_bounded_binary_mask() -> None:
    directory = Path("datasets/models/road-segformer-city")
    if not (directory / "model.safetensors").is_file():
        pytest.skip("Official road checkpoint not installed; run fetch_road_model.py")
    pytest.importorskip("transformers")
    import torch

    model = RoadSegmenter(directory, Path("configs/models/road-segformer-city.json"))
    try:
        model.open()
        torch.set_num_threads(2)
        image = np.full((80, 100, 3), 100, dtype=np.uint8)
        mask = model.predict(image)
        assert mask.shape == image.shape[:2] and mask.dtype == np.uint8
        assert set(np.unique(mask)).issubset({0, 255})
    finally:
        model.close()


def test_refinement_fills_vehicle_gap_but_preserves_large_median() -> None:
    image = np.full((200, 300, 3), [45, 65, 90], dtype=np.uint8)
    image[40:160, 20:280] = [90, 85, 75]
    image[90:93, 100:103] = [10, 10, 10]
    image[70:130, 160:200] = [45, 65, 90]
    seed = np.zeros((200, 300), dtype=np.uint8)
    seed[50:65, 30:80] = 255
    mask = refine_aerial_roads(image, seed)
    assert mask[91, 101] == 255
    assert mask[100, 180] == 0


def test_refinement_recovers_adjacent_asphalt_without_reaching_distant_roof() -> None:
    image = np.full((200, 300, 3), [45, 65, 90], dtype=np.uint8)
    image[80:120, :] = [90, 85, 75]
    image[80:120, 110:120] = [78, 78, 78]
    image[145:175, 210:260] = [78, 78, 78]
    seed = np.zeros((200, 300), dtype=np.uint8)
    seed[90:110, 30:70] = 255
    mask = refine_aerial_roads(image, seed)
    assert mask[100, 115] == 255
    assert mask[160, 235] == 0


def test_shadow_asphalt_is_recovered_only_near_seeded_road() -> None:
    image = np.full((200, 300, 3), [45, 65, 90], dtype=np.uint8)
    image[80:120, :] = [90, 85, 75]
    image[80:120, 100:120] = [47, 42, 22]
    image[145:175, 210:260] = [47, 42, 22]
    seed = np.zeros((200, 300), dtype=np.uint8)
    seed[90:110, 30:70] = 255
    mask = refine_aerial_roads(image, seed)
    assert mask[100, 110] == 255
    assert mask[160, 235] == 0

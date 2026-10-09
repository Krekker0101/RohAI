import json
from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np
import pytest
from traffic_core.models import ObjectKind
from traffic_vision.dataset.files import write_new_json
from traffic_vision.dataset.ingest import ImportSpec, MediaSource, import_media
from traffic_vision.dataset.labels import export_coco, export_yolo, import_coco, import_yolo, review
from traffic_vision.dataset.schema import Conditions, DatasetSpec, UsageRights
from traffic_vision.evaluation.metrics import Prediction, PredictionFrame, tracking_metrics
from traffic_vision.training.package import seal_package


def test_yolo_review_coco_roundtrip() -> None:
    classes = {0: ObjectKind.CAR}
    proposal = import_yolo("0 .5 .5 .25 .5", frame_id="f", width=100, height=100, classes=classes)
    assert proposal.provenance == "model_predictions" and not proposal.exhaustive_classes
    with pytest.raises(ValueError):
        review(
            proposal, reviewer=" ", exhaustive_classes=(0,), width=100, height=100, classes=classes
        )
    reviewed = review(
        proposal,
        reviewer="TEST_REVIEWER",
        exhaustive_classes=(0,),
        width=100,
        height=100,
        classes=classes,
    )
    assert reviewed.reviewed_by == "TEST_REVIEWER"
    coco = export_coco(
        reviewed, image_id=7, file_name="f.png", width=100, height=100, classes=classes
    )
    restored = import_coco(
        coco,
        image_id=7,
        frame_id="f",
        width=100,
        height=100,
        classes=classes,
        category_mapping={0: 0},
    )
    assert restored.provenance == "model_predictions"
    assert export_yolo(restored, width=100, height=100, classes=classes) == "0 0.5 0.5 0.25 0.5\n"


@pytest.mark.parametrize(
    "label", ["0 nan .5 .1 .1", "2 .5 .5 .1 .1", "0 .9 .5 .9 .2", "0 .5 .5 -.2 .1", "0 .5 .5 .1"]
)
def test_invalid_proposals(label: str) -> None:
    with pytest.raises(ValueError):
        import_yolo(label, frame_id="f", width=100, height=100, classes={0: ObjectKind.CAR})


def test_image_import_preserves_lineage_and_review_gate(tmp_path: Path) -> None:
    assert cv2.imwrite(str(tmp_path / "owned.png"), np.zeros((48, 64, 3), dtype=np.uint8))
    (tmp_path / "label.txt").write_text("0 .5 .5 .25 .5", encoding="utf-8")
    source = MediaSource(
        source_id="s",
        recording_id="r",
        camera_id="c",
        intersection_id="i",
        kind="images",
        paths=("owned.png",),
        captured_at=datetime.now(UTC),
        conditions=Conditions(
            day_night="unknown",
            weather="unknown",
            traffic_density="unknown",
            occlusion="unknown",
            camera_angle="unknown",
            lighting="unknown",
        ),
        scene_domain="PHYSICAL_MAKET",
        provenance="TEST_ONLY",
        labels={0: "label.txt"},
        rights=UsageRights(
            license_id="TEST_ONLY",
            owner="TEST",
            source_reference="generated unit fixture",
            training_allowed=True,
            privacy_reviewed=True,
            reviewed_by="TEST",
            evidence="unit-test metadata only",
        ),
    )
    spec = ImportSpec(
        dataset_id="d", version="v1", class_mapping={0: ObjectKind.CAR}, sources=(source,)
    )
    path = tmp_path / "import.json"
    write_new_json(path, spec.model_dump(mode="json"))
    draft = import_media(path, tmp_path / "draft")
    dataset = DatasetSpec.model_validate_json(draft.read_text(encoding="utf-8"))
    lineage = json.loads(dataset.sources[0].provenance)["lineage"]
    assert len(lineage["original_assets"][0]["sha256"]) == 64
    assert dataset.sources[0].media_kind == "image_sequence"
    assert dataset.frames[0].sequence_id == "s"
    annotation = json.loads((draft.parent / dataset.frames[0].annotation.path).read_text())
    assert annotation["provenance"] == "model_predictions"
    with pytest.raises(FileExistsError):
        import_media(path, draft.parent)
    forbidden = spec.model_copy(
        update={
            "sources": (
                source.model_copy(
                    update={"rights": source.rights.model_copy(update={"training_allowed": False})}
                ),
            )
        }
    )
    other = tmp_path / "forbidden.json"
    write_new_json(other, forbidden.model_dump(mode="json"))
    with pytest.raises(PermissionError):
        import_media(other, tmp_path / "forbidden-draft")
    assert not (tmp_path / "forbidden-draft").exists()


def test_hota_identity_fragmentation() -> None:
    proposal = import_yolo(
        "0 .5 .5 .25 .5", frame_id="f", width=100, height=100, classes={0: ObjectKind.CAR}
    )
    annotation = proposal.model_copy(
        update={
            "exhaustive_classes": (0,),
            "objects": (proposal.objects[0].model_copy(update={"track_id": "truth"}),),
        }
    )

    def frame(identity: str) -> PredictionFrame:
        prediction = Prediction(
            bbox=annotation.objects[0].bbox, class_id=0, confidence=1, track_id=identity
        )
        return PredictionFrame(frame_id="f", detections=(prediction,), tracks=(prediction,))

    perfect = tracking_metrics((("s", annotation, frame("a")), ("s", annotation, frame("a"))), (0,))
    fragmented = tracking_metrics(
        (("s", annotation, frame("a")), ("s", annotation, frame("b"))), (0,)
    )
    assert perfect["HOTA"] == pytest.approx(1)
    assert fragmented["HOTA"] == pytest.approx(2**-0.5)
    assert fragmented["IDF1"] == pytest.approx(0.5)


def test_fixture_checkpoint_never_packaged(tmp_path: Path) -> None:
    from traffic_vision.dataset.files import canonical_hash
    from traffic_vision.training.config import TrainingConfig

    config = TrainingConfig(
        purpose="synthetic_fixture",
        experiment="test",
        dataset_release=tmp_path,
        dataset_version="v1",
        dataset_hash="0" * 64,
        baseline=tmp_path / "baseline.json",
    ).model_dump(mode="json")
    manifest = {"status": "COMPLETED", "training_config": config}
    write_new_json(
        tmp_path / "manifest.json", {**manifest, "report_sha256": canonical_hash(manifest)}
    )
    with pytest.raises(PermissionError):
        seal_package(tmp_path, tmp_path / "package")

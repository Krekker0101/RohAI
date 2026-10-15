import pytest
from traffic_vision.dataset.schema import FrameAnnotation
from traffic_vision.evaluation.metrics import PredictionFrame

from scripts.evaluate_review import score


def truth() -> FrameAnnotation:
    return FrameAnnotation.model_validate(
        {
            "frame_id": "frame-1",
            "layers": ["detection"],
            "exhaustive_classes": list(range(6)),
            "provenance": "human",
            "reviewed_by": "UNIT_FIXTURE",
            "objects": [
                {
                    "annotation_id": "small",
                    "class_id": 0,
                    "domain_class": "car",
                    "bbox": {"x1": 0, "y1": 0, "x2": 10, "y2": 10},
                },
                {
                    "annotation_id": "large",
                    "class_id": 0,
                    "domain_class": "car",
                    "bbox": {"x1": 20, "y1": 20, "x2": 70, "y2": 70},
                },
            ],
        }
    )


def test_review_counts_misses_false_positives_and_duplicate_predictions() -> None:
    annotation = truth()
    prediction = PredictionFrame.model_validate(
        {
            "frame_id": "frame-1",
            "detections": [
                {"class_id": 0, "confidence": 0.9, "bbox": annotation.objects[0].bbox.model_dump()},
                {"class_id": 0, "confidence": 0.8, "bbox": annotation.objects[0].bbox.model_dump()},
                {
                    "class_id": 0,
                    "confidence": 0.7,
                    "bbox": {"x1": 80, "y1": 80, "x2": 90, "y2": 90},
                },
            ],
        }
    )
    report = score({"frame-1": annotation}, {"frame-1": prediction}, 0.25)
    metrics = report["detection"]["per_class"]["0"]
    assert (metrics["tp"], metrics["fp"], metrics["fn"]) == (1, 2, 1)
    assert metrics["recall"] == 0.5
    assert metrics["precision"] == pytest.approx(1 / 3)
    assert report["small_objects"]["per_class"]["0"]["fn"] == 0
    assert report["independent_test"] is False


def test_unlabeled_review_never_claims_accuracy() -> None:
    assert score({}, {}, 0.25)["status"] == "UNSCORED"


def test_missing_predictions_and_model_labels_are_rejected() -> None:
    annotation = truth()
    with pytest.raises(ValueError, match="Missing predictions"):
        score({"frame-1": annotation}, {}, 0.25)
    prediction = PredictionFrame(frame_id="frame-1", detections=())
    annotation = annotation.model_copy(update={"provenance": "model_predictions"})
    with pytest.raises(ValueError, match="human"):
        score({"frame-1": annotation}, {"frame-1": prediction}, 0.25)

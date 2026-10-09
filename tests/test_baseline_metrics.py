import itertools

import pytest
from traffic_core.models import ObjectKind
from traffic_vision.contracts import BoundingBox
from traffic_vision.dataset.schema import FrameAnnotation, Layer, ObjectAnnotation
from traffic_vision.evaluation.metrics import (
    Prediction,
    PredictionFrame,
    assignment,
    detection_metrics,
    tracking_metrics,
)


def annotation(frame: str = "f", *, ignore: bool = False) -> FrameAnnotation:
    return FrameAnnotation(
        frame_id=frame,
        layers=(Layer.DETECTION, Layer.TRACKING),
        exhaustive_classes=(0, 1),
        provenance="synthetic_fixture",
        objects=(
            ObjectAnnotation(
                annotation_id="obj",
                bbox=BoundingBox(x1=0, y1=0, x2=10, y2=10),
                class_id=0,
                domain_class=ObjectKind.CAR,
                track_id="truth",
                ignore=ignore,
            ),
        ),
    )


def pred(
    *, width: float = 10, confidence: float = 0.9, class_id: int = 0, identity: str = "pred"
) -> Prediction:
    return Prediction(
        bbox=BoundingBox(x1=0, y1=0, x2=width, y2=10),
        class_id=class_id,
        confidence=confidence,
        track_id=identity,
    )


def evaluate(*predictions: Prediction) -> dict[str, object]:
    return detection_metrics(
        {"f": annotation()}, {"f": PredictionFrame(frame_id="f", detections=predictions)}, (0, 1)
    )


def test_perfect_detection_and_absent_class_undefined() -> None:
    result = evaluate(pred())
    assert result["mAP50"] == result["mAP50_95"] == result["precision"] == result["recall"] == 1
    detailed = detection_metrics(
        {"f": annotation()}, {"f": PredictionFrame(frame_id="f", detections=(pred(),))}, (0, 1)
    )
    assert detailed["per_class"]["1"]["AP50"] is None


def test_localization_ap_iou_sweep() -> None:
    result = evaluate(pred(width=8))
    assert result["mAP50"] == 1
    assert result["mAP50_95"] == pytest.approx(0.7)


def test_duplicate_and_high_confidence_false_positive() -> None:
    duplicate = evaluate(pred(), pred(confidence=0.8))
    assert duplicate["precision"] == 0.5 and duplicate["recall"] == 1
    assert duplicate["mAP50"] == 1
    false_positive = pred(confidence=0.99).model_copy(
        update={"bbox": BoundingBox(x1=20, y1=20, x2=30, y2=30)}
    )
    result = evaluate(false_positive, pred())
    assert result["mAP50"] == pytest.approx(0.5)


def test_wrong_class_confusion_is_not_a_true_positive() -> None:
    report = detection_metrics(
        {"f": annotation()},
        {"f": PredictionFrame(frame_id="f", detections=(pred(class_id=1),))},
        (0, 1),
    )
    assert report["recall"] == report["precision"] == 0
    assert report["confusion_matrix"]["rows_true_columns_predicted"]["0"]["1"] == 1


def test_unknown_annotation_coverage_not_negative() -> None:
    truth = annotation().model_copy(update={"exhaustive_classes": (1,)})
    report = detection_metrics(
        {"f": truth}, {"f": PredictionFrame(frame_id="f", detections=(pred(),))}, (0, 1)
    )
    assert report["ground_truth"] == report["fp"] == 0
    assert report["mAP50"] is None


def test_ignored_objects_and_explicit_empty_predictions() -> None:
    report = detection_metrics(
        {"f": annotation(ignore=True)},
        {"f": PredictionFrame(frame_id="f", detections=(pred(),))},
        (0, 1),
    )
    assert report["fp"] == report["ground_truth"] == 0
    assert evaluate()["recall"] == 0
    with pytest.raises(ValueError, match="cover"):
        detection_metrics({"f": annotation()}, {}, (0, 1))


@pytest.mark.parametrize(
    "weights",
    [
        [[1.0, 4.0, 2.0], [3.0, 1.0, 2.0]],
        [[3.0, 1.0], [2.0, 4.0], [0.0, 5.0]],
        [[0.0, 0.0], [0.0, 0.0]],
        [[1.0, 1.0], [1.0, 1.0]],
    ],
)
def test_assignment_matches_brute_force(weights: list[list[float]]) -> None:
    matrix = (
        weights
        if len(weights) <= len(weights[0])
        else [list(row) for row in zip(*weights, strict=True)]
    )
    optimum = max(
        sum(matrix[i][j] for i, j in enumerate(columns))
        for columns in itertools.permutations(range(len(matrix[0])), len(matrix))
    )
    pairs = assignment(weights)
    assert sum(weights[i][j] for i, j in pairs) == optimum
    assert len({i for i, _ in pairs}) == len({j for _, j in pairs}) == len(pairs)


def test_tracking_global_idf1_switch_and_fragmentation() -> None:
    switched = tuple(
        (
            "s",
            annotation(str(i)),
            PredictionFrame(
                frame_id=str(i), detections=(), tracks=(pred(identity="a" if i < 2 else "b"),)
            ),
        )
        for i in range(4)
    )
    report = tracking_metrics(switched, (0, 1))
    assert report["IDF1"] == 0.5
    assert report["id_switches"] == 1 and report["fragmentations"] == 0
    fragmented = tuple(
        (
            "s",
            annotation(str(i)),
            PredictionFrame(frame_id=str(i), detections=(), tracks=() if i == 1 else (pred(),)),
        )
        for i in range(3)
    )
    result = tracking_metrics(fragmented, (0, 1))
    assert result["IDF1"] == pytest.approx(0.8)
    assert result["fragmentations"] == result["false_negatives"] == 1
    assert result["HOTA_status"] == "MEASURED"
    assert 0 <= result["HOTA"] <= 1


def test_sequences_do_not_share_identities() -> None:
    frames = tuple(
        (
            f"sequence{i}",
            annotation(str(i)),
            PredictionFrame(frame_id=str(i), detections=(), tracks=(pred(identity=f"p{i}"),)),
        )
        for i in range(2)
    )
    result = tracking_metrics(frames, (0, 1))
    assert result["IDF1"] == 1 and result["id_switches"] == 0


def test_untracked_proposal_is_not_a_reidentified_vehicle() -> None:
    result = tracking_metrics(
        (
            (
                "s",
                annotation(),
                PredictionFrame(
                    frame_id="f",
                    detections=(),
                    tracks=(pred().model_copy(update={"track_id": None}),),
                ),
            ),
        ),
        (0, 1),
    )
    assert result["IDF1"] == 0
    assert result["false_negatives"] == 1

"""Explicit bbox AP protocol and global identity association, independently testable."""

from bisect import bisect_left
from collections import Counter, defaultdict
from typing import Any

from pydantic import Field
from traffic_core.models import Model

from traffic_vision.contracts import BoundingBox
from traffic_vision.dataset.schema import FrameAnnotation


class Prediction(Model):
    bbox: BoundingBox
    class_id: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    track_id: str | None = None


class PredictionFrame(Model):
    frame_id: str
    detections: tuple[Prediction, ...]
    tracks: tuple[Prediction, ...] = ()


def iou(a: BoundingBox, b: BoundingBox) -> float:
    overlap = max(0, min(a.x2, b.x2) - max(a.x1, b.x1)) * max(0, min(a.y2, b.y2) - max(a.y1, b.y1))
    union = (a.x2 - a.x1) * (a.y2 - a.y1) + (b.x2 - b.x1) * (b.y2 - b.y1) - overlap
    return overlap / union if union else 0


def assignment(weights: list[list[float]]) -> list[tuple[int, int]]:
    if not weights or not weights[0]:
        return []
    # Rectangular Hungarian algorithm. No optional MLOps/matching package required.
    transposed = len(weights) > len(weights[0])
    matrix = [list(row) for row in zip(*weights, strict=True)] if transposed else weights
    n, m = len(matrix), len(matrix[0])
    u, v = [0.0] * (n + 1), [0.0] * (m + 1)
    p, way = [0] * (m + 1), [0] * (m + 1)
    for row in range(1, n + 1):
        p[0] = row
        minimum = [float("inf")] * (m + 1)
        used = [False] * (m + 1)
        column = 0
        while True:
            used[column] = True
            current = p[column]
            delta, next_column = float("inf"), 0
            for j in range(1, m + 1):
                if used[j]:
                    continue
                cost = -matrix[current - 1][j - 1] - u[current] - v[j]
                if cost < minimum[j]:
                    minimum[j], way[j] = cost, column
                if minimum[j] < delta:
                    delta, next_column = minimum[j], j
            for j in range(m + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minimum[j] -= delta
            column = next_column
            if p[column] == 0:
                break
        while column:
            previous = way[column]
            p[column] = p[previous]
            column = previous
    pairs = [(p[j] - 1, j - 1) for j in range(1, m + 1) if p[j] and matrix[p[j] - 1][j - 1] > 0]
    return [(j, i) for i, j in pairs] if transposed else pairs


def detection_metrics(
    truth: dict[str, FrameAnnotation],
    predictions: dict[str, PredictionFrame],
    class_ids: tuple[int, ...],
    *,
    operating_confidence: float = 0.1,
) -> dict[str, Any]:
    if set(truth) != set(predictions):
        raise ValueError(
            "Predictions must explicitly cover each evaluated frame, including empty frames"
        )
    per_class: dict[str, dict[str, Any]] = {}
    total_tp = total_fp = total_gt = 0
    for class_id in class_ids:
        eligible = {key: ann for key, ann in truth.items() if class_id in ann.exhaustive_classes}
        ground = {
            key: [o for o in ann.objects if o.class_id == class_id and not o.ignore]
            for key, ann in eligible.items()
        }
        ignored = {
            key: [o for o in ann.objects if o.class_id == class_id and o.ignore]
            for key, ann in eligible.items()
        }
        n_gt = sum(len(values) for values in ground.values())
        ranked = sorted(
            (
                (p.confidence, key, index, p)
                for key in eligible
                for index, p in enumerate(predictions[key].detections)
                if p.class_id == class_id
            ),
            key=lambda item: (-item[0], item[1], item[2]),
        )
        aps: list[float] = []
        op_tp = op_fp = 0
        for step in range(10):
            threshold = 0.5 + step * 0.05
            matched: dict[str, set[int]] = defaultdict(set)
            tp = fp = 0
            curve: list[tuple[float, float]] = []
            for confidence, key, _, pred in ranked:
                candidates = [
                    (iou(pred.bbox, obj.bbox), index)
                    for index, obj in enumerate(ground[key])
                    if index not in matched[key]
                ]
                best, index = max(candidates, default=(0.0, -1))
                if index >= 0 and best + 1e-12 >= threshold:
                    matched[key].add(index)
                    tp += 1
                    if step == 0 and confidence >= operating_confidence:
                        op_tp += 1
                elif any(iou(pred.bbox, obj.bbox) + 1e-12 >= threshold for obj in ignored[key]):
                    continue
                else:
                    fp += 1
                    if step == 0 and confidence >= operating_confidence:
                        op_fp += 1
                if n_gt:
                    curve.append((tp / n_gt, tp / (tp + fp)))
            if n_gt:
                # 101 recall levels; monotone precision envelope. Classes with no GT stay undefined.
                recalls = [row[0] for row in curve]
                envelope = [row[1] for row in curve]
                for index in range(len(envelope) - 2, -1, -1):
                    envelope[index] = max(envelope[index], envelope[index + 1])
                indices = [bisect_left(recalls, level / 100 - 1e-12) for level in range(101)]
                aps.append(sum(envelope[i] if i < len(envelope) else 0 for i in indices) / 101)
        per_class[str(class_id)] = {
            "ground_truth": n_gt,
            "frames": len(eligible),
            "tp": op_tp,
            "fp": op_fp,
            "fn": n_gt - op_tp,
            "precision": op_tp / (op_tp + op_fp) if op_tp + op_fp else None,
            "recall": op_tp / n_gt if n_gt else None,
            "AP50": aps[0] if aps else None,
            "AP50_95": sum(aps) / len(aps) if aps else None,
        }
        total_tp += op_tp
        total_fp += op_fp
        total_gt += n_gt
    # Rows = true class, columns = predicted class; background includes misses/spurious detections.
    labels = [str(c) for c in class_ids] + ["background"]
    confusion = {key: dict.fromkeys(labels, 0) for key in labels}
    for key, ann in truth.items():
        eligible_ids = set(class_ids).intersection(ann.exhaustive_classes)
        gt = [o for o in ann.objects if not o.ignore and o.class_id in eligible_ids]
        frame_predictions = [
            p
            for p in predictions[key].detections
            if p.class_id in eligible_ids and p.confidence >= operating_confidence
        ]
        weights = [
            [
                1 + iou(g.bbox, p.bbox) if iou(g.bbox, p.bbox) >= 0.5 else 0
                for p in frame_predictions
            ]
            for g in gt
        ]
        matches = assignment(weights)
        matched_g, matched_p = {i for i, _ in matches}, {j for _, j in matches}
        for i, j in matches:
            confusion[str(gt[i].class_id)][str(frame_predictions[j].class_id)] += 1
        for i, g in enumerate(gt):
            if i not in matched_g:
                confusion[str(g.class_id)]["background"] += 1
        for j, p in enumerate(frame_predictions):
            if j not in matched_p and not any(
                o.ignore and iou(o.bbox, p.bbox) >= 0.5 for o in ann.objects
            ):
                confusion["background"][str(p.class_id)] += 1
    present = [value for value in per_class.values() if value["ground_truth"]]
    return {
        "protocol": "BBox AP: IoU .50:.05:.95; 101 recall levels; exhaustive labels only.",
        "operating_confidence": operating_confidence,
        "operating_iou": 0.5,
        "frames": len(truth),
        "ground_truth": total_gt,
        "tp": total_tp,
        "fp": total_fp,
        "fn": total_gt - total_tp,
        "precision": total_tp / (total_tp + total_fp) if total_tp + total_fp else None,
        "recall": total_tp / total_gt if total_gt else None,
        "mAP50": sum(v["AP50"] for v in present) / len(present) if present else None,
        "mAP50_95": sum(v["AP50_95"] for v in present) / len(present) if present else None,
        "per_class": per_class,
        "confusion_matrix": {"labels": labels, "rows_true_columns_predicted": confusion},
    }


def hota_metrics(
    frames: tuple[tuple[str, FrameAnnotation, PredictionFrame], ...], class_ids: tuple[int, ...]
) -> dict[str, Any]:
    """Class-aware HOTA, averaged over 19 IoU thresholds; identity namespaces per sequence.

    Global alignment uses normalized potential matches before per-frame assignment.
    Protocol reference: TrackEval's published HOTA algorithm (independent implementation).
    """
    truth_counts: Counter[tuple[str, str]] = Counter()
    predicted_counts: Counter[tuple[str, str]] = Counter()
    potential: dict[tuple[tuple[str, str], tuple[str, str]], float] = defaultdict(float)
    prepared = []
    for sequence, ann, frame in frames:
        eligible = set(class_ids).intersection(ann.exhaustive_classes)
        gt = [o for o in ann.objects if o.class_id in eligible and not o.ignore]
        pred = [
            p
            for p in frame.tracks
            if p.class_id in eligible
            and p.track_id is not None
            and not any(
                o.ignore and o.class_id == p.class_id and iou(o.bbox, p.bbox) >= 0.5
                for o in ann.objects
            )
        ]
        if any(o.track_id is None for o in gt) or len({o.track_id for o in gt}) != len(gt):
            raise ValueError("HOTA requires complete unique ground-truth identities")
        if len({p.track_id for p in pred}) != len(pred):
            raise ValueError("HOTA requires unique predicted identities")
        gids = [(sequence, str(o.track_id)) for o in gt]
        pids = [(sequence, str(p.track_id)) for p in pred]
        truth_counts.update(gids)
        predicted_counts.update(pids)
        similarities = [
            [iou(g.bbox, p.bbox) if g.class_id == p.class_id else 0 for p in pred] for g in gt
        ]
        row_sums = [sum(row) for row in similarities]
        column_sums = [sum(row[j] for row in similarities) for j in range(len(pred))]
        for i, gid in enumerate(gids):
            for j, pid in enumerate(pids):
                similarity = similarities[i][j]
                denominator = row_sums[i] + column_sums[j] - similarity
                if denominator > 0:
                    potential[gid, pid] += similarity / denominator
        prepared.append((gids, pids, similarities))
    alignment = {
        pair: value / (truth_counts[pair[0]] + predicted_counts[pair[1]] - value)
        for pair, value in potential.items()
    }
    counts: list[Counter[tuple[tuple[str, str], tuple[str, str]]]] = [Counter() for _ in range(19)]
    for gids, pids, similarities in prepared:
        weights = [
            [alignment.get((g, p), 0) * similarities[i][j] for j, p in enumerate(pids)]
            for i, g in enumerate(gids)
        ]
        for i, j in assignment(weights):
            for index in range(19):
                if similarities[i][j] >= (index + 1) * 0.05 - 1e-12:
                    counts[index][gids[i], pids[j]] += 1
    n_gt, n_pred = sum(truth_counts.values()), sum(predicted_counts.values())
    if not n_gt and not n_pred:
        return {"HOTA": None, "HOTA_status": "NO_EVALUATED_IDENTITIES", "HOTA_thresholds": []}
    results = []
    for index, matched in enumerate(counts):
        tp = sum(matched.values())
        detection = tp / (n_gt + n_pred - tp)
        association = (
            sum(
                value * value / (truth_counts[g] + predicted_counts[p] - value)
                for (g, p), value in matched.items()
            )
            / tp
            if tp
            else 0.0
        )
        results.append(
            {
                "iou": (index + 1) * 0.05,
                "DetA": detection,
                "AssA": association,
                "HOTA": (detection * association) ** 0.5,
            }
        )
    return {
        "HOTA": sum(row["HOTA"] for row in results) / 19,
        "HOTA_status": "MEASURED",
        "HOTA_thresholds": results,
    }


def tracking_metrics(
    frames: tuple[tuple[str, FrameAnnotation, PredictionFrame], ...], class_ids: tuple[int, ...]
) -> dict[str, Any]:
    """Frames ordered by (sequence, timestamp). ID assignment maximizes global match counts.

    Uses all IoU-qualified pair co-occurrences for global IDF1, as in TrackEval identity.
    Frame association for switches preserves the previous valid association before assignment.
    """
    gt_count: Counter[tuple[str, str]] = Counter()
    pred_count: Counter[tuple[str, str]] = Counter()
    possible: Counter[tuple[tuple[str, str], tuple[str, str]]] = Counter()
    last: dict[tuple[str, str], str] = {}
    was_matched: dict[tuple[str, str], bool] = {}
    switches = fragments = matched_count = false_positive = false_negative = 0
    for sequence, ann, frame in frames:
        ids = set(class_ids).intersection(ann.exhaustive_classes)
        gt = [o for o in ann.objects if o.class_id in ids and not o.ignore]
        if any(o.track_id is None for o in gt) or len({o.track_id for o in gt}) != len(gt):
            raise ValueError("Tracking truth must include all evaluated identities")
        pred = [
            p
            for p in frame.tracks
            if p.track_id is not None
            and p.class_id in ids
            and not any(
                o.ignore and o.class_id == p.class_id and iou(o.bbox, p.bbox) >= 0.5
                for o in ann.objects
            )
        ]
        if any(p.track_id is None for p in pred) or len({p.track_id for p in pred}) != len(pred):
            raise ValueError("Tracker output must have unique non-null IDs per frame")
        weights = [
            [
                iou(g.bbox, p.bbox)
                if g.class_id == p.class_id and iou(g.bbox, p.bbox) >= 0.5
                else 0
                for p in pred
            ]
            for g in gt
        ]
        for i, g in enumerate(gt):
            gid = sequence, str(g.track_id)
            gt_count[gid] += 1
            for j, p in enumerate(pred):
                if weights[i][j] > 0:
                    possible[(gid, (sequence, str(p.track_id)))] += 1
        for p in pred:
            pred_count[(sequence, str(p.track_id))] += 1
        matches: list[tuple[int, int]] = []
        used_p: set[int] = set()
        for i, g in enumerate(gt):
            previous = last.get((sequence, str(g.track_id)))
            for j, p in enumerate(pred):
                if j not in used_p and previous == p.track_id and weights[i][j] > 0:
                    matches.append((i, j))
                    used_p.add(j)
                    break
        used_g = {i for i, _ in matches}
        remaining_g = [i for i in range(len(gt)) if i not in used_g]
        remaining_p = [j for j in range(len(pred)) if j not in used_p]
        remainder = [
            [1 + weights[i][j] if weights[i][j] else 0 for j in remaining_p] for i in remaining_g
        ]
        matches.extend((remaining_g[i], remaining_p[j]) for i, j in assignment(remainder))
        associated = {i: j for i, j in matches}
        for i, g in enumerate(gt):
            gid = sequence, str(g.track_id)
            if i in associated:
                identity = str(pred[associated[i]].track_id)
                if gid in last and last[gid] != identity:
                    switches += 1
                if gid in last and not was_matched.get(gid, False):
                    fragments += 1
                last[gid] = identity
                was_matched[gid] = True
            else:
                was_matched[gid] = False
        matched_count += len(matches)
        false_positive += len(pred) - len(matches)
        false_negative += len(gt) - len(matches)
    # The global problem is block-diagonal by sequence; solve without a dataset-wide dense matrix.
    sequence_pairs: dict[str, list[tuple[tuple[str, str], tuple[str, str]]]] = defaultdict(list)
    for gt_key, pred_key in possible:
        sequence_pairs[gt_key[0]].append((gt_key, pred_key))
    idtp = 0
    for edges in sequence_pairs.values():
        gt_ids = sorted({g for g, _ in edges})
        pred_ids = sorted({p for _, p in edges})
        if max(len(gt_ids), len(pred_ids)) > 2000:
            raise ValueError(
                "Tracking sequence exceeds 2000 associated IDs; declare shorter sequences"
            )
        weights_global = [[float(possible[(g, p)]) for p in pred_ids] for g in gt_ids]
        idtp += int(sum(weights_global[i][j] for i, j in assignment(weights_global)))
    n_gt, n_pred = sum(gt_count.values()), sum(pred_count.values())
    return {
        "protocol": "global IDF1 at class-aware IoU>=0.5; per-sequence identity namespaces",
        "IDF1": 2 * idtp / (n_gt + n_pred) if n_gt + n_pred else None,
        "IDTP": idtp,
        "IDFN": n_gt - idtp,
        "IDFP": n_pred - idtp,
        "id_switches": switches,
        "fragmentations": fragments,
        "matched_detections": matched_count,
        "false_positives": false_positive,
        "false_negatives": false_negative,
        "detection_recall": matched_count / n_gt if n_gt else None,
        "ground_truth_detections": n_gt,
        **hota_metrics(frames, class_ids),
    }

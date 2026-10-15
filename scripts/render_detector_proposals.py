"""Build a separate visual review page for model proposals; never creates human labels."""

import argparse
import html
import json
import math
from pathlib import Path

from traffic_vision.dataset.files import digest_file


def render(review: Path, predictions: Path, output: Path) -> int:
    source = json.loads((review / "review.json").read_text(encoding="utf-8"))
    report = json.loads(predictions.read_text(encoding="utf-8"))
    if report.get("source_sha256") != source.get("source_sha256"):
        raise ValueError("Prediction report belongs to another video")
    frames = {row["frame_index"]: row for row in report.get("frames", [])}
    if len(frames) != len(report.get("frames", [])):
        raise ValueError("Duplicate prediction frame")
    if not frames:
        raise ValueError("Prediction report has no frames")
    cards = []
    for frame in source["frames"]:
        index = frame["frame_index"]
        prediction = frames.get(index)
        if prediction is None:
            continue
        image = (review / frame["image"]).resolve()
        if not image.is_relative_to(review.resolve()) or digest_file(image) != frame["sha256"]:
            raise ValueError("Review image path or hash mismatch")
        width, height = frame["width"], frame["height"]
        boxes = []
        for detection in prediction.get("detections", []):
            x1, y1, x2, y2 = detection["bbox"]
            confidence = detection["confidence"]
            if (
                not all(math.isfinite(value) for value in (x1, y1, x2, y2, confidence))
                or not 0 <= confidence <= 1
                or not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height)
            ):
                raise ValueError(f"Invalid proposal box in frame {index}")
            label = html.escape(f"{detection['class']} {confidence:.0%}")
            boxes.append(
                f'<g><rect x="{x1}" y="{y1}" width="{x2 - x1}" height="{y2 - y1}"/>'
                f'<text x="{x1}" y="{max(48, y1 - 8)}">{label}</text></g>'
            )
        src = html.escape(frame["image"], quote=True)
        cards.append(
            f"<article><h2>Кадр {index} · {frame['nominal_timestamp_seconds']:.2f} с</h2>"
            f'<div class="image" style="aspect-ratio:{width}/{height}">'
            f'<img src="{src}" alt="Исходный кадр {index}">'
            f'<svg viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="Предложения модели, кадр {index}">{"".join(boxes)}</svg></div>'
            f"<p>{prediction.get('candidate_count', len(boxes))} предложений модели</p></article>"
        )
    model_hash = html.escape(str(report.get("model_sha256") or "не указан"))
    cards_html = "\n".join(cards)
    document = f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RohAI — предложения модели</title><style>
body{{font:16px system-ui;background:#101827;color:#e8eef8;margin:24px}}
main{{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr));gap:18px}}
article{{background:#1b2638;padding:14px;border-radius:12px}}h1{{margin-bottom:8px}}
.image{{position:relative;width:100%;background:#000}}
.image img,.image svg{{position:absolute;inset:0;width:100%;height:100%;object-fit:contain}}
svg{{overflow:visible}}
rect{{fill:#26d9e8;fill-opacity:.12;stroke:#26d9e8;stroke-width:12;
vector-effect:non-scaling-stroke}}
text{{fill:#08131e;stroke:#26d9e8;stroke-width:1px;paint-order:stroke;
stroke-linejoin:round;font:bold 44px system-ui}}
.notice{{padding:14px;background:#302a1b;border-radius:8px}}a{{color:#79e7f0}}
</style><h1>Предложения модели на кадрах</h1>
<p>YOLO26s · фрагменты 1024 px · порог {report.get("confidence", "—")}</p>
<p>SHA256 модели: {model_hash}</p>
<p class="notice">Рамки — только вывод модели. Это не человеческая разметка и не
доказательство точности. Проверяйте пропуски и ложные рамки по исходным кадрам;
для ручной разметки откройте <a href="annotate.html">редактор</a>.</p>
<main>{cards_html}</main></html>"""
    output.write_text(document, encoding="utf-8")
    return len(cards)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("review", type=Path)
    parser.add_argument("predictions", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    count = render(args.review, args.predictions, args.output)
    print(
        json.dumps({"status": "MODEL_PROPOSALS_ONLY", "frames": count, "output": str(args.output)})
    )


if __name__ == "__main__":
    main()

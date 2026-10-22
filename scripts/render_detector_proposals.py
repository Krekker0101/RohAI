"""Build a separate visual review page for model proposals; never creates human labels."""

import argparse
import json
import math
import shutil
from pathlib import Path

from traffic_vision.dataset.files import digest_file


def render(review: Path, predictions: Path, output: Path) -> int:
    review = review.resolve()
    output = output.resolve()
    if output.parent != review:
        raise ValueError("Proposal page must be written beside its review frames")
    source = json.loads((review / "review.json").read_text(encoding="utf-8"))
    report = json.loads(predictions.read_text(encoding="utf-8"))
    if report.get("source_sha256") != source.get("source_sha256"):
        raise ValueError("Prediction report belongs to another video")
    frames = {row["frame_index"]: row for row in report.get("frames", [])}
    if len(frames) != len(report.get("frames", [])):
        raise ValueError("Duplicate prediction frame")
    if not frames:
        raise ValueError("Prediction report has no frames")
    visible_frames = []
    for frame in source["frames"]:
        index = frame["frame_index"]
        prediction = frames.get(index)
        if prediction is None:
            continue
        image = (review / frame["image"]).resolve()
        if not image.is_relative_to(review.resolve()) or digest_file(image) != frame["sha256"]:
            raise ValueError("Review image path or hash mismatch")
        width, height = frame["width"], frame["height"]
        for detection in prediction.get("detections", []):
            x1, y1, x2, y2 = detection["bbox"]
            confidence = detection["confidence"]
            if (
                not all(math.isfinite(value) for value in (x1, y1, x2, y2, confidence))
                or not 0 <= confidence <= 1
                or not (0 <= x1 < x2 <= width and 0 <= y1 < y2 <= height)
            ):
                raise ValueError(f"Invalid proposal box in frame {index}")
        visible_frames.append(
            {
                "index": index,
                "time": frame["nominal_timestamp_seconds"],
                "image": frame["image"],
                "width": width,
                "height": height,
                "detections": prediction.get("detections", []),
            }
        )
    if not visible_frames:
        raise ValueError("No prediction frames match this review")
    payload = json.dumps(
        {
            "model": report.get("model", "Model"),
            "model_sha256": report.get("model_sha256"),
            "confidence": report.get("confidence", 0.25),
            "frames": visible_frames,
        },
        ensure_ascii=False,
    ).replace("<", "\\u003c")
    document = f"""<!doctype html><html lang="ru"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>RohAI — предложения модели</title><style>
body{{font:16px system-ui;background:#101827;color:#e8eef8;margin:20px}}
header{{display:flex;flex-wrap:wrap;align-items:center;gap:10px}}button,input{{font:inherit}}
button{{padding:9px 14px;border-radius:8px;border:0;cursor:pointer}}
label{{display:flex;align-items:center;gap:8px}}.notice{{padding:12px;background:#302a1b;border-radius:8px}}
a{{color:#79e7f0}}#viewport{{width:100%;height:68vh;overflow:auto;background:#05080d}}
#stage{{position:relative}}#frame{{display:block;width:100%;height:100%}}
#overlay{{position:absolute;inset:0;width:100%;height:100%;overflow:visible}}
rect{{fill:#26d9e8;fill-opacity:.1;stroke:#26d9e8;stroke-width:12;vector-effect:non-scaling-stroke}}
svg text{{fill:#fff;stroke:#06101a;stroke-width:4px;paint-order:stroke;
stroke-linejoin:round;font:bold 44px system-ui}}
table{{border-collapse:collapse;margin-top:12px}}
td,th{{padding:6px 12px;text-align:left;border-bottom:1px solid #34445c}}
</style><h1>Предложения модели на кадрах</h1>
<p class="notice">Рамки — вывод модели для визуальной проверки.
Они не являются человеческими метками.
Сначала проверьте кадры вручную; редактор разметки доступен <a href="annotate.html">здесь</a>.</p>
<p id="model"></p><header><button id="previous" type="button">← Кадр</button>
<strong id="position"></strong><button id="next" type="button">Кадр →</button>
<label>Увеличение <input id="zoom" type="range" min="1" max="6" step="0.5" value="1">
<output id="zoom-value">1×</output></label>
<label>Порог показа <input id="threshold" type="range" min="0.25" max="0.8" step="0.05">
<output id="threshold-value"></output></label></header>
<p id="frame-info" aria-live="polite"></p><div id="viewport"><div id="stage">
<img id="frame" alt="Кадр видео с предложениями модели"><svg id="overlay" role="img"></svg>
</div></div><table><thead><tr><th>Класс</th><th>Уверенность</th><th>Рамка в 4K</th></tr></thead>
<tbody id="detections"></tbody></table>
<script id="data" type="application/json">{payload}</script>
<script src="proposals.js"></script></html>"""
    output.write_text(document, encoding="utf-8")
    shutil.copyfile(
        Path(__file__).parent / "review_assets" / "proposals.js",
        output.parent / "proposals.js",
    )
    return len(visible_frames)


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

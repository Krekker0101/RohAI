"""Export dispersed original frames for human review; never fabricates annotations."""

import argparse
import html
import json
import math
import shutil
from pathlib import Path
from typing import Any

import cv2
from traffic_vision.dataset.files import digest_file


def prepare(source: Path, output: Path, count: int = 24) -> dict[str, Any]:
    if not 1 <= count <= 200:
        raise ValueError("Review count must be 1..200")
    if output.exists():
        raise FileExistsError("Review output must be a new directory")
    capture = cv2.VideoCapture(str(source))
    try:
        total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = capture.get(cv2.CAP_PROP_FPS)
        if not capture.isOpened() or total <= 0 or not math.isfinite(fps) or fps <= 0:
            raise ValueError("Video requires decodable frames and a finite positive frame rate")
        output.mkdir(parents=True)
        images = output / "frames"
        images.mkdir()
        selected = sorted({round(i * (total - 1) / max(1, count - 1)) for i in range(count)})
        frames = []
        for index in selected:
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, image = capture.read()
            if not ok or image is None:
                raise ValueError(f"Could not decode selected frame {index}")
            if round(capture.get(cv2.CAP_PROP_POS_FRAMES)) - 1 != index:
                raise ValueError("Decoder seek did not reach the requested original frame")
            name = f"frame-{index:08d}.png"
            path = images / name
            if not cv2.imwrite(str(path), image):
                raise OSError("Could not write review image")
            frames.append(
                {
                    "frame_index": index,
                    "nominal_timestamp_seconds": index / fps,
                    "image": f"frames/{name}",
                    "sha256": digest_file(path),
                    "width": image.shape[1],
                    "height": image.shape[0],
                    "review_status": "UNREVIEWED",
                    "annotations": None,
                }
            )
        manifest = {
            "schema_version": "1.0",
            "status": "DRAFT_UNREVIEWED",
            "source": str(source),
            "source_sha256": digest_file(source),
            "source_frames": total,
            "nominal_fps": fps,
            "frames": frames,
            "training_rights": "NOT_VERIFIED",
            "privacy_review": "NOT_VERIFIED",
            "independent_ground_truth": False,
            "split_rule": "Keep every frame of this recording/cohort in one split",
            "timestamp_note": "Nominal frame-index/FPS; not calibrated capture time",
        }
        (output / "review.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        editor = Path(__file__).parent / "review_assets"
        embedded = json.dumps(manifest, ensure_ascii=False).replace("<", "\\u003c")
        (output / "annotate.html").write_text(
            (editor / "editor.html").read_text(encoding="utf-8").replace("__MANIFEST__", embedded),
            encoding="utf-8",
        )
        shutil.copyfile(editor / "editor.js", output / "editor.js")
        cards = "".join(
            f'<figure><img loading="lazy" src="{f["image"]}" '
            f'alt="Frame {f["frame_index"]}"><figcaption>Frame {f["frame_index"]} · '
            f"{f['nominal_timestamp_seconds']:.2f}s · UNREVIEWED</figcaption></figure>"
            for f in frames
        )
        (output / "index.html").write_text(
            '<!doctype html><html lang="ru"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            "<title>RohAI — кадры для проверки</title><style>"
            "body{font:16px system-ui;margin:24px;background:#f3f5f7;color:#17212b}"
            "main{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:16px}"
            "figure{margin:0;background:white;padding:12px}img{width:100%;height:auto}"
            "</style><h1>Кадры для независимой разметки</h1><p>"
            '<a href="annotate.html">Разметить машины и сохранить JSON</a></p><p>'
            + html.escape(source.name)
            + "</p><p>Разметка отсутствует. Пустые annotations не означают отсутствие объектов. "
            "Перед обучением нужны права на видео и проверка человеком.</p><main>"
            + cards
            + "</main></html>",
            encoding="utf-8",
        )
        return manifest
    finally:
        capture.release()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=24)
    args = parser.parse_args()
    result = prepare(args.source, args.output, args.count)
    print(json.dumps({"status": result["status"], "frames": len(result["frames"])}))


if __name__ == "__main__":
    main()

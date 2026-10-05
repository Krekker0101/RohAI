"""Extract review candidates from a recorded live stand session; never create labels."""

import argparse
import hashlib
import json
from pathlib import Path

import cv2


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--every", type=int, default=10)
    args = parser.parse_args()
    if args.every < 1:
        parser.error("--every must be positive")
    metadata = json.loads((args.session / "session.json").read_text())
    if metadata["mode"] != "PHYSICAL_MAKET":
        raise ValueError("Expected an actual recorded maket session")
    args.output.mkdir(parents=True, exist_ok=False)
    cap = cv2.VideoCapture(str(args.session / "video.avi"))
    frames = []
    index = 0
    try:
        while True:
            ok, image = cap.read()
            if not ok:
                break
            if index % args.every == 0:
                name = f"{metadata['id']}-{index:08d}.png"
                ok, data = cv2.imencode(".png", image)
                if not ok:
                    raise ValueError("Image encoding failed")
                raw = data.tobytes()
                (args.output / name).write_bytes(raw)
                frames.append(
                    {
                        "frame_id": name,
                        "sha256": hashlib.sha256(raw).hexdigest(),
                        "recording_frame_index": index,
                        "annotation_status": "UNANNOTATED",
                        "session_id": metadata["id"],
                        "ground_truth": False,
                    }
                )
            index += 1
    finally:
        cap.release()
    if not frames:
        raise ValueError("No recorded frames; no dataset fabricated")
    (args.output / "review_inventory.json").write_text(
        json.dumps(
            {
                "deployment_domain": "PHYSICAL_MAKET",
                "source_session": metadata,
                "frames": frames,
                "timestamp_note": (
                    "Recording is sampled variable-rate video; join actual capture times from "
                    "events, do not infer road timing from nominal AVI FPS"
                ),
            },
            indent=2,
        )
    )
    print(json.dumps({"frames": len(frames), "labels_created": 0}))


if __name__ == "__main__":
    main()

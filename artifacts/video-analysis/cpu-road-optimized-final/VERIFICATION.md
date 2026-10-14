# CPU / road optimization verification

- Exact original source frame 0, unchanged YOLO26s weights, 15 tiles: alternating 2/6 CPU threads, three runs each. Median 5.856s -> 2.095s (2.79x detector speedup on this frame only). Each run found 3 objects. No independently labelled accuracy available.
- Browser acceptance: all 4 tests passed. Real uploaded clip: 6 frames re-encoded from the source at frame 0, 6 processed, 0 dropped, 21 observations. Total worker 22.697s includes model loading; detector median 2547.8ms. This is a short-clip test, not full-source throughput verification.
- Road snapshots: original frames 0, 300, 586. Coverage fraction measures mask area, not segmentation accuracy. Ground/trees can still receive erroneous edges. Cityscapes pretrained model plus bounded aerial heuristic; no new training.
- Preview rendering moved to <=1280px; inference inputs and stored coordinates stay full-resolution. uint8 OpenCV blend replaces large floating-point pixel arrays.
- Full Python suite: 540 passed, 8 hardware tests skipped before the final additional road regression. Final road tests: 8 passed. Ruff passed. Mypy: 210 source files passed.
- No CUDA device available; realtime 30fps on 4K aerial inference has not been achieved. No claim of an absolute hardware-wide optimum.

# Windows preview sharing regression — 2026-10-12

Original uploaded file: `15025977_3840_2160_30fps.mp4` (3840×2160, 30 FPS).
The failed job's traceback identified PermissionError / WinError 5 when replacing
`preview.jpg` while FileResponse held it open during transfer.

The corrected API closes the JPEG before sending its body. Atomic writes retry brief
sharing violations; a persistently locked preview skips an image update without
dropping inference frames or failing the job.

The unchanged original uploaded video was submitted through the corrected API:
587/587 frames completed, zero frame drops, 665 successful preview reads during
analysis. See `verification.json` and `report.json`; `preview.jpg` is the final preview.
This checks processing and file lifecycle, not independently labeled detection accuracy.

Focused Python regression: 25 passed, including real Windows open-file locking,
transient retry, analysis completion under a permanently locked preview, and response
file-handle release. Ruff and strict mypy passed (204 source files).

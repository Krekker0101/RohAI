# Local recordings

The default Vision config expects `car-detection.mp4` in this directory.
It is intentionally not bundled in this project archive.

Fetch the pinned demo input from the project root:

```powershell
uv run --extra vision python scripts/fetch_vision_demo.py --asset video
```

For the mixed traffic sample used by the dashboard recipe:

```powershell
uv run --extra vision python scripts/fetch_vision_demo.py --asset traffic
```

"""Recorded stand review, deliberately no hardware import or runtime creation."""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import cv2
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse


def replay_app(session: Path) -> FastAPI:
    if not (session / "session.json").is_file():
        raise ValueError("Recorded session manifest missing")
    app = FastAPI(title="RohAI VIDEO_REPLAY — physical outputs disabled")

    @app.get("/")
    def page() -> FileResponse:
        return FileResponse("apps/maket-ui/replay.html")

    @app.get("/events")
    def events() -> StreamingResponse:
        def lines() -> Iterator[bytes]:
            with (session / "events.jsonl").open("rb") as stream:
                yield from stream

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @app.get("/timeline")
    async def recorded_timeline(
        offset: int = Query(default=0, ge=0, le=1_000_000),
        limit: int = Query(default=200, ge=1, le=500),
    ) -> dict[str, object]:
        from smart_traffic_backend.maket.history import timeline

        try:
            return await asyncio.to_thread(timeline, session.parent, session.name, offset, limit)
        except (OSError, ValueError) as error:
            raise HTTPException(422, str(error)) from error

    @app.get("/frame/{index}")
    async def frame(index: int) -> Response:
        if index < 0:
            raise HTTPException(422, "Nonnegative index required")

        def read() -> bytes:
            capture = cv2.VideoCapture(str(session / "video.avi"))
            try:
                capture.set(cv2.CAP_PROP_POS_FRAMES, index)
                ok, image = capture.read()
                if not ok:
                    raise ValueError("Recorded frame unavailable")
                ok, data = cv2.imencode(".jpg", image)
                if not ok:
                    raise ValueError("JPEG encoding failed")
                return data.tobytes()
            finally:
                capture.release()

        try:
            return Response(await asyncio.to_thread(read), media_type="image/jpeg")
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    return app

"""Recorded stand review, deliberately no hardware import or runtime creation."""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import cv2
from fastapi import FastAPI, HTTPException
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

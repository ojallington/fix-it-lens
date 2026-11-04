"""The local SAM 3.1 server: POST /v1/responses takes the hosted sam-3.1 request body calls/ builds, and streams
the hosted API's events back. GET /health says whether the weights are present and the model is loaded; local mode
picks its second witness from it (SAM when it answers, the Glimmer-locate track when it does not).

    uv run --project local/sam_server uvicorn local.sam_server.server:app --host 0.0.0.0 --port 8088

It binds on the LAN only and holds no key. Frames never leave the machine it runs on.
"""

from __future__ import annotations

import asyncio
import base64
import gc
import io
import json
import os
import signal
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from PIL import Image

from local.sam_server.encode import MODEL, frames_text, parse_prompt, sse_events
from local.sam_server.segmenter import WEIGHTS_DIR, Sam31, Segmenter, as_media, weights_present


def _data(url: str, prefix: str) -> bytes:
    if not url.startswith(prefix):
        raise HTTPException(400, f"expected a {prefix}... data URL")
    return base64.b64decode(url.split(",", 1)[1])


def _mp4_size(mp4: bytes) -> tuple[int, int]:
    import av  # the GPU env only; the image path needs none of it

    with av.open(io.BytesIO(mp4)) as c:
        s = c.streams.video[0]
        return int(s.width), int(s.height)


IDLE_S = float(os.environ.get("SAM_IDLE_S", "0"))  # > 0: unload the model after that long idle (the 5090 is shared)
# Dropping the model in-process leaves ~5.9 GB held (sam3 keeps references, and the CUDA context stays; measured),
# so the unit ends the process instead: systemd starts it again with nothing on the GPU.
IDLE_EXIT = os.environ.get("SAM_IDLE_EXIT") == "1"


def _unload(state: dict[str, Any]) -> None:
    if IDLE_EXIT:
        os.kill(os.getpid(), signal.SIGTERM)  # uvicorn shuts down cleanly; Restart=always brings it back unloaded
        return
    state["seg"] = None
    gc.collect()
    try:
        import torch  # the GPU env only

        torch.cuda.empty_cache()
    except ImportError:
        pass


def create_app(segmenter: Segmenter | None = None, *, idle_s: float = IDLE_S) -> FastAPI:
    state: dict[str, Any] = {"seg": segmenter, "lock": asyncio.Lock(), "served": 0, "last": time.monotonic()}
    keep = segmenter is not None  # a model handed in (a test's) is never unloaded

    async def reap_once() -> None:
        if state["seg"] is not None and time.monotonic() - state["last"] > idle_s and not state["lock"].locked():
            async with state["lock"]:
                _unload(state)

    async def reaper() -> None:
        while True:
            await asyncio.sleep(min(60.0, idle_s))
            await reap_once()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(reaper()) if idle_s > 0 and not keep else None
        yield
        if task is not None:
            task.cancel()

    app = FastAPI(title="fixit-sam-local", lifespan=lifespan)
    app.state.reap_once = reap_once
    app.state.sam = state

    def seg() -> Segmenter:
        if state["seg"] is None:
            state["seg"] = Sam31()
        s: Segmenter = state["seg"]
        return s

    @app.post("/warm")
    async def warm() -> dict[str, Any]:
        """Load the model now (preflight's warm-up), so the first segment of a session is not a 15-20 s cold load."""
        t0 = time.perf_counter()
        async with state["lock"]:
            await asyncio.to_thread(seg)
        state["last"] = time.monotonic()
        return {"ok": True, "loaded": True, "ms": round((time.perf_counter() - t0) * 1000)}

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": state["seg"] is not None or weights_present(), "model": MODEL,
                "weights": state["seg"] is not None or weights_present(), "weights_dir": str(WEIGHTS_DIR),
                "loaded": state["seg"] is not None, "served": state["served"]}

    @app.post("/v1/responses")
    async def responses(request: Request) -> StreamingResponse:
        body = await request.json()
        if body.get("store") is not False:
            raise HTTPException(400, "store must be false")
        content = body["input"][0]["content"]
        text = next(c["text"] for c in content if c.get("type") == "input_text")
        media = next(c for c in content if c.get("type") in ("input_image", "input_video"))
        video = media["type"] == "input_video"
        prompt = parse_prompt(text)
        t0 = time.perf_counter()
        if video:
            mp4 = _data(media["video_url"], "data:video/mp4;base64,")
            size = _mp4_size(mp4)
            ctx = as_media(mp4=mp4)
        else:
            jpeg = _data(media["image_url"], "data:image/")
            with Image.open(io.BytesIO(jpeg)) as im:
                size = im.size
            ctx = as_media([jpeg])
        async with state["lock"]:  # one GPU, one request at a time
            with ctx as path:
                frames = await asyncio.to_thread(seg().segment, path, prompt, size)
        state["served"] += 1
        state["last"] = time.monotonic()
        evs = sse_events(frames_text(frames, *size), video=video, n_frames=len(frames))
        ms = (time.perf_counter() - t0) * 1000

        async def stream() -> AsyncIterator[bytes]:
            for ev in evs:
                yield f"event: {ev['type']}\ndata: {json.dumps(ev)}\n\n".encode()

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"x-sam-ms": f"{ms:.0f}"})

    return app


app = create_app()

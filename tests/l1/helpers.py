"""Test helpers for the session tests: frames, a fake calls client for timing cases, SAM results."""

from __future__ import annotations

import asyncio
import io
from typing import Any

from PIL import Image, ImageDraw

import calls
from app.frames import StoredFrame


def jpeg(w: int = 640, h: int = 480, shade: int = 120, mark: tuple[int, int] | None = None) -> bytes:
    im = Image.new("RGB", (w, h), (shade, shade + 5, shade + 10))
    if mark is not None:
        ImageDraw.Draw(im).rectangle([mark[0], mark[1], mark[0] + 40, mark[1] + 40], fill=(250, 250, 0))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def frame(i: int, t_ms: float, **kw: Any) -> StoredFrame:
    return StoredFrame(f"f{i}", t_ms, jpeg(**kw), f"{i:016x}", kw.get("w", 640), kw.get("h", 480))


def sam_result(objs: list[tuple[str, int | None, tuple[float, float, float, float], float]],
               w: int = 1000, h: int = 1000) -> calls.SamResult:
    return calls.SamResult(w, h, [calls.SamObject(o, box, conf, None, fr) for o, fr, box, conf in objs], "", 5.0, 0.0,
                           "0" * 64)


class FakeClient:
    """Stands in for calls.Client where a test needs a call to be slow or to fail. Records what was called."""

    def __init__(self, *, video: calls.SamResult | None = None, image: calls.SamResult | None = None,
                 video_delay: float = 0.0, video_error: Exception | None = None,
                 images: dict[str, calls.SamResult] | None = None) -> None:
        self.video, self.image, self.images = video, image, images or {}
        self.video_delay, self.video_error = video_delay, video_error
        self.called: list[str] = []

    async def sam_video(self, clip: bytes, prompt: Any, **kw: Any) -> calls.SamResult:
        self.called.append("sam_video")
        await asyncio.sleep(self.video_delay)
        if self.video_error:
            raise self.video_error
        assert self.video is not None
        return self.video

    async def sam_image(self, frame: bytes, prompt: Any, **kw: Any) -> calls.SamResult:
        self.called.append("sam_image")
        if isinstance(prompt, str) and prompt in self.images:
            return self.images[prompt]
        assert self.image is not None
        return self.image

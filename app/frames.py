"""The frames the phone sends. Latest-wins: the buffer keeps a few seconds and readers take the newest; nothing
ever queues behind a frame. The judge's NOW pair and the SAM clip are picked from here."""

from __future__ import annotations

import asyncio
import base64
import io
from collections import deque
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageStat

from app import config


@dataclass(frozen=True)
class StoredFrame:
    frame_id: str
    t_ms: float  # capture time on the phone's clock
    jpeg: bytes
    phash: str
    width: int
    height: int

    def sharpness(self) -> float:
        """Variance of a Laplacian-like edge image on a small grey copy: higher is sharper."""
        with Image.open(io.BytesIO(self.jpeg)) as im:
            grey = im.convert("L")
            grey.thumbnail((320, 320))
            edges = grey.filter(ImageFilter.FIND_EDGES)
            return float(ImageStat.Stat(edges).var[0])


def hamming(a: str, b: str) -> int:
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def decode(frame_id: str, t_ms: float, jpeg_b64: str, phash: str, width: int | None, height: int | None) -> StoredFrame:
    data = base64.b64decode(jpeg_b64, validate=True)
    if width is None or height is None:
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size
    return StoredFrame(frame_id, t_ms, data, phash, width, height)


class FrameBuffer:
    def __init__(self, keep_s: float = config.FRAME_RING_S) -> None:
        self.keep_ms = keep_s * 1000
        self._frames: deque[StoredFrame] = deque()
        self._by_id: dict[str, StoredFrame] = {}
        self._pinned: dict[str, StoredFrame] = {}  # frames a step still needs (BEFORE), kept past the ring
        self._new = asyncio.Event()

    def add(self, f: StoredFrame) -> None:
        if self._frames and f.t_ms < self._frames[-1].t_ms:
            return  # a late frame never replaces a newer one
        self._frames.append(f)
        self._by_id[f.frame_id] = f
        while self._frames and self._frames[-1].t_ms - self._frames[0].t_ms > self.keep_ms:
            old = self._frames.popleft()
            self._by_id.pop(old.frame_id, None)
        self._new.set()
        self._new = asyncio.Event()

    def latest(self) -> StoredFrame | None:
        return self._frames[-1] if self._frames else None

    def get(self, frame_id: str) -> StoredFrame | None:
        return self._by_id.get(frame_id) or self._pinned.get(frame_id)

    def pin(self, f: StoredFrame) -> None:
        self._pinned[f.frame_id] = f

    def window(self, seconds: float) -> list[StoredFrame]:
        if not self._frames:
            return []
        end = self._frames[-1].t_ms
        return [f for f in self._frames if end - f.t_ms <= seconds * 1000]

    async def next_after(self, frame_id: str | None, timeout_s: float) -> StoredFrame | None:
        """Wait for a frame newer than frame_id (any frame if None)."""
        latest = self.latest()
        if latest is not None and latest.frame_id != frame_id:
            return latest
        try:
            async with asyncio.timeout(timeout_s):
                while True:
                    ev = self._new
                    await ev.wait()
                    latest = self.latest()
                    if latest is not None and latest.frame_id != frame_id:
                        return latest
        except TimeoutError:
            return None

    def now_pair(self) -> tuple[StoredFrame, StoredFrame] | None:
        """NOW_1 and NOW_2: the two sharpest frames 0.5-1 s apart in the last 3 s (NOW_1 the earlier). With only
        one usable frame, both are that frame."""
        win = self.window(config.JUDGE_WINDOW_S)
        if not win:
            return None
        lo, hi = config.NOW_GAP_S[0] * 1000, config.NOW_GAP_S[1] * 1000
        scored = {f.frame_id: f.sharpness() for f in win}
        best: tuple[float, StoredFrame, StoredFrame] | None = None
        for i, a in enumerate(win):
            for b in win[i + 1:]:
                if lo <= b.t_ms - a.t_ms <= hi:
                    s = min(scored[a.frame_id], scored[b.frame_id])
                    if best is None or s > best[0]:
                        best = (s, a, b)
        if best is not None:
            return best[1], best[2]
        top = max(win, key=lambda f: scored[f.frame_id])
        return top, top

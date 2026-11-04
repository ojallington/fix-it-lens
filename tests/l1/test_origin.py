"""How the household came in (the door, or the judges' link) rides on every model call as `origin`, for calls/ to
route and budget on; a household without one sends nothing, so a client that does not know the field never sees it."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import calls
from app.frames import StoredFrame
from app.models import Models
from app.recorder import Recorder
from contracts.models.session import SessionMeta
from tests.l1.test_session import session


class Seen:
    def __init__(self) -> None:
        self.kw: dict[str, dict[str, Any]] = {}

    async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
        self.kw["respond"] = kw
        return calls.Response(text="ok", function_calls=[], model="m", usage=None)  # type: ignore[call-arg]

    async def sam_image(self, frame: bytes, prompt: Any, **kw: Any) -> calls.SamResult:
        self.kw["sam_image"] = kw
        raise calls.MissingFixture("x")

    async def sam_video(self, clip: bytes, prompt: Any, **kw: Any) -> calls.SamResult:
        self.kw["sam_video"] = kw
        raise calls.MissingFixture("x")

    async def transcribe(self, audio: bytes, *a: Any, **kw: Any) -> calls.Transcript:
        self.kw["transcribe"] = kw
        raise calls.MissingFixture("x")


async def every_call(m: Models) -> None:
    f = StoredFrame(frame_id="f1", jpeg=b"j", phash=0, t_ms=0.0, width=1, height=1)
    for go in (lambda: m.respond("guide", []), lambda: m.sam_image(f, "filter"),
               lambda: m.sam_video(b"c", "filter", width=1, height=1, frames=1, timeout_s=1.0),
               lambda: m.transcribe(b"w", ["filter"])):
        with contextlib.suppress(Exception):  # the call's outcome is not what this checks
            await go()


def models(client: Any, **kw: Any) -> Models:
    meta = SessionMeta.model_construct(session_id="o1")
    return Models(client, Recorder(None, meta), lambda: 0.0, household="h1", session_id="o1", **kw)


async def test_every_call_carries_the_origin() -> None:
    for origin in ("door", "judges"):
        c = Seen()
        await every_call(models(c, origin=origin))
        assert set(c.kw) == {"respond", "sam_image", "sam_video", "transcribe"}
        assert all(kw["origin"] == origin for kw in c.kw.values()), c.kw


async def test_no_origin_sends_nothing() -> None:
    c = Seen()
    await every_call(models(c))
    assert len(c.kw) == 4 and not any("origin" in kw for kw in c.kw.values())


def test_the_session_hands_its_origin_to_the_models(tmp_path: Path) -> None:
    assert session(tmp_path, origin="door").models.origin == "door"
    assert session(tmp_path).models.origin is None

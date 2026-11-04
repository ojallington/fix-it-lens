"""A re-run of a recorded session emits the same s2c sequence as the live run, with nothing calling the
live API. The live run here goes through a taping client; the re-run's client answers only a request it has seen,
byte for byte, which is what calls/ replay does with the fixtures (a new request raises MissingFixture)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import calls
from app import recorder
from app.rerun import rerun
from app.session import Session
from tests.l1.helpers import FakeClient, jpeg, sam_result
from tests.l1.test_session import acme, tap
from tests.l1.test_stepcheck import Judge


def digest(*parts: Any) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else json.dumps(p, sort_keys=True, default=str).encode())
    return h.hexdigest()


class Tape:
    """Live: forwards to the inner fake and keeps (request digest -> response). Replay: answers from the tape."""

    def __init__(self, inner: FakeClient | None, tape: dict[str, Any] | None = None) -> None:
        self.inner, self.tape = inner, tape if tape is not None else {}
        self.missing: list[str] = []

    async def _go(self, key: str, fn: Any) -> Any:
        if self.inner is None:
            if key not in self.tape:
                self.missing.append(key)
                raise calls.MissingFixture(key)
            return self.tape[key]
        out = await fn()
        self.tape[key] = out
        return out

    async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
        return await self._go(digest("respond", role, input, kw.get("format"), kw.get("effort")),
                              lambda: self.inner.respond(role, input, **kw))  # type: ignore[union-attr]

    async def sam_image(self, frame: bytes, prompt: Any, **kw: Any) -> calls.SamResult:
        return await self._go(digest("sam_image", frame, prompt),
                              lambda: self.inner.sam_image(frame, prompt, **kw))  # type: ignore[union-attr]

    async def sam_video(self, clip: bytes, prompt: Any, **kw: Any) -> calls.SamResult:
        return await self._go(digest("sam_video", clip, prompt),
                              lambda: self.inner.sam_video(clip, prompt, **kw))  # type: ignore[union-attr]


def frame_msg(i: int, t_ms: float, shade: int) -> tuple[dict[str, Any], bytes]:
    j = jpeg(shade=shade)
    return {"type": "frame", "seq": i, "t_ms": t_ms, "frame_id": f"f{i}", "phash": f"{i:016x}", "width": 640,
            "height": 480, "jpeg_1024": None}, j


async def live_session(tmp_path: Path) -> tuple[Session, Tape]:
    inner = Judge([300, 300])
    inner.images = {"plug": sam_result([("p", None, (100, 600, 200, 700), 0.9)]),
                    "socket": sam_result([("s", None, (600, 300, 700, 400), 0.9)])}
    tape = Tape(inner)
    s = Session(session_id="m1live", client=tape, card=acme(), job_id="clean_debris_filter",  # type: ignore[arg-type]
                country="nl", record_root=tmp_path)
    s.guide = None
    s.start()
    hello = {"type": "hello", "seq": 0, "t_ms": 0, "lang": "en"}
    s.rec.client(0, "c2s", hello)
    await s.emit({"type": "hello_ok", "session_id": s.session_id, "resumed": False, "mode": "api"})
    await s.greet()
    await s.start_job()
    seq, t = 1, 100.0
    for action in ("yes", "done", "done", "done"):
        for _ in range(12):
            m, j = frame_msg(seq, t, 60 + seq % 100)
            await s.handle(m, jpeg=j)
            seq, t = seq + 1, t + 100
        await s.handle(tap(action, seq))
        await s.idle()
        seq += 1
    await s.close()
    s.rec.end("abandoned")
    return s, tape


async def test_rerun_emits_the_same_s2c_with_no_new_calls(tmp_path: Path) -> None:
    live, tape = await live_session(tmp_path)
    rec = recorder.load(tmp_path, "m1live")
    kinds = [m["type"] for m in rec.s2c()]
    assert "outline" in kinds and [m["verdict"]["result"] for m in rec.s2c() if m["type"] == "verdict"][0] == "done"
    replay = Tape(None, tape.tape)
    r = await rerun(rec, replay, card=acme(), guide=False)
    assert replay.missing == []
    assert r.same, r.diff()[:3]


async def test_the_rerun_session_gets_the_manuals_the_guide_reads(tmp_path: Path, monkeypatch: Any) -> None:
    """A live session's guide requests carry the manual's pages (guide._prefix); a re-run without them sends other
    requests and finds no recording for any guide call."""
    from app import rerun as rerun_mod

    await live_session(tmp_path)
    got: dict[str, Any] = {}

    class Seen(Session):
        def __init__(self, **kw: Any) -> None:
            got.update(kw)
            super().__init__(**kw)

    monkeypatch.setattr(rerun_mod, "Session", Seen)
    manuals = object()
    await rerun(recorder.load(tmp_path, "m1live"), Tape(None, {}), card=acme(), guide=False,
                manuals=manuals)
    assert got["manuals"] is manuals

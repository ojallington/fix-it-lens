"""The session's streams: the guide runs on its own task, so a spoken hazard and a done tap
never wait behind a guide turn, and a stop cancels the turn in flight. A re-run compares order within each stream:
the stop and step path strictly, the guide's sentences among themselves."""

from __future__ import annotations

import asyncio
import base64
import time
from pathlib import Path
from typing import Any

import calls
from app import recorder
from app.guide import Guide
from app.rerun import Rerun, rerun
from contracts.models.safety import Stop
from tests.l1.helpers import frame
from tests.l1.test_guide import resp
from tests.l1.test_session import session
from tests.l1.test_stepcheck import Judge
from tests.l1.test_voice import WAV


class SlowGuide(Judge):
    """The judge fake; the guide's call takes 10 s; the microphone hears "ik ruik gas"."""

    def __init__(self) -> None:
        super().__init__([300, 300])
        self.guide_open = asyncio.Event()
        self.guide_cancelled = False

    async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
        if role != "guide":
            return await super().respond(role, input, **kw)
        self.guide_open.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            self.guide_cancelled = True
            raise
        return resp("Okay, the filter is at the bottom.")

    async def transcribe(self, wav: bytes, bias: Any, keywords: list[str], **kw: Any) -> calls.Transcript:
        return calls.Transcript("ik ruik gas", {}, 300.0, 0.001, "3" * 64)


def said(i: int, *, text: str | None = None) -> dict[str, Any]:
    return {"type": "utterance", "seq": i, "t_ms": 10.0 * i, "utterance_id": f"u{i}", "lang": "nl", "text": text,
            "audio_wav_b64": None if text else base64.b64encode(WAV).decode()}


async def until(pred: Any, within: float) -> float:
    t0 = time.monotonic()
    while not pred():
        assert time.monotonic() - t0 < within, f"not within {within} s"
        await asyncio.sleep(0.01)
    return time.monotonic() - t0


async def guided_session(tmp_path: Path) -> tuple[Any, SlowGuide]:
    c = SlowGuide()
    s = session(tmp_path, c, lang="nl")
    s.guide = Guide(s)
    s.start()
    await s.handle(said(1, text="waar zit het filter?"))
    await asyncio.wait_for(c.guide_open.wait(), 2)  # the guide's call is open, for 10 s
    return s, c


async def test_a_spoken_gas_smell_stops_within_a_second_while_the_guide_is_mid_call(tmp_path: Path) -> None:
    s, c = await guided_session(tmp_path)
    await s.handle(said(2))
    took = await until(lambda: isinstance(s.safety.state, Stop) and any(
        m["type"] == "safety" and m["state"]["state"] == "STOP" for m in s.out), within=1.0)
    assert took < 1.0
    await until(lambda: c.guide_cancelled, within=1.0)  # the turn in flight is cancelled, not left to finish
    assert not any(m["type"] == "sentence" and "bottom" in m["text"] for m in s.out)
    assert any(x.get("kind") == "guide_cancelled" for x in s.rec.lines)
    assert any(x.get("kind") == "call" and x.get("role") == "guide" and x.get("status") == "cancelled"
               and x.get("stream") == "guide" for x in s.rec.lines)
    await s.close()
    # the re-run: the guide call the stop cancelled ends the re-run's turn at the same point, never asks the model
    again = SlowGuide()
    r = await rerun(recorder.load(tmp_path, s.session_id), again, card=s.card)
    assert r.same, r.diff()[:3]
    assert not again.guide_open.is_set()


async def test_a_done_tap_checks_while_the_guide_is_mid_call(tmp_path: Path) -> None:
    s, c = await guided_session(tmp_path)
    for i in range(6):
        s.frames.add(frame(i, i * 100))
    s.power_off_seen = True
    await s.begin_step(1)
    await s.handle({"type": "tap", "seq": 3, "t_ms": 30, "action": "done"})
    await until(lambda: any(m["type"] == "verdict" for m in s.out), within=2.0)
    assert not c.guide_cancelled and c.guide_open.is_set()  # the guide's call is still open
    await s.close()


def test_a_rerun_compares_order_within_each_stream() -> None:
    stop = {"type": "safety", "seq": 0, "state": {"state": "STOP"}}
    verdict = {"type": "verdict", "seq": 1, "verdict": {"result": "done"}}
    a = {"type": "sentence", "seq": 2, "id": "s1", "text": "A."}
    b = {"type": "sentence", "seq": 3, "id": "s2", "text": "B."}
    live = Rerun([a, stop, b, verdict], [], ["guide", "safety", "guide", "safety"], None)
    again = [stop, a, verdict, b]  # another interleaving, the same order within each stream
    assert Rerun(live.live, again, live.live_streams, ["safety", "guide", "safety", "guide"]).same
    swapped = [verdict, a, stop, b]  # the stop and the verdict swapped: a difference
    r = Rerun(live.live, swapped, live.live_streams, ["safety", "guide", "safety", "guide"])
    assert not r.same and {k for k, _, _ in r.diff()} == {"safety#0", "safety#1"}
    assert not Rerun([a, b], [b, a], ["guide", "guide"], ["guide", "guide"]).same  # sentences keep their order

"""Failure paths: on a timeout the outline falls back to the tracked one, the step card keeps the written
step with its page, the screen says what took too long (the check, finding the part, an answer) and never blames the
connection, and nothing passes on a call that did not answer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import calls
from app.session import NOTICE
from tests.l1.helpers import frame
from tests.l1.test_session import session
from tests.l1.test_stepcheck import Judge


class Flaky(Judge):
    """The judge fake, with switches for the outline calls (SAM image, and Spark's locate that stands in when SAM
    fails) and the judge to time out."""

    def __init__(self) -> None:
        super().__init__([300, 300])
        self.sam_down = self.judge_down = False

    async def sam_image(self, frame: bytes, prompt: Any, **kw: Any) -> calls.SamResult:
        if self.sam_down:
            raise calls.CallTimeout("sam: 6 s")
        return await super().sam_image(frame, prompt, **kw)

    async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
        if self.judge_down and role == "judge" or self.sam_down and role == "locate":
            raise calls.CallTimeout(f"{role}: 6 s")
        return await super().respond(role, input, **kw)


def notices(s: Any) -> list[str]:
    return [m["kind"] for m in s.out if m["type"] == "notice"]


def slow(s: Any) -> list[str]:
    return [m["text"] for m in s.out if m["type"] == "notice" and m["kind"] == "slow_connection"]


async def test_sam_timeout_keeps_the_tracked_outline(tmp_path: Path) -> None:
    c = Flaky()
    s = session(tmp_path, c)
    s.frames.add(frame(1, 0))
    first = await s.outline_part("debris_filter")
    assert first is not None and first.region is not None
    drawn = sum(m["type"] == "outline" for m in s.out)
    c.sam_down = True
    s.frames.add(frame(2, 500, shade=30))
    again = await s.outline_part("debris_filter")
    assert again is not None and again.region == first.region and s.regions["debris_filter"] == first.region
    assert sum(m["type"] == "outline" for m in s.out) == drawn  # nothing new is drawn; the phone keeps tracking
    assert slow(s) == [NOTICE["en"]["slow_outline"]]
    assert any(m["type"] == "rail" and "kept the tracked outline" in m["result_short"] for m in s.out)


async def test_sam_timeout_with_nothing_tracked_draws_nothing(tmp_path: Path) -> None:
    c = Flaky()
    c.sam_down = True
    s = session(tmp_path, c)
    s.frames.add(frame(1, 0))
    out = await s.outline_part("debris_filter")
    assert out is not None and out.region is None and "debris_filter" not in s.regions
    assert not any(m["type"] == "outline" for m in s.out) and "slow_connection" in notices(s)


async def test_judge_timeout_is_not_done_and_the_card_stays(tmp_path: Path) -> None:
    c = Flaky()
    s = session(tmp_path, c)
    s.start()
    for i in range(6):
        s.frames.add(frame(i, i * 100))
    s.power_off_seen = True
    await s.begin_step(1)
    card = [m for m in s.out if m["type"] == "step"][-1]
    assert card["step_id"] == "open_flap" and card["text"] and card["page"]
    c.judge_down = True
    v = await s.check("tap done")
    assert v is not None and v.result == "cant_see" and v.witnesses.judge == "cant_see"
    assert slow(s) == [NOTICE["en"]["slow_check"]]
    assert [m for m in s.out if m["type"] == "step"][-1] == card  # no new step: the written step and page stand
    await s.close()


async def test_guide_timeout_says_slow_connection(tmp_path: Path) -> None:
    from app.guide import Guide

    class Down(Flaky):
        async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
            raise calls.CallTimeout(f"{role}: 6 s")

    s = session(tmp_path, Down())
    s.guide = Guide(s)
    await s.guide.turn("hello")
    assert slow(s) == [NOTICE["en"]["slow_answer"]] and s.phase != "stopped"


async def test_cap_reached_is_said_once_and_nothing_passes(tmp_path: Path) -> None:
    class Broke(Flaky):
        async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
            raise calls.BudgetExceeded("household cap")

    s = session(tmp_path, Broke())
    s.start()
    for i in range(6):
        s.frames.add(frame(i, i * 100))
    s.power_off_seen = True
    await s.begin_step(1)
    v1 = await s.check("tap done")
    v2 = await s.check("tap done")
    assert v1 is not None and v2 is not None and v1.result != "done" and v2.result != "done"
    assert notices(s).count("cap_reached") == 1
    await s.close()


async def test_g1_hand_lost_pause_says_so(tmp_path: Path) -> None:
    s = session(tmp_path)
    await s.handle({"type": "g1", "seq": 1, "t_ms": 5, "kind": "pause", "zone_id": "casing", "reason": "hand_lost",
                    "ttc_ms": None, "dist_cm": None, "frame_id": "f1"})
    assert notices(s) == ["hand_lost"]


async def test_lookup_without_manual_text_says_so(tmp_path: Path) -> None:
    from app.guide import Guide
    from contracts.models import tools as T

    s = session(tmp_path)
    s.guide = Guide(s)
    out = await s.guide._lookup_manual(T.LookupManualArgs(query="filter"))
    assert out == {"hits": []} and notices(s) == ["lookup_failed"]


class Unrecorded(Flaky):
    """Replay with no fixture for the outline and the judge calls."""

    async def sam_image(self, frame: bytes, prompt: Any, **kw: Any) -> calls.SamResult:
        raise calls.MissingFixture("no fixture for request")

    async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
        if role in ("locate", "judge"):
            raise calls.MissingFixture("no fixture for request")
        return await super().respond(role, input, **kw)


async def test_missing_fixture_says_no_recording_never_slow_connection(tmp_path: Path) -> None:
    s = session(tmp_path, Unrecorded())
    s.start()
    for i in range(6):
        s.frames.add(frame(i, i * 100))
    s.power_off_seen = True
    await s.outline_part("debris_filter")
    await s.begin_step(1)
    v = await s.check("tap done")
    assert v is not None and v.result == "cant_see"
    said = [m for m in s.out if m["type"] == "notice"]
    assert [m["kind"] for m in said] == ["no_recording"]  # once, and no slow_connection
    assert said[0]["text"] == "Replay has no recording for this call."
    assert any(x.get("status") == "missing" for x in s.rec.lines if x.get("kind") == "call")
    await s.close()

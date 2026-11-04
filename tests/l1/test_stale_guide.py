"""The guide's words belong to the step they were written on. A reply whose step the job has left (a check passed or
the person confirmed while the guide waited) is dropped, never shown under the next step; a wait for a new view ends
when the step does; and a view the last verdict already asked for is not asked for again in the same words."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from app.guide import ASK_VIEW, NO_ANSWER
from app.session import ASK_FOR, Session
from app.spoken import in_lang
from contracts.models.ws import Verdict, Witnesses
from tests.l1.helpers import frame
from tests.l1.test_fixit import done
from tests.l1.test_guide import guided, resp

CLOSER = ("ask_for_view", {"kind": "closer", "reason": "the filter is too small in the frame"})


async def at_open_flap(tmp_path: Path, replies: list[Any]) -> Session:
    s, _ = guided(tmp_path, replies)
    s.start()
    s.frames.add(frame(1, s.clock()))
    s.power_off_seen = True

    async def no_outline(*_: Any, **__: Any) -> None:
        return None

    s.outline_part = no_outline  # type: ignore[method-assign]
    await s.begin_step(1)
    assert s.run is not None and s.run.step.id == "open_flap" and s.phase == "instructed"
    return s


def said(s: Session) -> list[str]:
    """The guide's sentences: without the session's own asks for a step's precondition (the next step's zone outline)
    and the steps' own words, said as they are shown."""
    own = set(in_lang(ASK_FOR, s.lang).values()) | {m["text"] for m in s.out if m["type"] == "step"}  # type: ignore[attr-defined]
    return [m["text"] for m in s.out if m["type"] == "sentence" and m["text"] not in own]  # type: ignore[attr-defined]


async def turn_with_a_new_frame(s: Session, n: int) -> None:
    """A guide turn while the person moves the camera: a new frame comes during the wait."""
    async def moved() -> None:
        await asyncio.sleep(0.1)
        s.frames.add(frame(n, s.clock()))

    await asyncio.gather(s.guide.turn(None), moved())  # type: ignore[union-attr]


async def test_a_reply_about_a_step_the_job_has_left_is_dropped(tmp_path: Path) -> None:
    s = await at_open_flap(tmp_path, [resp("Open the flap at the bottom right.", calls_=[CLOSER]),
                                      resp("Now unscrew it anticlockwise.")])
    guide = s.guide
    assert guide is not None

    async def passes(name: str, arguments: str) -> dict[str, Any]:  # the check passes while the guide waits
        await s._verdict(done("open_flap"))
        return {"new_frame": False}

    guide.call = passes  # type: ignore[method-assign]
    await guide.turn("where is the flap?")
    assert s.run is not None and s.run.step.id == "refit_filter"
    assert said(s) == [] and NO_ANSWER["en"] not in said(s)  # nothing about open_flap shows under refit_filter
    assert guide.rounds == 1  # no second round on a step that is over
    assert any(m["type"] == "rail" and "reply dropped" in m["result_short"] for m in s.out)  # type: ignore[attr-defined]
    [line] = [x for x in s.rec.lines if x["dir"] == "state" and x.get("kind") == "guide_dropped"]
    assert line["step_id"] == "open_flap"
    await s.close()


async def test_a_reply_on_the_same_step_is_said(tmp_path: Path) -> None:
    s = await at_open_flap(tmp_path, [resp("Open the flap at the bottom right.")])
    await s.guide.turn("where is the flap?")  # type: ignore[union-attr]
    assert said(s) == ["Open the flap at the bottom right."]
    await s.close()


async def test_the_wait_for_a_new_view_ends_when_the_step_does(tmp_path: Path) -> None:
    s = await at_open_flap(tmp_path, [resp(calls_=[CLOSER]), resp("Closer is better.")])

    async def confirm() -> None:
        await asyncio.sleep(0.2)
        await s._verdict(done("open_flap"))

    t0 = time.monotonic()
    await asyncio.gather(s.guide.turn(None), confirm())  # type: ignore[union-attr]
    assert time.monotonic() - t0 < 3  # not the 20 s wait
    assert said(s) == [ASK_VIEW["en"]["closer"]] and s.run is not None and s.run.step.id == "refit_filter"
    await s.close()


async def test_a_view_the_verdict_asked_for_is_not_asked_again_in_the_same_words(tmp_path: Path) -> None:
    s = await at_open_flap(tmp_path, [resp(calls_=[CLOSER]), resp(""), resp(calls_=[CLOSER]), resp("")])
    assert s.run is not None
    s.run.attempts += 1
    await s._verdict(Verdict(step_id="open_flap", check_id="k1", attempt=1, result="cant_see", clause_ids=["c1"],
                             ask_for="closer", latency_ms=0, witnesses=Witnesses(judge="cant_see", sam="none")))
    await turn_with_a_new_frame(s, 2)
    assert ASK_VIEW["en"]["closer"] not in said(s)  # the verdict on screen already asks for it
    await turn_with_a_new_frame(s, 3)
    assert said(s).count(ASK_VIEW["en"]["closer"]) == 1  # asked again later: said once
    await s.close()

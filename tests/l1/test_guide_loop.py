"""The guide's turns nobody asked for are bounded: a verdict starts at most one, and a check that turn asks for starts
none; none while the guide waits on the person after asking for a new view (words, a tap or a changed view end the
wait); none once the step has used its guide rounds. The person's own words always get a turn."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import calls
from app import config
from app.session import Session
from tests.l1.helpers import frame
from tests.l1.test_guide import Scripted, guided, resp

OUTLINE = calls.SamResult(1000, 1000, [calls.SamObject("o1", (200, 200, 400, 400), 0.9,
                                                         "M 200 200 L 400 200 L 400 400 L 200 400 Z", 0)],
                          "", 5.0, 0.002, "0" * 64)


class Seeing(Scripted):
    """The scripted guide, with a SAM that outlines the step's parts."""

    async def sam_image(self, frame: bytes, prompt: Any, **kw: Any) -> calls.SamResult:
        return OUTLINE


class AlwaysCheck(Seeing):
    """A guide that asks for a check every round, as M1's did while the camera could not see the filters."""

    async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
        self.inputs.append(input)
        return resp("Let me look again.", [("check_step", {"step_id": "open_flap"})])


async def ready(tmp_path: Path, client: Seeing) -> tuple[Session, list[str]]:
    s, _ = guided(tmp_path, [])
    s.client = s.models.client = client  # type: ignore[assignment]
    checks: list[str] = []

    async def cant_see(reason: str) -> None:  # the camera never sees it: no verdict passes, the step stays
        checks.append(reason)

    s.check = cant_see  # type: ignore[method-assign]
    s.start()
    for i in range(6):
        s.frames.add(frame(i, i * 100))
    s.power_off_seen = True
    await s.begin_step(1)
    assert s.phase == "instructed" and s.run is not None and s.run.step.id == "open_flap"
    return s, checks


async def test_repeated_cant_see_makes_a_bounded_number_of_guide_calls(tmp_path: Path) -> None:
    guide = AlwaysCheck([])
    s, checks = await ready(tmp_path, guide)
    await s.handle({"type": "tap", "seq": 1, "t_ms": 0, "action": "done"})
    await asyncio.wait_for(s.idle(), 5)  # it settles: no verdict feeds another turn for ever
    # the tap's check starts one turn; that turn's own checks start none (the doom guard ends it at 3 calls)
    assert len(guide.inputs) <= config.DOOM_REPEATS
    assert checks[0] == "tap done" and len(checks) <= 1 + config.DOOM_REPEATS
    await s.close()


async def test_a_turn_that_asked_for_a_view_waits_for_the_person(tmp_path: Path) -> None:
    guide = Seeing([resp("", [("ask_for_view", {"kind": "closer", "reason": "too far"})]), resp("Closer, please.")])
    s, _ = await ready(tmp_path, guide)
    config_wait = config.ASK_FOR_VIEW_WAIT_S
    config.ASK_FOR_VIEW_WAIT_S = 0.05
    try:
        await s.handle({"type": "utterance", "seq": 1, "t_ms": 0, "utterance_id": "u1", "lang": "en",
                        "text": "is this right?", "audio_wav_b64": None})
        await asyncio.wait_for(s.idle(), 5)
    finally:
        config.ASK_FOR_VIEW_WAIT_S = config_wait
    asked = len(guide.inputs)
    s.queue_check("the guide asked")  # a verdict while the guide waits on the person: no turn
    await asyncio.wait_for(s.idle(), 5)
    assert len(guide.inputs) == asked
    same = "0" * 15 + "7"  # a frame much like the last: still waiting
    await s.handle({"type": "frame", "seq": 2, "t_ms": 700, "frame_id": "f7", "phash": same, "width": 64,
                    "height": 48, "jpeg_1024": __import__("base64").b64encode(frame(7, 700).jpeg).decode()})
    s.queue_check("the guide asked")
    await asyncio.wait_for(s.idle(), 5)
    assert len(guide.inputs) == asked
    moved = "f" * 16  # the view changed: the wait is over, and the next verdict gets its turn
    await s.handle({"type": "frame", "seq": 3, "t_ms": 800, "frame_id": "f8", "phash": moved, "width": 64,
                    "height": 48, "jpeg_1024": __import__("base64").b64encode(frame(8, 800).jpeg).decode()})
    s.queue_check("the guide asked")
    await asyncio.wait_for(s.idle(), 5)
    assert len(guide.inputs) == asked + 1
    await s.close()


async def test_a_step_out_of_guide_rounds_answers_words_but_starts_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(config, "GUIDE_STEP_ROUNDS", 1)
    guide = Seeing([])
    s, _ = await ready(tmp_path, guide)
    await s.handle({"type": "utterance", "seq": 1, "t_ms": 0, "utterance_id": "u1", "lang": "en",
                    "text": "where is it?", "audio_wav_b64": None})
    await asyncio.wait_for(s.idle(), 5)
    assert len(guide.inputs) == 1  # the person's words got their answer
    await s.handle({"type": "tap", "seq": 2, "t_ms": 10, "action": "done"})
    await asyncio.wait_for(s.idle(), 5)
    assert len(guide.inputs) == 1  # the verdict starts no turn: the step's rounds are used
    await s.handle({"type": "utterance", "seq": 3, "t_ms": 20, "utterance_id": "u2", "lang": "en",
                    "text": "and now?", "audio_wav_b64": None})
    await asyncio.wait_for(s.idle(), 5)
    assert len(guide.inputs) == 2  # words still do
    await s.close()

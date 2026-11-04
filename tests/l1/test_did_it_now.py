"""The person's "I did it, continue" confirms the step at once: it never waits on the camera's work for that step. A
tap that comes while the step's parts are still being found and outlined is confirmed before they are, and their
outline, for a step the job has left, is never drawn; a tap that comes while the camera checks the step cuts the check
short, and only the person's verdict comes. Where their word is refused (a hold), the check runs on."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app import judge, locate, stepcheck
from app.session import Session
from app.verdict import JudgeFinding, SamWitness
from tests.l1.test_did_it import at_the_filter
from tests.l1.test_session import tap


def verdicts(s: Session) -> list[dict[str, Any]]:
    return [m["verdict"] for m in s.out if m["type"] == "verdict"]  # type: ignore[attr-defined]


def states(s: Session, kind: str) -> list[dict[str, Any]]:
    return [x for x in s.rec.lines if x["dir"] == "state" and x.get("kind") == kind]


async def until(cond: Any) -> None:
    for _ in range(200):
        if cond():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("never happened")


async def test_a_tap_while_the_step_is_outlined_is_confirmed_first_and_the_outline_dropped(
        tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    s, _ = await at_the_filter(tmp_path)
    del s.outline_part  # the real one, on a locate and SAM that take their time
    release, asked = asyncio.Event(), []

    async def slow_outline(models: Any, part: Any, f: Any, lang: str) -> tuple[Any, None]:
        asked.append(part.id)
        await release.wait()
        return object(), None

    monkeypatch.setattr(locate, "outline", slow_outline)
    assert s.run is not None
    await s.begin_step(s.run.index)  # refit_filter shown again: its parts go to locate
    await until(lambda: asked)
    await s.handle(tap("did_it", 7))
    await s._queue.join()  # the worker is done: the outline still waits
    assert not release.is_set() and verdicts(s)[-1]["step_id"] == "refit_filter"
    assert verdicts(s)[-1]["witnesses"] == {"sam": "none", "by_person": True}
    assert s.run.step.id == "plug_back_in"
    n = len(s.out)  # type: ignore[attr-defined]
    release.set()
    await s.idle()
    assert not [m for m in s.out[n:] if m["type"] in ("outline", "zone")]  # type: ignore[attr-defined]
    assert [x["step_id"] for x in states(s, "outline_dropped")] == ["refit_filter"]
    assert "debris_filter" not in s.regions
    await s.close()


async def blocked_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Session, asyncio.Event]:
    s, _ = await at_the_filter(tmp_path)
    s.power_off_seen = True
    release = asyncio.Event()

    async def judge_and_witness(*_: Any) -> tuple[Any, JudgeFinding, SamWitness]:
        await release.wait()
        return judge.JudgeRun(None, 1, 0.0, False), JudgeFinding("done", ["c1"], None, ""), SamWitness("frame", "done")

    monkeypatch.setattr(stepcheck, "judge_and_witness", judge_and_witness)
    await s.handle(tap("done", 6))
    await until(lambda: s.phase == "checking")
    return s, release


async def test_a_tap_while_the_camera_checks_cuts_the_check_short(tmp_path: Path,
                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    s, release = await blocked_check(tmp_path, monkeypatch)
    await s.handle(tap("did_it", 7))
    await s.idle()
    [v] = [v for v in verdicts(s) if v["step_id"] == "refit_filter"]
    assert v["witnesses"] == {"sam": "none", "by_person": True}  # the person's word, never the camera's
    assert s.run is not None and s.run.step.id == "plug_back_in" and "refit_filter" in s.by_person
    assert [x["step_id"] for x in states(s, "check_cancelled")] == ["refit_filter"]
    release.set()
    await s.close()


async def test_under_a_hold_the_tap_is_refused_and_the_check_runs_on(tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    s, release = await blocked_check(tmp_path, monkeypatch)
    await s._user_hold("stopped by the user")
    await s.handle(tap("did_it", 7))
    assert not states(s, "check_cancelled")
    release.set()
    await s.idle()
    [v] = [v for v in verdicts(s) if v["step_id"] == "refit_filter"]
    assert "by_person" not in v["witnesses"] and not s.by_person
    await s.close()

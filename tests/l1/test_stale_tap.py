"""A "done" or "I did it" is about the step the person saw: one that came in while the job moved on (the look again
at confirmed steps before a start, about 12 s) is refused, never applied to the next step: a tap with the notice
tap_not_current, spoken words with a line that says so.
The look again shows as checking, except at the job's end; a step it reopens says why; a "yes" whose look does not see
the plug out says so."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app import power
from app.session import PLUG_UNSEEN, REOPENED, STALE, Session, split_sentences
from app.verdict import JudgeFinding, SamWitness
from tests.l1.test_did_it import at_the_filter
from tests.l1.test_did_it_now import states, until, verdicts
from tests.l1.test_session import tap


def said(s: Session) -> list[dict[str, Any]]:
    return [m for m in s.out if m["type"] == "sentence"]  # type: ignore[attr-defined]


class SlowLook:
    """The look again before the start, held until released; then it sees what it is given."""

    def __init__(self, s: Session, judge: str = "cant_see", sam: str | None = None) -> None:
        self.release, self.steps, self.seen = asyncio.Event(), [], (judge, sam)  # type: list[str]
        s._look_again = self  # type: ignore[method-assign]

    async def __call__(self, step: Any) -> tuple[JudgeFinding, SamWitness]:
        self.steps.append(step.id)
        await self.release.wait()
        judge, sam = self.seen
        return JudgeFinding(judge, [], None, ""), SamWitness("none" if sam is None else "frame", sam)  # type: ignore[arg-type]


async def test_a_second_tap_during_the_look_again_is_refused_not_applied_to_the_next_step(tmp_path: Path) -> None:
    s, hooks = await at_the_filter(tmp_path)
    look = SlowLook(s)
    await s.handle(tap("did_it", 5))  # refit_filter confirmed: before plugging back in, it is looked at again
    await until(lambda: look.steps)
    [chk] = [m for m in s.out if m["type"] == "checking"]  # type: ignore[attr-defined]
    assert chk["step_id"] == "refit_filter"  # the phone shows the look
    await s.handle(tap("did_it", 6))  # the person still sees refit_filter and taps again
    await s.handle(tap("done", 7))
    look.release.set()
    await s.idle()
    assert s.run is not None and s.run.step.id == "plug_back_in" and s.phase == "instructed"
    assert [v["step_id"] for v in verdicts(s)][-1] == "refit_filter"  # nothing confirmed or checked plug_back_in
    refused = states(s, "stale_refused")
    assert [(x["what"], x["seen"], x["step_id"]) for x in refused] == [
        ("did_it", "refit_filter", "plug_back_in"), ("done", "refit_filter", "plug_back_in")]
    told = [m["kind"] for m in s.out if m["type"] == "notice"]  # type: ignore[attr-defined]
    assert told.count("tap_not_current") == 2 and not [m for m in said(s) if m["text"] in STALE["en"]]
    await s.close()


async def test_spoken_i_did_it_during_the_look_again_is_refused_too(tmp_path: Path) -> None:
    s, _ = await at_the_filter(tmp_path)
    look = SlowLook(s)
    await s.handle(tap("did_it", 5))
    await until(lambda: look.steps)
    s._queue.put_nowait({"kind": "heard", "msg": None, "text": "I did it", "step_at": "refit_filter"})
    look.release.set()
    await s.idle()
    assert s.run is not None and s.run.step.id == "plug_back_in"
    assert [x["what"] for x in states(s, "stale_refused")] == ["did_it"]
    lines = [m for m in said(s) if m["text"] == split_sentences(STALE["en"])[0]]  # words, not a tap: told in words
    assert len(lines) == 1 and lines[0]["step_id"] == "plug_back_in"
    await s.close()


async def test_a_tap_on_the_step_now_is_applied(tmp_path: Path) -> None:
    s, _ = await at_the_filter(tmp_path)
    SlowLook(s).release.set()
    await s.handle(tap("did_it", 5))
    await s.idle()
    assert s.run is not None and s.run.step.id == "plug_back_in" and not states(s, "stale_refused")
    await s.close()


async def test_the_look_again_at_the_jobs_end_sends_no_checking(tmp_path: Path) -> None:
    s, _ = await at_the_filter(tmp_path)
    look = SlowLook(s)
    look.release.set()
    await s.handle(tap("did_it", 5))
    await s.idle()
    assert look.steps == ["refit_filter"] and s.run is not None and s.run.step.id == "plug_back_in"
    n = len(s.out)  # type: ignore[attr-defined]
    s.stood.clear()  # looked at again at the job's end too
    await s.handle(tap("done", 6))
    await s.idle()
    assert s.phase == "job_done" and look.steps == ["refit_filter", "refit_filter"]
    assert not [m for m in s.out[n:] if m["type"] == "checking"]  # type: ignore[attr-defined]
    await s.close()


async def test_a_reopened_step_says_why_first_with_no_step_id(tmp_path: Path) -> None:
    s, _ = await at_the_filter(tmp_path)
    look = SlowLook(s, "not_done", "not_done")
    look.release.set()
    n = len(s.out)  # type: ignore[attr-defined]
    await s.handle(tap("did_it", 5))
    await s.idle()
    assert s.run is not None and s.run.step.id == "refit_filter" and s.run.reopened
    out = [m for m in s.out[n:] if m["type"] in ("sentence", "step")]  # type: ignore[attr-defined]
    lines = split_sentences(REOPENED["en"])
    assert [m.get("text") for m in out[:len(lines)]] == lines and out[len(lines)]["type"] == "step"
    assert all("step_id" not in m for m in out[:len(lines)])  # the phone left this step: never dropped
    await s.close()


async def test_a_reopened_step_held_by_its_gate_asks_with_no_step_id(tmp_path: Path) -> None:
    s, _ = await at_the_filter(tmp_path)
    look = SlowLook(s, "not_done", "not_done")
    look.release.set()
    s.card.jobs[0].steps[2].power = "off"  # type: ignore[union-attr]
    s.power_off_seen = False  # the plug went back in: the reopened step is held until it is seen out
    n = len(s.out)  # type: ignore[attr-defined]
    await s.handle(tap("did_it", 5))
    await s.idle()
    assert s.run is not None and s.run.step.id == "refit_filter" and s.phase == "held"
    out = [m for m in s.out[n:] if m["type"] in ("sentence", "step")]  # type: ignore[attr-defined]
    assert not [m for m in out if m["type"] == "step"] and out
    assert all("step_id" not in m for m in out)
    await s.close()


async def test_yes_on_the_plug_ask_looks_and_says_when_it_did_not_see_it(tmp_path: Path, monkeypatch: Any) -> None:
    s, _ = await at_the_filter(tmp_path)
    s.power_off_seen = False
    await s.begin_step(0)  # unplug: held until the camera sees the plug out
    assert s.phase == "held" and s.run is not None and s.run.asked == "power_off_seen"
    looked: list[str] = []

    class Unseen:
        ha_w = None
        rail = "no plug in view"

        def witness(self) -> Any:
            return type("W", (), {"result": "cant_see"})()

    async def evidence(*_: Any) -> Any:
        looked.append("plug")
        return Unseen()

    monkeypatch.setattr(power, "power_evidence", evidence)
    await s.handle(tap("yes", 8))
    await s.idle()
    assert looked == ["plug"] and s.phase == "held" and not s.power_off_seen  # the yes only asked for a look
    assert [m["text"] for m in said(s)[-2:]] == split_sentences(PLUG_UNSEEN["en"])
    assert {m["step_id"] for m in said(s)[-2:]} == {"unplug"}
    await s.close()

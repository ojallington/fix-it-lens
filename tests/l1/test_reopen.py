"""Before the start (the job's last manual step, a power-on step, our own step, the job's end) the steps the person
confirmed whose part should still be in place are looked at again. One the camera sees undone reopens: the job goes
back to it, it is no longer confirmed, "I did it" is refused on it, and once a check sees it done the job goes on at
the start it interrupted. A view that shows nothing either way, or a judge alone against a witness that saw it done,
lets the person's word stand; a step that stands is not looked at again."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app import stepcheck
from app.session import Session
from app.verdict import JudgeFinding, SamWitness
from tests.l1.helpers import frame
from tests.l1.test_did_it import Both, at_the_filter
from tests.l1.test_fixit import done
from tests.l1.test_session import tap


def seen(judge: str, sam: str | None) -> tuple[JudgeFinding, SamWitness]:
    return JudgeFinding(judge, [], None, ""), SamWitness("none" if sam is None else "frame", sam)  # type: ignore[arg-type]


class Looks:
    """What the camera sees each time a confirmed step is looked at again."""

    def __init__(self, s: Session, *seen: tuple[JudgeFinding, SamWitness]) -> None:
        self.seen, self.steps = list(seen), []  # type: list[tuple[JudgeFinding, SamWitness]]; list[str]
        s._look_again = self  # type: ignore[method-assign]

    async def __call__(self, step: Any) -> tuple[JudgeFinding, SamWitness]:
        self.steps.append(step.id)
        return self.seen.pop(0)


async def confirmed(tmp_path: Path, *look: tuple[JudgeFinding, SamWitness]) -> tuple[Session, Both, Looks]:
    s, hooks = await at_the_filter(tmp_path)
    looks = Looks(s, *look)
    await s.handle(tap("did_it", 5))
    await s.idle()
    return s, hooks, looks


def steps(s: Session) -> list[tuple[str, int]]:
    return [(m["step_id"], m["index"]) for m in s.out if m["type"] == "step"]  # type: ignore[attr-defined]


async def test_a_confirmed_step_seen_undone_reopens_and_only_a_check_ends_it(tmp_path: Path) -> None:
    s, hooks, looks = await confirmed(tmp_path, seen("not_done", "not_done"))
    assert looks.steps == ["refit_filter"]  # looked at again before plugging back in
    assert s.run is not None and s.run.step.id == "refit_filter" and s.run.reopened and s.phase == "instructed"
    assert steps(s)[-2:] == [("refit_filter", 2), ("refit_filter", 2)] and not s.by_person
    reopened = [x["step_id"] for x in s.rec.lines if x["dir"] == "state" and x.get("kind") == "reopened"]
    assert reopened == ["refit_filter"]
    assert any("not done after all" in e for e in s.events)
    assert await s.did_it() is None  # the person's word does not end it again
    assert s.run.step.id == "refit_filter" and hooks.by_person == 0
    await s._verdict(done("refit_filter"))  # the camera sees it done: on to the start it interrupted
    assert s.run.step.id == "plug_back_in" and steps(s)[-1] == ("plug_back_in", 3)
    assert (hooks.done, hooks.by_person) == (1, 0)  # checked by the camera after all
    await s.close()


async def test_the_judge_alone_against_a_witness_that_saw_it_done_does_not_reopen(tmp_path: Path) -> None:
    s, hooks, looks = await confirmed(tmp_path, seen("not_done", "done"))
    assert s.run is not None and s.run.step.id == "plug_back_in" and not s.run.reopened
    assert hooks.by_person == 1 and s.by_person == {"refit_filter"}
    await s.close()


async def test_the_judge_reopens_when_the_witness_saw_nothing(tmp_path: Path) -> None:
    s, _, _ = await confirmed(tmp_path, seen("not_done", None))
    assert s.run is not None and s.run.step.id == "refit_filter" and s.run.reopened
    await s.close()


async def test_a_view_that_shows_nothing_lets_the_word_stand_and_it_is_looked_at_once(tmp_path: Path) -> None:
    s, hooks, looks = await confirmed(tmp_path, seen("cant_see", None))
    assert s.run is not None and s.run.step.id == "plug_back_in" and hooks.by_person == 1
    rail = [m["result_short"] for m in s.out if m["type"] == "rail" and m["tool"] == "check_step"]  # type: ignore[attr-defined]
    assert rail == ["could not see it: their word stands"]  # never "still done" when nothing was seen
    await s.handle(tap("done", 6))  # our step: the job's end is a start too, and it stood already
    await s.idle()
    assert s.phase == "job_done" and looks.steps == ["refit_filter"]
    await s.close()


async def test_a_step_ours_restores_or_without_a_witness_is_not_looked_at(tmp_path: Path) -> None:
    s, _ = await at_the_filter(tmp_path)
    job = s.job
    assert job is not None
    s.by_person = {"unplug", "open_flap", "refit_filter"}
    assert [st.id for st in job.steps if s.rechecked(st)] == ["refit_filter"]
    assert [i for i in range(len(job.steps) + 1) if s.before_start(i)] == [2, 3, 4]
    await s.close()


async def test_the_look_again_is_the_check_on_the_view_now(tmp_path: Path, monkeypatch: Any) -> None:
    s, _ = await at_the_filter(tmp_path)
    del s._look_again  # the real one
    asked: list[Any] = []

    async def judge_and_witness(models: Any, step: Any, lang: str, frames: Any, regions: Any, text: str,
                                provider: Any) -> tuple[Any, JudgeFinding, SamWitness]:
        asked.append((step.id, frames.now1.frame_id, frames.now2.frame_id))
        return None, *seen("done", "done")

    async def no_region(*_: Any) -> None:
        return None

    monkeypatch.setattr(stepcheck, "judge_and_witness", judge_and_witness)
    s.region_on = no_region  # type: ignore[method-assign]
    s.frames.add(frame(7, s.clock()))
    s.frames.add(frame(8, s.clock()))
    job = s.job
    assert job is not None
    finding, sam = await s._look_again(job.steps[2])
    assert (finding.result, sam.result) == ("done", "done") and [a[0] for a in asked] == ["refit_filter"]
    await s.close()

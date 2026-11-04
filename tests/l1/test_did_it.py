"""On every step the person may say they did it ("I did it, continue", tap did_it): the step ends in a by_person
verdict, and the record and any claim say confirmed by you, never checked. After MAX_CHECK_ATTEMPTS checks that could
not see it, the guide is told to point to it. Never while a hold or stop stands or a check runs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app import config
from app.session import Session
from app.verdict import JudgeFinding, SamWitness
from contracts import load_card
from contracts.models.ws import Verdict, Witnesses
from tests.l1.helpers import FakeClient, frame
from tests.l1.test_fixit import Counted, card_data, done
from tests.l1.test_session import PLUG_OUT, session, tap


class Both(Counted):
    def __init__(self) -> None:
        super().__init__()
        self.by_person = 0

    def job_done_by_person(self, s: Session) -> None:
        self.by_person += 1


async def at_the_filter(tmp_path: Path) -> tuple[Session, Both]:
    hooks = Both()
    s = session(tmp_path, FakeClient(images=PLUG_OUT, image=PLUG_OUT["plug"]), hooks=hooks)
    data = card_data()
    for st in data["jobs"][0]["steps"]:  # no zone to outline first: here only the step order matters
        st["zones"], st["zone_rings"] = [], []
    s.card = load_card(data)

    async def no_outline(*_: Any, **__: Any) -> None:
        return None

    async def nothing_seen(*_: Any) -> tuple[JudgeFinding, SamWitness]:  # the look before the start: see test_reopen
        return JudgeFinding("cant_see", [], "closer", ""), SamWitness("none", None)

    s.outline_part = no_outline  # type: ignore[method-assign]
    s._look_again = nothing_seen  # type: ignore[method-assign]
    s.start()
    s.frames.add(frame(1, s.clock()))
    await s.start_job()
    await s.handle(tap("yes"))
    await s.idle()
    for sid in ("unplug", "open_flap"):
        assert s.run is not None and s.run.step.id == sid
        await s._verdict(done(sid))
    assert s.run is not None and s.run.step.id == "refit_filter" and s.phase == "instructed"
    return s, hooks


def offers(s: Session) -> int:
    return sum(1 for m in s.out if m["type"] == "notice" and m["kind"] == "did_it_offered")  # type: ignore[attr-defined]


async def cant_see(s: Session, n: int) -> None:
    """n checks that could not see the step, as check() ends them."""
    for _ in range(n):
        assert s.run is not None
        s.run.attempts += 1
        await s._verdict(Verdict(step_id=s.run.step.id, check_id="k", attempt=s.run.attempts, result="cant_see",
                                 clause_ids=["c1"], ask_for="closer", latency_ms=0,
                                 witnesses=Witnesses(judge="cant_see", sam="none")))


async def test_the_offer_comes_once_after_the_last_check_that_could_not_see(tmp_path: Path) -> None:
    s, _ = await at_the_filter(tmp_path)
    await cant_see(s, config.MAX_CHECK_ATTEMPTS - 1)
    assert offers(s) == 0
    await cant_see(s, 2)
    assert offers(s) == 1  # once per step, not once per check
    await s.close()


async def test_did_it_is_there_from_the_first_look(tmp_path: Path) -> None:
    s, hooks = await at_the_filter(tmp_path)
    assert s.run is not None and s.run.attempts == 0
    await s.handle(tap("did_it", 5))
    await s.idle()
    assert s.run is not None and s.run.step.id == "plug_back_in" and hooks.by_person == 1
    await s.close()


async def test_did_it_does_nothing_while_the_step_holds(tmp_path: Path) -> None:
    s = (await at_the_filter(tmp_path))[0]
    s.power_off_seen = False
    await s.start_job()  # back at unplug: it holds until the camera sees the plug out
    assert s.phase == "held" and s.run is not None and s.run.step.id == "unplug"
    await s.handle(tap("did_it", 5))
    await s.idle()
    assert s.phase == "held" and s.run.step.id == "unplug" and not s.by_person
    await s.close()


async def test_did_it_does_nothing_while_a_check_runs(tmp_path: Path) -> None:
    s, _ = await at_the_filter(tmp_path)
    s.phase = "checking"
    assert await s.did_it() is None
    assert s.run is not None and s.run.step.id == "refit_filter" and not s.by_person
    await s.close()


async def test_did_it_ends_the_step_confirmed_by_you_and_the_job_is_never_called_checked(tmp_path: Path) -> None:
    s, hooks = await at_the_filter(tmp_path)
    await cant_see(s, config.MAX_CHECK_ATTEMPTS)
    n = len(s.out)  # type: ignore[attr-defined]
    await s.handle(tap("did_it", 5))
    await s.idle()
    [v] = [m["verdict"] for m in s.out[n:] if m["type"] == "verdict"]  # type: ignore[attr-defined]
    assert v["step_id"] == "refit_filter" and v["result"] == "done"
    assert v["witnesses"] == {"sam": "none", "by_person": True}  # no judge named: none ran
    out = [m["type"] for m in s.out[n:]]  # type: ignore[attr-defined]
    assert "checking" not in out[:out.index("verdict")]  # no check of the step: the person's word ends it
    assert [m["step_id"] for m in s.out[n:] if m["type"] == "checking"] == ["refit_filter"]  # type: ignore[attr-defined]
    # (the look again before plugging back in, after the verdict)
    assert s.run is not None and s.run.step.id == "plug_back_in"
    assert (hooks.done, hooks.by_person) == (0, 1)  # recorded done on the person's word, never as checked
    states = [x for x in s.rec.lines if x["dir"] == "state" and x.get("kind") == "confirmed_by_person"]
    assert [x["step_id"] for x in states] == ["refit_filter"]
    assert "the person says they did refit_filter: confirmed by them, not checked by the camera" in s.events
    assert "every manual step checked done: the job is recorded as done" not in s.events
    await s.close()


async def test_did_it_does_nothing_while_stopped(tmp_path: Path) -> None:
    s, _ = await at_the_filter(tmp_path)
    await cant_see(s, config.MAX_CHECK_ATTEMPTS)
    await s.handle(tap("stop", 5))
    await s.idle()
    await s.handle(tap("did_it", 6))
    await s.idle()
    assert s.run is not None and s.run.step.id == "refit_filter"
    await s.close()


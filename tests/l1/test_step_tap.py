"""A tap about the step on screen (did_it, done, repeat, next, back, closer) carries the step the person saw. One for
any other step is refused, recorded, and the phone told (notice tap_not_current); without a step_id it is about the
step current when it came in. A step's words are said as well as shown, tagged with the step, once per showing and
again on repeat; a held step text is not said. A view the guide asks for once the job has left its step is not said."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.guide import ASK_VIEW
from app.session import HELD_STEP, Session
from contracts.models import tools as T
from tests.l1.test_did_it import at_the_filter
from tests.l1.test_did_it_now import states
from tests.l1.test_fixit import done
from tests.l1.test_guide import resp
from tests.l1.test_session import tap
from tests.l1.test_stale_guide import at_open_flap


def on(action: str, step_id: str, seq: int) -> dict[str, Any]:
    return {**tap(action, seq), "step_id": step_id}


def sent(s: Session, kind: str) -> list[dict[str, Any]]:
    return [m for m in s.out if m["type"] == kind]  # type: ignore[attr-defined]


async def test_a_tap_for_another_step_is_refused_and_the_phone_told(tmp_path: Path) -> None:
    s, hooks = await at_the_filter(tmp_path)
    for seq, action in enumerate(("did_it", "done", "back", "repeat"), 5):
        n = len(s.out)  # type: ignore[attr-defined]
        await s.handle(on(action, "open_flap", seq))
        await s.idle()
        assert s.run is not None and s.run.step.id == "refit_filter" and s.phase == "instructed"
        new = s.out[n:]  # type: ignore[attr-defined]
        assert [m["kind"] for m in new if m["type"] == "notice"] == ["tap_not_current"]
        assert not [m for m in new if m["type"] in ("step", "checking", "verdict", "sentence")]
    assert [x["what"] for x in states(s, "stale_refused")] == ["did_it", "done", "back", "repeat"]
    assert {x["seen"] for x in states(s, "stale_refused")} == {"open_flap"} and hooks.by_person == 0
    await s.close()


async def test_a_tap_for_the_step_now_is_applied(tmp_path: Path) -> None:
    s, hooks = await at_the_filter(tmp_path)
    await s.handle(on("did_it", "refit_filter", 5))
    await s.idle()
    assert s.run is not None and s.run.step.id == "plug_back_in" and hooks.by_person == 1
    assert not states(s, "stale_refused") and not [m for m in sent(s, "notice") if m["kind"] == "tap_not_current"]
    await s.close()


async def test_the_step_text_is_said_tagged_once_per_showing_and_on_repeat(tmp_path: Path) -> None:
    s, _ = await at_the_filter(tmp_path)
    text = sent(s, "step")[-1]["text"]
    said = [m for m in sent(s, "sentence") if m["text"] == text]
    assert len(said) == 1 and said[0]["step_id"] == "refit_filter"
    out = [m for m in s.out if m["type"] in ("step", "sentence")]  # type: ignore[attr-defined]
    last = max(i for i, m in enumerate(out) if m["type"] == "step")
    assert out[last + 1] is said[0]  # said right after it is shown
    await s.handle(on("repeat", "refit_filter", 5))
    await s.idle()
    assert [m["text"] for m in sent(s, "sentence")].count(text) == 2 and sent(s, "step")[-1]["text"] == text
    await s.close()


async def test_a_held_step_text_is_shown_as_held_and_never_said(tmp_path: Path) -> None:
    s, _ = await at_the_filter(tmp_path)
    text = sent(s, "step")[-1]["text"]
    gate = s.gate

    async def holds_the_step(t: str | None, **kw: Any) -> str | None:  # G0 holds this step's words
        return None if t == text else await gate(t, **kw)

    s.gate = holds_the_step  # type: ignore[method-assign]
    n = len(s.out)  # type: ignore[attr-defined]
    await s.handle(tap("back", 5))  # open_flap shown again, then refit_filter after its check
    await s.idle()
    await s._verdict(done("open_flap"))
    assert s.run is not None and s.run.step.id == "refit_filter" and sent(s, "step")[-1]["text"] == HELD_STEP["en"]
    said = [m["text"] for m in s.out[n:] if m["type"] == "sentence"]  # type: ignore[attr-defined]
    assert text not in said and HELD_STEP["en"] not in said
    await s.handle(tap("repeat", 7))
    await s.idle()
    assert HELD_STEP["en"] not in [m["text"] for m in sent(s, "sentence")]
    await s.close()


async def test_a_view_asked_for_after_the_job_left_the_step_is_not_said(tmp_path: Path) -> None:
    s = await at_open_flap(tmp_path, [resp("")])
    guide = s.guide
    assert guide is not None
    guide.step_at = "open_flap"
    await s._verdict(done("open_flap"))  # the job moves on while the guide's call is on its way
    n = len(s.out)  # type: ignore[attr-defined]
    await guide._ask_for_view(T.AskForViewArgs(kind="closer", reason="too small"))
    assert ASK_VIEW["en"]["closer"] not in [m.get("text") for m in s.out[n:]]  # type: ignore[attr-defined]
    await s.close()

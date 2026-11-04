"""A sentence or held line about a step carries that step's id, so the phone can let it go once the step has passed:
the guide's words carry the step its turn began on, the session's own asks and a step's held text carry their step, and
words about no step (the disclosure, the stop script) carry none. Every message still validates against the contract."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.session import Session
from contracts.models import ws
from tests.l1.test_guide import resp
from tests.l1.test_session import HELD
from tests.l1.test_stale_guide import at_open_flap


def lines(s: Session) -> list[dict[str, Any]]:
    out = [m for m in s.out if m["type"] in ("sentence", "held")]  # type: ignore[attr-defined]
    for m in out:
        (ws.Sentence if m["type"] == "sentence" else ws.Held).model_validate(m)
    return out


async def test_the_guides_words_carry_the_step_they_are_about(tmp_path: Path) -> None:
    s = await at_open_flap(tmp_path, [resp(f"Open the flap at the bottom right. {HELD}")])
    n = len(s.out)  # type: ignore[attr-defined]
    await s.guide.turn("where is the flap?")  # type: ignore[union-attr]
    said = lines(s)[-2:]
    assert sorted(m["type"] for m in said) == ["held", "sentence"] and len(s.out) > n  # type: ignore[attr-defined]
    assert {m["step_id"] for m in said} == {"open_flap"}
    await s.close()


async def test_a_held_text_inside_a_step_message_carries_its_step_and_words_about_no_step_carry_none(
        tmp_path: Path) -> None:
    s = await at_open_flap(tmp_path, [])
    await s.say("Fix-It Lens reads your manual.")
    assert "step_id" not in lines(s)[-1]
    assert await s.gate(HELD, step_id="open_flap") is None
    held = lines(s)[-1]
    assert held["type"] == "held" and held["step_id"] == "open_flap" and held["sentence_id"] is None
    assert await s.gate(HELD) is None and "step_id" not in lines(s)[-1]
    await s.close()

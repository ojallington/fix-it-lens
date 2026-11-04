"""G0 reads each sentence with the one before it, so a line of ours that starts on a bare pronoun ("Unplug it first")
can read as work on whatever the sentence before named (a socket, a gas pipe). Every line the app gates passes G0 after
such a sentence exactly as it passes alone."""

from __future__ import annotations

from typing import Any

from app import guide, session, spoken, voice
from contracts import load_card
from rules import g0
from tests.l1.test_fixit import card_data

BEFORE = {"en": ["Disconnect the mains plug from the mains socket.", "Find the gas pipe.",
                 "Look at the wires behind the panel.", "Find the socket."],
          "nl": ["Haal de stekker uit het stopcontact.", "Zoek de gasleiding.",
                 "Kijk naar de draden achter het paneel.", "Zoek het stopcontact."]}


def lines(x: Any, lang: str | None = None) -> list[tuple[str, str]]:
    if isinstance(x, dict):
        return [ln for k, v in x.items() for ln in lines(v, k if k in BEFORE else lang)]
    return [(lang, x.format(page=41, n=9, m=20))] if isinstance(x, str) and lang else []


def test_no_app_line_reads_as_work_on_what_the_sentence_before_named() -> None:
    card = load_card(card_data())
    ours = [ln for d in (session.ASK_FOR, session.HELD_STEP, session.POWER_LINE, session.ZONE_LINE, session.NOTICE,
                         session.GAS_CONFIRM, session.GAS_AGAIN, session.OPENING, session.STALE, session.REOPENED,
                         session.PLUG_UNSEEN, guide.ASK_VIEW, guide.STUCK,
                         guide.NO_ANSWER, voice.AGAIN, spoken.REST) for ln in lines(d)]
    assert len(ours) > 40 and ("en", session.POWER_LINE["en"]["off"]) in ours
    for lang, line in ours:
        alone = type(g0.check_sentence(line, lang, card, None, country="nl"))
        for before in BEFORE[lang]:
            assert type(g0.check_sentence(line, lang, card, None, country="nl", before=before)) is alone, (line, before)

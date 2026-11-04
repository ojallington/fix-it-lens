"""A step's quote in English (polish finding 21, contracts 1.9 Quote.en): sent as quote_en under the manual's words,
through G0 like the step text (a model wrote it); a held translation refuses its job when the card loads."""

from __future__ import annotations

from pathlib import Path

from app import cards
from contracts.models.card import Card
from tests.l1.helpers import FakeClient, frame
from tests.l1.test_session import HELD, PLUG_OUT, acme, session, tap


def with_en(card: Card, job_id: str, en: str) -> Card:
    jobs = []
    for j in card.jobs:
        if j.id == job_id:
            steps = [st.model_copy(update={"quote": st.quote.model_copy(update={"en": en})}) for st in j.steps]
            j = j.model_copy(update={"steps": steps})
        jobs.append(j)
    return card.model_copy(update={"jobs": jobs})


def test_a_held_translation_refuses_its_job_at_load() -> None:
    card = acme()
    job = card.jobs[0].id
    loaded = cards.check_card(with_en(card, job, HELD), "nl", None)
    assert job in loaded.refused and "(en) held by" in loaded.refused[job]
    assert job not in cards.check_card(with_en(card, job, "Pull the plug out of the socket."), "nl", None).refused


async def test_the_step_carries_quote_en_tidied(tmp_path: Path) -> None:
    s = session(tmp_path, FakeClient(images=PLUG_OUT))
    s.card = with_en(s.card, "clean_debris_filter", "Pull the plug out of the socket .")  # type: ignore[arg-type]
    s.card.jobs[0].steps[0].quote.text = s.card.jobs[0].steps[0].quote.text.rstrip(".") + " ."  # type: ignore[union-attr]
    s.start()
    s.frames.add(frame(1, s.clock()))
    await s.start_job()
    await s.handle(tap("yes"))
    await s.idle()
    step = next(m for m in s.out if m["type"] == "step")
    assert step["quote_en"] == "Pull the plug out of the socket." and not step["quote"].endswith(" .")
    await s.close()

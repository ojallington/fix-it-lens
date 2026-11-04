"""A job's needs at load: a card never invents one. A need whose quote fails leaves, a need whose words G0 holds leaves,
and the job and the card stay usable either way."""

from __future__ import annotations

from typing import Any

import pytest

from app import cards
from app.ports import Page, QuoteCheck
from calls.config import ROOT
from contracts.models.card import Card

BOSCH = ROOT / "cards/bosch/SMV4HAX40N.yaml"
INVENTED = "Houd een nieuwe pakking van de fabrikant bij de hand voordat u begint."


class Manuals:
    """Every quote passes on its page, except the one invented here."""

    def manual_pages(self, brand: str, model: str) -> list[Page]:
        return [Page(page=1, text="x")]

    def verify(self, pages: list[Page], page: int, quote: str) -> QuoteCheck:
        ok = quote != INVENTED
        return QuoteCheck(verified=ok, page=page, reason=None if ok else "not on the page")


def need(nid: str, what: dict[str, str], ref: str, kind: str = "tool") -> dict[str, Any]:
    return {"id": nid, "what": what, "kind": kind, "quote_ref": ref}


@pytest.fixture(scope="module")
def raw() -> Card:
    data = cards.read(BOSCH).model_dump(mode="json")
    data["quotes"].append({"id": "q_invented_gasket", "text": INVENTED, "lang": "nl", "page": 42,
                           "en": "Keep a new gasket from the maker to hand before you start."})
    job = next(j for j in data["jobs"] if j["id"] == "machine_cleaning")
    ref = job["steps"][0]["done_when"][0]["quote_ref"]
    job["needs"] = [need("cloth", {"en": "damp cloth", "nl": "vochtige doek"}, ref),
                    need("wiring", {"en": "the cover off the live wiring"}, ref),
                    need("gasket", {"en": "new gasket", "nl": "nieuwe pakking"}, "q_invented_gasket", "consumable")]
    return Card.model_validate(data)


def test_a_need_g0_holds_or_whose_quote_fails_leaves_and_the_job_stays(raw: Card) -> None:
    loaded = cards.check_card(raw, "nl", Manuals())
    job = next(j for j in loaded.card.jobs if j.id == "machine_cleaning")
    assert [cards.slug(n.id) for n in job.needs or []] == ["cloth"]
    assert set(loaded.needs_held) == {"machine_cleaning/wiring", "machine_cleaning/gasket"}
    assert "nl.mains.wiring" in loaded.needs_held["machine_cleaning/wiring"]
    assert "failed the citation check" in loaded.needs_held["machine_cleaning/gasket"]
    # the invented quote only a need cites leaves with it: the card's citation still passes and the card is usable
    assert loaded.usable and loaded.card.checks is not None and loaded.card.checks.citation.status == "pass"


def test_a_failed_quote_anything_else_cites_still_fails_the_card(raw: Card) -> None:
    data = raw.model_dump(mode="json")
    job = next(j for j in data["jobs"] if j["id"] == "machine_cleaning")
    job["steps"][0]["done_when"][0]["quote_ref"] = "q_invented_gasket"
    loaded = cards.check_card(Card.model_validate(data), "nl", Manuals())
    assert not loaded.usable and any("q_invented_gasket" in f for f in loaded.failures)

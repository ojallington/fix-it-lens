"""Our own step (origin fixit: plug it back in): G0 checks its text, the citation check and the manual pages skip it,
the step message carries origin and no page or quote, the guide's cite says it is ours, and the user's word ends it
in a by_person verdict that names no judge. The job counts as done when its last manual step passes the camera, once."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from app import cards, recorder, trace
from app.guide import Guide
from app.note import build_note
from app.ports import Page, QuoteCheck
from app.server import cited_pages
from app.session import NoHooks, Session
from contracts import ContractError, load_card
from contracts.models import tools as T
from contracts.models.card import Card
from contracts.models.ws import Verdict, Witnesses
from tests.l1.helpers import FakeClient, frame
from tests.l1.test_session import HELD, PLUG_OUT, ROOT, session, tap

OURS = {"id": "plug_back_in", "origin": "fixit", "restores": "unplug", "text": {"en": "Plug the machine back in."},
        "page": None, "quote": None, "done_when": [], "parts": [], "power": "any", "zones": [], "zone_rings": [],
        "view": "front", "look_only_lines": [], "witness": None}


def card_data(step: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.loads((ROOT / "contracts/examples/card/acme-wm-0000.json").read_text())
    for z in data["red_zones"]:
        z["rule_id"] = "nl.mains.casing"
    data["jobs"][0]["steps"].append(step or dict(OURS))
    return data


def ours(**step: Any) -> Card:
    return load_card(card_data({**OURS, **step}))


class Asked:
    """Manual pages that pass every quote, and remember which were asked."""

    def __init__(self) -> None:
        self.quotes: list[str] = []

    def manual_pages(self, brand: str, model: str) -> list[Page]:
        return [Page(4, "the manual")]

    def verify(self, pages: list[Page], page: int, quote: str) -> QuoteCheck:
        self.quotes.append(quote)
        return QuoteCheck(True, page, None, "")


def test_the_card_loads_and_its_own_rules_hold(tmp_path: Path) -> None:
    card = ours()
    assert card.jobs[0].steps[-1].origin == "fixit" and cited_pages(card) == cited_pages(load_card(card_data()))
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(card_data({**OURS, "restores": "nothing"})))
    with pytest.raises(ContractError):  # the loader refuses a fixit step that restores no earlier power-off step
        cards.read(bad)


def test_the_citation_check_skips_it_and_g0_still_reads_it() -> None:
    m = Asked()
    loaded = cards.check_card(ours(), "nl", m)  # type: ignore[arg-type]
    assert loaded.usable and "clean_debris_filter" not in loaded.refused
    assert len(m.quotes) == sum(1 for j in loaded.card.jobs for s in j.steps if s.quote is not None) + \
        len(loaded.card.red_zones) + len(loaded.card.quotes)
    held = cards.check_card(ours(text={"en": HELD}), "nl", m)  # type: ignore[arg-type]
    assert "clean_debris_filter" in held.refused and "plug_back_in (en) held by" in held.refused["clean_debris_filter"]


class Counted(NoHooks):
    def __init__(self) -> None:
        self.done = 0

    def job_done_checked(self, s: Session) -> None:
        self.done += 1


def done(step_id: str) -> Verdict:
    return Verdict(step_id=step_id, check_id="k", attempt=1, result="done", clause_ids=[], latency_ms=0,
                   witnesses=Witnesses(judge="done", sam="frame", sam_result="done"))


async def at_our_step(tmp_path: Path) -> tuple[Session, Counted]:
    hooks = Counted()
    s = session(tmp_path, FakeClient(images=PLUG_OUT, image=PLUG_OUT["plug"]), hooks=hooks)
    data = card_data()
    for st in data["jobs"][0]["steps"]:  # no zone to outline first: here only the step order matters
        st["zones"], st["zone_rings"] = [], []
    s.card = load_card(data)

    async def no_outline(*_: Any, **__: Any) -> None:  # outlining is tested elsewhere; here only the step order matters
        return None

    s.outline_part = no_outline  # type: ignore[method-assign]
    s.start()
    s.frames.add(frame(1, s.clock()))
    await s.start_job()
    await s.handle(tap("yes"))
    await s.idle()
    for sid in ("unplug", "open_flap", "refit_filter"):
        assert s.run is not None and s.run.step.id == sid and s.phase == "instructed"
        await s._verdict(done(sid))
    return s, hooks


async def test_the_step_message_is_marked_ours_with_no_page_or_quote(tmp_path: Path) -> None:
    s, hooks = await at_our_step(tmp_path)
    msg = [m for m in s.out if m["type"] == "step"][-1]
    assert msg["step_id"] == "plug_back_in" and msg["origin"] == "fixit" and msg["text"] == "Plug the machine back in."
    assert not {"page", "quote", "quote_en"} & set(msg)
    assert all("origin" not in m and m["page"] for m in s.out if m["type"] == "step" and m is not msg)
    assert hooks.done == 1 and s.phase == "instructed"  # recorded done when the last manual step passed
    await s.close()


async def test_the_users_word_ends_it_with_no_verdict_and_no_second_record(tmp_path: Path) -> None:
    s, hooks = await at_our_step(tmp_path)
    await s.handle(tap("back", 2))  # back to the filter, and its check passes again
    await s.idle()
    assert s.run is not None and s.run.step.id == "refit_filter"
    await s._verdict(done("refit_filter"))
    assert s.run.step.id == "plug_back_in" and hooks.done == 1
    n = len(s.out)
    await s.handle(tap("done", 3))
    await s.idle()
    assert s.phase == "job_done" and hooks.done == 1
    assert not [m for m in s.out[n:] if m["type"] == "checking"]  # no camera check ran
    [v] = [m["verdict"] for m in s.out[n:] if m["type"] == "verdict"]
    assert v["step_id"] == "plug_back_in" and v["result"] == "done" and v["clause_ids"] == []
    assert v["witnesses"] == {"sam": "none", "by_person": True}  # no judge named: none ran
    assert all("by_person" not in m["verdict"]["witnesses"] for m in s.out[:n] if m["type"] == "verdict")
    assert "our own step plug_back_in ended on the user's word, not checked by the camera" in s.events
    await s.close()
    note = build_note(recorder.load(tmp_path, s.session_id), s.card, photo_frame_ids=[], renter=False)
    assert "Plug the machine back in. (our step, not from the manual: done on your word)" in note["text"]
    assert "Screw the filter back in until the arrows line up.\n" in note["text"] + "\n"  # a checked step: no mark


async def test_the_guides_cite_says_it_is_ours(tmp_path: Path) -> None:
    s = session(tmp_path)
    s.card = ours()
    g = Guide(s)
    out = await g._cite(T.CiteArgs(step_id="plug_back_in", page=None, quote=None))
    assert out["origin"] == "fixit" and out["quote"] == "" and "page" not in out and g.cited_page is None
    assert trace.result(out, "cite") == "our own step, not from the manual"
    await s.close()

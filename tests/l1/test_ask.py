"""app.ask.answer, the eval's whole-context entry point: the production guide turn on one question. The retrieval arm
keeps the card and the history and reaches the manual only through lookup_manual; the history is rendered by the same
code as the store's hooks; the tool calls and cites come back for the failure write-ups."""

from __future__ import annotations

import json

import calls
from app.ask import answer
from app.guide import NO_ANSWER, render_history
from app.ports import Page, QuoteCheck
from tests.l1.test_guide import Scripted, resp
from tests.l1.test_session import HELD, acme

HISTORY = [{"id": 7, "kind": "fault", "on_date": "2026-09-02", "job_id": None, "text": "drain filter blocked"},
           {"id": 3, "kind": "job_done", "on_date": "2026-06-01", "job_id": "clean_debris_filter", "text": None}]


class Manual:
    def manual_pages(self, brand: str, model: str) -> list[Page]:
        return [Page(1, "Safety first."),
                Page(4, "Clean the debris filter every two months, turning it anticlockwise.")]

    def verify(self, pages: list[Page], page: int, quote: str) -> QuoteCheck:
        return QuoteCheck(True, page, None, "")


def prefix_text(c: Scripted) -> str:
    return json.dumps(c.inputs[0])


async def test_whole_context_has_the_manual_and_the_history() -> None:
    c = Scripted([resp("Every two months, as the manual says on page 4. Last done in June [entry 3].")])
    out = await answer(acme(), "How often do I clean the filter?", whole_context=True, history=HISTORY,
                       manuals=Manual(), client=c)  # type: ignore[arg-type]
    p = prefix_text(c)
    assert "Clean the debris filter every two months" in p and "[entry 7] 2026-09-02 fault: drain filter blocked" in p
    assert out["text"].startswith("Every two months") and out["tool_calls"] == [] and out["usd"] > 0


async def test_retrieval_arm_keeps_card_and_history_and_searches_the_manual() -> None:
    c = Scripted([resp(calls_=[("lookup_manual", {"query": "debris filter clean"})]),
                  resp(calls_=[("cite", {"step_id": acme().jobs[0].steps[0].id, "page": None, "quote": None})]),
                  resp("Every two months.")])
    out = await answer(acme(), "How often do I clean the filter?", whole_context=False, history=HISTORY,
                       manuals=Manual(), client=c)  # type: ignore[arg-type]
    p = prefix_text(c)
    assert "every two months" not in p and "[entry 3]" in p and "repair card" in p
    assert out["tool_calls"][0] == {"name": "lookup_manual", "query": "debris filter clean", "pages": [4]}
    assert out["cites"] and out["cites"][0]["page"] == acme().jobs[0].steps[0].quote.page
    assert out["text"] == "Every two months."


async def test_a_held_sentence_is_not_in_the_answer() -> None:
    c = Scripted([resp(f"Every two months. {HELD}")])
    out = await answer(acme(), "q", history=[], manuals=Manual(), client=c)  # type: ignore[arg-type]
    assert out["text"].startswith("Every two months. ") and HELD not in out["text"]
    assert "technician" in out["text"]  # the rule's own warning, as the user hears it


def test_render_history_lines_newest_first_with_ids() -> None:
    assert render_history(HISTORY) == ("[entry 7] 2026-09-02 fault: drain filter blocked\n"
                                       "[entry 3] 2026-06-01 job_done (job clean_debris_filter)")
    assert render_history([]) is None


def test_the_store_hooks_give_the_guide_this_appliances_history() -> None:
    from app.schedule import store as l5
    from app.session import Session
    from app.store import Store, StoreHooks
    from tests.l5.conftest import add_appliance, add_household

    conn = l5.open_store(":memory:")
    hid = add_household(conn)
    aid = add_appliance(conn, hid)
    st = Store(conn, b"\x01" * 32)
    st.add_history(hid, {"appliance_id": aid, "kind": "fault", "on_date": "2026-09-02", "text": "drain filter blocked"})
    s = Session(session_id="h1", client=None, household=hid, appliance_id=aid)  # type: ignore[arg-type]
    text = StoreHooks(st).history_text(s)
    assert text is not None and text.endswith("2026-09-02 fault: drain filter blocked")
    assert StoreHooks(st).history_text(Session(session_id="h2", client=None)) is None  # type: ignore[arg-type]


class RealCheck(Manual):
    """A four-page manual with ingest's real quote check (ingest.citation)."""

    def manual_pages(self, brand: str, model: str) -> list[Page]:
        filler = "Read these instructions before you use the appliance and keep them for later. " * 3
        return [Page(1, "Safety first. " + filler), Page(2, filler), Page(3, filler),
                Page(4, "Clean the debris filter every two months, turning it anticlockwise. " + filler)]

    def verify(self, pages: list[Page], page: int, quote: str) -> QuoteCheck:
        from app.ports import IngestManuals

        return IngestManuals().verify(pages, page, quote)


def manual_cite(page: int | None, quote: str | None, step_id: str | None = None) -> list[object]:
    return [resp(calls_=[("cite", {"step_id": step_id, "page": page, "quote": quote})]), resp("Every two months.")]


async def test_a_manual_cite_is_checked_against_the_pages() -> None:
    words = "Clean the debris filter every two months, turning it anticlockwise."
    out = await answer(acme(), "How often?", manuals=RealCheck(), client=Scripted(manual_cite(4, words)))  # type: ignore[arg-type]
    assert out["cites"] == [{"page": 4, "quote": words, "verified": True}]
    out = await answer(acme(), "How often?", manuals=RealCheck(),
                       client=Scripted(manual_cite(4, "Clean the debris filter every week, turning it clockwise.")))  # type: ignore[arg-type]
    assert out["cites"][0]["verified"] is False


async def test_a_cite_takes_one_shape_and_a_held_quote_is_not_returned() -> None:
    c = Scripted(manual_cite(4, "Clean the debris filter", step_id=acme().jobs[0].steps[0].id))
    out = await answer(acme(), "q", manuals=RealCheck(), client=c)  # type: ignore[arg-type]
    assert out["cites"] == [] and "step_id alone, or page and quote" in json.dumps(c.inputs[1])
    c = Scripted(manual_cite(1, HELD))
    out = await answer(acme(), "q", manuals=RealCheck(), client=c)  # type: ignore[arg-type]
    assert out["cites"] == [{"page": 1, "quote": "", "verified": False}]
    returned = [x for x in c.inputs[1] if x.get("type") == "function_call_output"]
    assert returned and HELD not in json.dumps(returned)  # the tool result never hands the held words back


async def test_a_string_null_step_id_is_a_manual_cite() -> None:
    words = "Clean the debris filter every two months, turning it anticlockwise."
    c = Scripted([resp(calls_=[("cite", {"step_id": "null", "page": 4, "quote": words})]), resp("Every two months.")])
    out = await answer(acme(), "How often?", manuals=RealCheck(), client=c)  # type: ignore[arg-type]
    assert out["cites"] == [{"page": 4, "quote": words, "verified": True}] and out["text"] == "Every two months."


class Timing(Scripted):
    def __init__(self, replies: list[object], timeout_usd: float = 0.0) -> None:
        super().__init__(replies)  # type: ignore[arg-type]
        self.timeouts: list[float | None] = []
        self.timeout_usd = timeout_usd

    async def respond(self, role: str, input: object, **kw: object) -> object:
        self.timeouts.append(kw.get("timeout_s"))  # type: ignore[arg-type]
        if self.timeout_usd:
            e = calls.CallTimeout("guide passed its 20 s timeout")
            e.usd = self.timeout_usd  # type: ignore[attr-defined]  # the ledger's estimate, when calls/ attaches it
            raise e
        return await super().respond(role, input, **kw)


async def test_every_guide_call_gets_the_measured_guide_timeout() -> None:
    from app import config

    whole, part = Timing([resp("Yes.")]), Timing([resp("Yes.")])
    await answer(acme(), "q", whole_context=True, manuals=Manual(), client=whole)  # type: ignore[arg-type]
    await answer(acme(), "q", whole_context=False, manuals=Manual(), client=part)  # type: ignore[arg-type]
    assert whole.timeouts == part.timeouts == [config.GUIDE_TIMEOUT_S]


async def test_a_timed_out_turn_reports_its_estimate() -> None:
    out = await answer(acme(), "q", manuals=Manual(), client=Timing([], timeout_usd=0.042))  # type: ignore[arg-type]
    assert out["usd"] == 0.042 and out["text"] == NO_ANSWER["en"]


def test_a_string_null_is_null_at_the_parse_boundary() -> None:
    from app.guide import _nulls
    from contracts.models import tools as T

    raw = {"step_id": "null", "page": 25, "quote": "Set the water hardness"}
    assert _nulls(T.CiteArgs, raw) == {"step_id": None, "page": 25, "quote": "Set the water hardness"}
    assert _nulls(T.CiteArgs, {"step_id": "", "page": None, "quote": None})["step_id"] is None
    assert _nulls(T.LookupManualArgs, {"query": "null"}) == {"query": "null"}  # not nullable: left alone


async def test_a_question_whose_rounds_all_go_to_tools_still_gets_an_answer() -> None:
    from app import config

    looping = [resp(calls_=[("lookup_manual", {"query": f"hardness {i}"})]) for i in range(config.GUIDE_MAX_ROUNDS)]
    out = await answer(acme(), "What hardness?", manuals=Manual(), client=Scripted(looping))  # type: ignore[arg-type]
    assert out["text"] == NO_ANSWER["en"] and len(out["tool_calls"]) == config.GUIDE_MAX_ROUNDS

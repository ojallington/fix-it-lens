import asyncio
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from eval import wholectx
from eval.wholectx import Outcome, claims, history_entries, quoted_spans, score_answer

P1 = "Reinig de zeven elke twee maanden. Draai de grove zeef met de klok mee tot de pijlen tegenover elkaar staan."
P2 = "Vul het zout vlak voor de start van een programma, want gemorst zout tast de houder aan."
PAGES = {"acme/Y": [P1, P2]}
Q = {"qid": "q1", "needs": ["acme/Y", "history"], "answer_key": ["two months"]}


def test_history_is_given_as_api_rows_newest_first() -> None:
    rows = history_entries([{"id": "h01", "on_date": "2026-03-02", "text": "a"},
                            {"id": "h02", "on_date": "2026-04-18", "text": "b", "kind": "job_done"}])
    assert [(r["id"], r["kind"]) for r in rows] == [(2, "job_done"), (1, "user_entry")]


def test_quoted_spans_skip_apostrophes() -> None:
    assert quoted_spans("Don't worry: the manual says “Reinig de zeven elke twee maanden.” It's fine.") == [
        "Reinig de zeven elke twee maanden."]


def test_answer_needs_the_manual_and_the_history_cited() -> None:
    card_cite = {"text": "Every two months; you last did it in August [entry 6].",
                 "cites": [{"page": 1, "quote": "Reinig de zeven elke twee maanden.", "verified": True}]}
    o = score_answer(Q, "whole", card_cite, PAGES, {1, 6})
    assert o.answered_checked and o.citations == o.citations_ok == 2 and o.cited_docs == ["acme/Y", "history"]
    spoken_quote = {"text": "The manual says “Reinig de zeven elke twee maanden.” So two months after [entry 6].",
                    "cites": []}
    assert score_answer(Q, "whole", spoken_quote, PAGES, {6}).answered_checked
    false_quote = {**spoken_quote, "text": "It says “Draai de grove zeef tegen de klok in tot het stopt.” two months"
                   " [entry 6]"}
    assert not score_answer(Q, "whole", false_quote, PAGES, {6}).answered_checked
    unverified = {**card_cite, "cites": [{**card_cite["cites"][0], "verified": False}]}
    assert not score_answer(Q, "whole", unverified, PAGES, {6}).answered_checked
    not_on_page = {**card_cite, "cites": [{"page": 1, "quote": "Vul het zout na elke wasbeurt bij.", "verified": True}]}
    assert not score_answer(Q, "whole", not_on_page, PAGES, {6}).answered_checked
    no_entry = {**card_cite, "text": "Every two months [entry 99]."}
    assert not score_answer(Q, "whole", no_entry, PAGES, {6}).answered_checked
    assert not score_answer(Q, "whole", {**card_cite, "text": "Monthly [entry 6]."}, PAGES, {6}).answered_checked


def test_results_keep_hashes_and_queries_not_text() -> None:
    reply = {"text": "The manual says “Reinig de zeven elke twee maanden.” [entry 6]", "cites": [], "usd": 0.01,
             "latency_ms": 2100.0, "tool_calls": [{"name": "lookup_manual", "query": "zeven reinigen", "pages": [1]}]}
    o = score_answer(Q, "retrieved", reply, PAGES, {6})
    assert len(o.quote_sha256[0]) == 16 and "Reinig" not in json.dumps(o.__dict__)
    assert o.tool_calls == [{"name": "lookup_manual", "query": "zeven reinigen", "pages": [1]}]


def test_claims() -> None:
    rs = [Outcome(f"q{i}", "whole", i < 28, i < 29, 2, 2 if i < 28 else 1, [], [], 0.0) for i in range(30)]
    c = {x["id"]: x for x in claims({"whole": rs})}
    assert c["wholectx.whole.answered_checked"]["value"] == 28 and c["wholectx.whole.answered_checked"]["n"] == 30
    assert c["wholectx.whole.citation_pass_rate"]["value"] == round(100 * 58 / 60, 1)


def test_run_calls_the_production_guide_turn(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "wholectx-history.json").write_text(json.dumps(
        [{"id": "h06", "on_date": "2026-08-10", "text": "cleaned", "kind": "job_done"}]))
    seen: list[dict[str, Any]] = []

    async def answer(card: Any, question: str, **kw: Any) -> dict[str, Any]:
        seen.append({"model": card.model, "question": question, **kw})
        return {"text": "Every two months [entry 6].", "cites": [{"page": 1, "quote": P1[:34], "verified": True}],
                "usd": 0.02, "latency_ms": 900.0, "tool_calls": []}

    class Manuals:
        def manual_pages(self, brand: str, model: str) -> list[Any]:
            return [types.SimpleNamespace(page=1, text=P1)]

    monkeypatch.setitem(sys.modules, "app.ask", types.SimpleNamespace(answer=answer))
    monkeypatch.setattr("app.ports.IngestManuals", Manuals)
    monkeypatch.setattr("calls.make_client", lambda *a, **k: "CLIENT")
    q = {"qid": "q1", "appliance": "dishwasher", "question": "When?", "lang": "nl",
         "needs": ["bosch/SMV4HAX40N", "history"],
         "answer_key": ["two months"]}
    for mode, whole in (("whole", True), ("retrieved", False)):
        [o] = asyncio.run(wholectx.run(mode, [q], tmp_path))
        assert o.answered_checked and o.usd == 0.02
        s = seen[-1]
        assert (s["model"], s["whole_context"], s["lang"], s["lane"], s["client"]) == (
            "SMV4HAX40N", whole, "nl", "l6", "CLIENT")
        assert s["history"] == [{"id": 6, "kind": "job_done", "on_date": "2026-08-10", "text": "cleaned",
                                 "job_id": None}]


def test_check_names_what_the_live_run_lacks(tmp_path: Path) -> None:
    (tmp_path / "manuals" / "samsung").mkdir(parents=True)
    (tmp_path / "manuals" / "samsung" / "WW90T.pdf").write_bytes(b"%PDF")
    (tmp_path / "cards" / "samsung").mkdir(parents=True)
    (tmp_path / "cards" / "samsung" / "WW90T.yaml").write_text("x")
    (tmp_path / "wholectx-history.json").write_text(json.dumps(
        {"washer": [{"id": "h01", "on_date": "2026-01-01", "text": "ok"}],
         "boiler": [{"id": "h01", "on_date": "2026-01-01", "text": "code <fill from manual>"}]}))
    ready = {"qid": "w01", "appliance": "washer", "needs": ["samsung/*", "history"], "answer_key": ["40"]}
    assert wholectx.resolve("samsung/*", tmp_path) == "samsung/WW90T"
    assert wholectx.check([ready], tmp_path) == ["history boiler h01: still has a <fill ...> placeholder"]
    lacking = {"qid": "b01", "appliance": "boiler", "needs": ["intergas/*", "history"], "answer_key": []}
    errs = wholectx.check([lacking], tmp_path)
    assert "b01: no answer_key yet" in errs and "b01: no single manual under manuals/intergas/" in errs


def test_a_bare_history_list_is_the_dishwashers() -> None:
    assert wholectx.histories([{"id": "h01"}]) == {"dishwasher": [{"id": "h01"}]}


def test_score_before_any_recording_is_unmeasured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(wholectx, "RESULTS", tmp_path)
    wholectx.score(["whole"])
    out = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert out and all(c["n"] == 0 for c in out)


def test_a_spoken_answer_with_markdown_is_counted() -> None:
    reply = {"text": "Clean it **every two months** [entry 6].", "cites": []}
    o = score_answer(Q, "whole", reply, PAGES, {6})
    assert o.markdown
    c = {x["id"]: x for x in claims({"whole": [o]})}
    assert c["wholectx.whole.markdown_answers"]["value"] == 1

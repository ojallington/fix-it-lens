"""Whole context against retrieval: on questions that need the manual and the household history, the
production guide with every manual page in its prefix, against the same guide reaching the manual only through
lookup_manual; scored on answers with a checked citation, citation pass rate and step accuracy.

    python -m eval.wholectx check                        # what the live run still lacks; exits 1 if anything
    python -m eval.wholectx run --mode whole|retrieved   # FIXIT_CALLS=replay (default) or record; needs FIXIT_PRIVATE
    python -m eval.wholectx score                        # claims from eval/results/wholectx/<mode>.jsonl
    python -m eval.wholectx live                         # check, run both modes, score: the row's live_command
    make wholectx-live                                   # the one command: live through the harness, --write, grep

`run` also takes --qid (repeatable) and --fixtures DIR, for a smoke run that leaves the real fixtures alone.

Each question goes through app.ask.answer (one production guide turn, its output through G0), so the row
measures what the app ships. The retrieved arm is the app's own: the card (with its quotes) and the history stay in
the prefix, and lookup_manual searches the card's manual pages by keyword count. Nothing here helps it: the agent
writes its own query.

Questions: eval/labels/wholectx.jsonl, hand-labelled, one line each:
    {"qid": "q01", "appliance": "dishwasher", "question": "...", "lang": "en",
     "needs": ["bosch/SMV4HAX40N", "history"], "answer_key": ["2 months", "clockwise"]}
`needs` names the documents a correct answer must cite: a manual by brand/model, or "brand/*" for the one manual
under FIXIT_PRIVATE/manuals/<brand>/ (written before the model was known), and "history". The card is
cards/<brand>/<model>.yaml, or FIXIT_PRIVATE/cards/<brand>/<model>.yaml for one built when the appliance was added.
A question is answered with a checked citation when every answer_key term is in the answer the user would hear, the
manual is cited, and the history is cited as [entry N] with a real entry id. The manual counts as cited by a `cite`
result (a card step's quote, or a page and its exact words) that the app marks verified and whose quote
ingest.citation.verify also finds on its page, or by a quoted span of the answer that verify finds on a page of the
manual.

The household history is FIXIT_PRIVATE/wholectx-history.json, per appliance ({"washer": [{id: "h01", on_date, kind,
text}, ...], ...}), given to the guide as the api's HistoryEntry rows for that appliance. Manual text never reaches
the repo: the recorded responses live in FIXIT_PRIVATE/fixtures-l6/ (they quote the manuals), and eval/results/ keeps
only hashes, verdicts, page numbers and the agent's lookup queries.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from eval.stats import wilson_count, wilson_pct

EVAL = Path(__file__).resolve().parent
ROOT = EVAL.parent
QUESTIONS = EVAL / "labels" / "wholectx.jsonl"
RESULTS = EVAL / "results" / "wholectx"
MODES = {"whole": True, "retrieved": False}  # mode -> app.ask.answer(whole_context=...)
_QUOTED = re.compile(r"[\"“„«]([^\"“”„«»]{12,}?)[\"”»]")
_ENTRY = re.compile(r"\[entry (\d+)\]")


@dataclass(frozen=True)
class Outcome:
    qid: str
    mode: str
    answered_checked: bool
    answer_ok: bool
    citations: int
    citations_ok: int
    cited_docs: list[str]
    quote_sha256: list[str]
    usd: float
    latency_ms: float | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)  # name, query, pages: for the failure write-ups
    markdown: bool = False  # the spoken answer carries Markdown (eval.spoken): it is read aloud as plain text


def history_entries(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The private history as GET /history returns it: integer ids, newest first."""
    rows = [{"id": int(h["id"].lstrip("h")), "kind": h.get("kind", "user_entry"), "on_date": h["on_date"],
             "text": h["text"], "job_id": h.get("job_id")} for h in history]
    return sorted(rows, key=lambda r: (r["on_date"], r["id"]), reverse=True)


def histories(raw: Any) -> dict[str, list[dict[str, Any]]]:
    """Per appliance; a bare list is the dishwasher's (the file's first shape)."""
    return {"dishwasher": raw} if isinstance(raw, list) else dict(raw)


def resolve(doc: str, private: Path) -> str | None:
    """"brand/*" -> "brand/<model>" when exactly one manual is under manuals/<brand>/; else the doc, or None."""
    if not doc.endswith("/*"):
        return doc
    pdfs = sorted((private / "manuals" / doc[:-2]).glob("*.pdf"))
    return f"{doc[:-2]}/{pdfs[0].stem}" if len(pdfs) == 1 else None


def card_path(doc: str, private: Path) -> Path | None:
    return next((p for p in (ROOT / "cards" / f"{doc}.yaml", private / "cards" / f"{doc}.yaml") if p.exists()), None)


def manual_doc(q: dict[str, Any], private: Path) -> str | None:
    return resolve(next(d for d in q["needs"] if d != "history"), private)


def check(questions: list[dict[str, Any]], private: Path) -> list[str]:
    """Everything the live run needs, question by question; empty when it can go."""
    errs = []
    hist = histories(json.loads((private / "wholectx-history.json").read_text())) \
        if (private / "wholectx-history.json").exists() else {}
    for q in questions:
        qid, doc = q["qid"], manual_doc(q, private)
        if not q.get("answer_key"):
            errs.append(f"{qid}: no answer_key yet")
        if doc is None:
            errs.append(f"{qid}: no single manual under manuals/{q['needs'][0][:-2]}/")
        else:
            if not (private / "manuals" / f"{doc}.pdf").exists():
                errs.append(f"{qid}: no manual manuals/{doc}.pdf")
            if card_path(doc, private) is None:
                errs.append(f"{qid}: no card cards/{doc}.yaml (repo or FIXIT_PRIVATE)")
        if "history" in q["needs"] and not hist.get(q.get("appliance", "dishwasher")):
            errs.append(f"{qid}: no {q.get('appliance', 'dishwasher')} history in wholectx-history.json")
    for appliance, rows in hist.items():
        errs += [f"history {appliance} {h['id']}: still has a <fill ...> placeholder"
                 for h in rows if "<fill" in h["text"]]
    return errs


def quoted_spans(text: str) -> list[str]:
    return [m.group(1).strip() for m in _QUOTED.finditer(text)]


def score_answer(q: dict[str, Any], mode: str, reply: dict[str, Any], pages: dict[str, list[str]],
                 entry_ids: set[int]) -> Outcome:
    """`pages`: each needed manual's page texts (index 0 is page 1)."""
    from eval.spoken import has_markdown
    from ingest.citation import verify

    text = str(reply.get("text", ""))
    ok_docs: set[str] = set()
    n = n_ok = 0
    shas = []
    manual = next((d for d in q["needs"] if d != "history"), None)
    for c in reply.get("cites") or []:  # a card step's quote or a manual page's words; the phone shows the page
        n += 1
        quote, page = str(c.get("quote", "")), c.get("page")
        shas.append(hashlib.sha256(quote.encode()).hexdigest()[:16])
        ps = pages.get(manual or "", [])
        rechecked = bool(quote.strip()) and isinstance(page, int) and bool(ps) and verify(ps, page, quote).ok
        if c.get("verified") and rechecked and manual:  # the app's flag and our own check must agree
            n_ok += 1
            ok_docs.add(manual)
    for span in quoted_spans(text):  # a manual quote in the answer itself: true if it is on a page of the manual
        n += 1
        shas.append(hashlib.sha256(span.encode()).hexdigest()[:16])
        ps = pages.get(manual or "", [])
        if ps and verify(ps, 1, span).ok:  # no page given in speech: verify finds the page it is on
            n_ok += 1
            ok_docs.add(manual or "")
    for m in _ENTRY.finditer(text):
        n += 1
        if int(m.group(1)) in entry_ids:
            n_ok += 1
            ok_docs.add("history")
    answer_ok = all(t.lower() in text.lower() for t in q.get("answer_key", []))
    checked = answer_ok and all(d in ok_docs for d in q["needs"])
    calls_ = [{"name": t.get("name"), "query": (t.get("query") or None) and str(t["query"])[:80],
               "pages": list(t.get("pages") or [])} for t in reply.get("tool_calls") or []]
    return Outcome(q["qid"], mode, checked, answer_ok, n, n_ok, sorted(ok_docs), shas,
                   float(reply.get("usd") or 0.0), reply.get("latency_ms"), calls_, has_markdown(text))


def claims(by_mode: dict[str, list[Outcome]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for mode, rs in sorted(by_mode.items()):
        n = len(rs)
        k = sum(r.answered_checked for r in rs)
        cit = sum(r.citations for r in rs)
        cit_ok = sum(r.citations_ok for r in rs)
        step = sum(r.answer_ok for r in rs)
        md = sum(r.markdown for r in rs)
        target = {"whole": 28, "retrieved": 19}.get(mode)
        out += [
            {"id": f"wholectx.{mode}.answered_checked", "value": k, "unit": "questions of n answered with a checked "
             "citation", "n": n, "ci": list(wilson_count(k, n)) if n else None, "target": target},
            {"id": f"wholectx.{mode}.citation_pass_rate", "value": round(100 * cit_ok / cit, 1) if cit else None,
             "unit": "%", "n": cit, "ci": list(wilson_pct(cit_ok, cit)) if cit else None},
            {"id": f"wholectx.{mode}.step_accuracy", "value": round(100 * step / n, 1) if n else None, "unit": "%",
             "n": n, "ci": list(wilson_pct(step, n)) if n else None},
            {"id": f"wholectx.{mode}.markdown_answers", "value": md, "unit": "spoken answers of n with Markdown",
             "n": n, "ci": list(wilson_count(md, n)) if n else None},
        ]
    return out


async def run(mode: str, questions: list[dict[str, Any]], private: Path,
              fixtures: Path | None = None) -> list[Outcome]:
    import calls
    from app.cards import read
    from app.ports import IngestManuals

    try:
        ask: Any = importlib.import_module("app.ask")
    except ModuleNotFoundError as e:
        raise SystemExit("app.ask.answer is not on this branch yet (app/); nothing to run") from e
    hist = histories(json.loads((private / "wholectx-history.json").read_text()))
    manuals = IngestManuals()
    client = calls.make_client(os.environ.get("FIXIT_CALLS", "replay"),  # type: ignore[arg-type]
                               fixtures_root=fixtures or private / "fixtures-l6")
    out = []
    for q in questions:
        doc = manual_doc(q, private)
        path = card_path(doc, private) if doc else None
        if doc is None or path is None:
            raise SystemExit(f"{q['qid']}: no manual or card yet; run `python -m eval.wholectx check`")
        card = read(path)
        history = history_entries(hist.get(q.get("appliance", "dishwasher"), []))
        pages = {doc: [p.text for p in manuals.manual_pages(card.brand, card.model)]}
        reply = await ask.answer(card, q["question"], lang=q.get("lang", "en"), whole_context=MODES[mode],
                                 history=history, manuals=manuals, client=client, lane="l6")
        out.append(score_answer({**q, "needs": [doc if d.endswith("/*") else d for d in q["needs"]]}, mode, reply,
                                pages, {h["id"] for h in history}))
    return out


def load_questions() -> list[dict[str, Any]]:
    return [json.loads(x) for x in QUESTIONS.read_text().splitlines() if x.strip()]


def score(modes: list[str]) -> None:
    """Claims from the recorded results; a mode with none yet prints n=0 (unmeasured, never written)."""
    by_mode = {}
    for m in modes:
        f = RESULTS / f"{m}.jsonl"
        rows = [json.loads(x) for x in f.read_text().splitlines() if x.strip()] if f.exists() else []
        by_mode[m] = [Outcome(**r) for r in rows]
    for c in claims(by_mode):
        print(json.dumps(c, ensure_ascii=False))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.wholectx")
    ap.add_argument("cmd", choices=["check", "run", "score", "live"])
    ap.add_argument("--mode", action="append", choices=list(MODES))
    ap.add_argument("--qid", action="append")
    ap.add_argument("--fixtures", type=Path)
    a = ap.parse_args(argv)
    modes = a.mode or list(MODES)
    if a.cmd == "score":
        score(modes)
        return 0
    from calls import private_dir

    priv = private_dir()
    if priv is None:
        print("wholectx needs FIXIT_PRIVATE (manuals, cards, history, fixtures-l6)", file=sys.stderr)
        return 2
    qs = [q for q in load_questions() if not a.qid or q["qid"] in a.qid]
    errs = check(qs, priv)
    if a.cmd == "check" or (a.cmd == "live" and errs):
        for e in errs:
            print(e, file=sys.stderr)
        print(f"wholectx: {len(qs)} questions, {len(errs)} problem(s)", file=sys.stderr)
        return 1 if errs else 0
    smoke = a.qid is not None or a.fixtures is not None
    out = RESULTS if not smoke else (a.fixtures or priv / "fixtures-l6-smoke") / "results"
    out.mkdir(parents=True, exist_ok=True)
    for m in modes:
        rs = asyncio.run(run(m, qs, priv, a.fixtures))
        (out / f"{m}.jsonl").write_text("".join(json.dumps(asdict(r), ensure_ascii=False) + "\n" for r in rs))
        print(f"wholectx {m}: {sum(r.answered_checked for r in rs)} of {len(rs)} checked, "
              f"${sum(r.usd for r in rs):.3f} -> {out / f'{m}.jsonl'}", file=sys.stderr)
    if a.cmd == "live":
        score(modes)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

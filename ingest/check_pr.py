"""make check-pr: the citation check as code, with no Meta key.

On the contributor's machine (their copy of the manual at FIXIT_PRIVATE/manuals/<brand>/<model>.pdf, or --pdf):
- the PDF's page-text hashes must equal the card's `manual.page_text_sha256`;
- every quote in the card must pass `check_quote` on its page;
- the result is written as a record beside the card, cards/<brand>/<model>.check.json: hashes only, no page text:
  {record_version: 2, card_sha256 (the card file's bytes), pdf_sha256, page_text_sha256: [...], quotes: [{key, ok,
  score}]}, key = sha256 of "<page>\n<quote text>".

In CI (--ci; no PDF, no key): the schema, every step citing a page inside the manual, every quote at most two
sentences, and the record: it was made for these card bytes, its page hashes equal the card's and every quote in the
card is in it as passed. The card loader counts a repo card as citation-checked when check_ci returns no errors.

    uv run python -m ingest.check_pr                 # every card with a local PDF; writes records
    uv run python -m ingest.check_pr --ci            # what CI runs
    uv run python -m ingest.check_pr cards/bosch/SMV4HAX40N.yaml --pdf ~/Downloads/manual.pdf
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import jsonschema

import contracts
from calls.config import ROOT
from ingest import faultcodes, pages
from ingest.appliance_file import MAX_SENTENCES, WARNING_BOX_SENTENCES, sentences, within_cap
from ingest.cards import CARD_SCHEMA, all_cards, read_card
from ingest.citation import check_quote

RECORD_VERSION = 2  # 2: card_sha256, the card file's bytes, so an edited card cannot ride an old record


def card_quotes(card: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    out: list[tuple[str, dict[str, Any]]] = []
    for z in card["red_zones"]:
        out.append((f"red_zones[{z['id']}]", z["quote"]))
    for q in card["quotes"]:
        out.append((f"quotes[{q['id']}]", q))
    for j in card["jobs"]:
        for s in j["steps"]:
            if s.get("origin") != "fixit":  # ours: no quote, nothing to check against the manual
                out.append((f"jobs[{j['id']}].steps[{s['id']}]", s["quote"]))
    return out


def quote_key(q: dict[str, Any]) -> str:
    return hashlib.sha256(f"{q['page']}\n{q['text']}".encode()).hexdigest()


def card_sha256(card_file: Path) -> str:
    return hashlib.sha256(card_file.read_bytes()).hexdigest()


def record_path(card_file: Path) -> Path:
    return card_file.with_suffix(".check.json")


def structure_errors(card: dict[str, Any]) -> list[str]:
    errs: list[str] = []
    try:
        jsonschema.validate(card, CARD_SCHEMA)
    except jsonschema.ValidationError as e:
        errs.append(f"schema: {e.message} at {'/'.join(map(str, e.absolute_path))}")
        return errs
    n = card["manual"]["page_count"]
    if len(card["manual"]["page_text_sha256"]) != n:
        errs.append(f"manual: {len(card['manual']['page_text_sha256'])} page hashes for {n} pages")
    for where, q in card_quotes(card):
        if not 1 <= q["page"] <= n:
            errs.append(f"{where}: page {q['page']} is outside the manual's {n} pages")
        if sentences(q["text"]) > WARNING_BOX_SENTENCES + 1:  # the box's title line; the page check decides
            errs.append(f"{where}: a quote is at most {MAX_SENTENCES} sentences, a whole warning box "
                        f"{WARNING_BOX_SENTENCES} and its title")
    errs += contracts.card_invariants(card)
    for j in card["jobs"]:
        for s in j["steps"]:
            if s.get("origin") != "fixit" and s["page"] != s["quote"]["page"]:
                errs.append(f"jobs[{j['id']}].steps[{s['id']}]: step page {s['page']} ≠ quote page")
    ids = {q["id"] for q in card["quotes"]}
    for j in card["jobs"]:
        refs = [j["interval"]["quote_ref"]] if j["interval"] else []
        refs += [a["quote_ref"] for a in j.get("after") or []]
        if j["duration_min"] and j["duration_min"]["source"] == "manual_quote":
            refs.append(j["duration_min"].get("ref", ""))
        errs += [f"jobs[{j['id']}]: quote_ref {r!r} is not in quotes" for r in refs if r not in ids]
    return errs


def drawn_codes(card: dict[str, Any], texts: list[str], passed: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    """The codes the card carries that the manual's text does not print: each must be on a fault row whose quote
    passed (`passed`: quote ids) and have display shape, read from the drawing; a job's code must be one of them.
    Returns the record's entries, {row, code, page, meaning} (the row's English meaning, what a person checks the
    code against), and the errors."""
    out: list[dict[str, Any]] = []
    errs: list[str] = []
    for r in card.get("troubleshooting", []):
        for code in r["codes"]:
            if faultcodes.printed(code, texts):
                continue
            if r["quote_ref"] in passed and faultcodes.display_shape(code):
                meaning = r["meaning"].get("en") or next(iter(r["meaning"].values()), "")
                out.append({"row": r["id"], "code": code, "page": r["page"], "meaning": meaning})
            else:
                errs.append(f"troubleshooting[{r['id']}]: code {code!r} is not printed and not a drawn display code")
    drawn = {d["code"] for d in out}
    for j in card["jobs"]:
        errs += [f"jobs[{j['id']}]: code {c!r} is not printed and no fault row reads it from a drawing"
                 for c in j.get("fault_codes", []) if not faultcodes.printed(c, texts) and c not in drawn]
    return out, errs


def fixit_steps(card: dict[str, Any]) -> list[dict[str, Any]]:
    """Our own steps on the card (origin fixit), for a person to accept before the card is shared: {job, step,
    restores, text} (the English words, fixed by code)."""
    return [{"job": j["id"], "step": s["id"], "restores": s["restores"], "text": s["text"]["en"]}
            for j in card["jobs"] for s in j["steps"] if s.get("origin") == "fixit"]


def unaccepted(record: dict[str, Any]) -> list[str]:
    """Our own steps no person has accepted yet, as job/step (the card is not shared until this is empty)."""
    return [f"{d['job']}/{d['step']}" for d in record.get("fixit_steps", []) if not d.get("accepted_by")]


def unconfirmed(record: dict[str, Any]) -> list[str]:
    """The drawn codes no person has confirmed yet (the card is not shared until this is empty)."""
    return [d["code"] for d in record.get("drawn_codes", []) if not d.get("confirmed_by")]


def check_local(card_file: Path, pdf: Path) -> tuple[list[str], dict[str, Any]]:
    """Check a card against the contributor's PDF; return the errors and the record."""
    card = read_card(card_file)
    errs = structure_errors(card)
    got = pages.extract(pdf)
    hashes = [pages.page_hash(p.text) for p in got]
    if hashes != card["manual"]["page_text_sha256"]:
        diff = [i + 1 for i, (a, b) in enumerate(zip(hashes, card["manual"]["page_text_sha256"], strict=False))
                if a != b]
        errs.append(f"manual: this PDF's page text does not match the card (pages {diff[:10]}, "
                    f"{len(hashes)} pages against {card['manual']['page_count']})")
    texts = [p.text for p in got]
    ocr = {p.number for p in got if p.ocr}
    results = []
    for where, q in card_quotes(card):
        r = check_quote(texts[q["page"] - 1], q["text"], ocr=q["page"] in ocr) if q["page"] <= len(texts) else None
        ok = bool(r and r.ok)
        if q["page"] <= len(texts) and not within_cap(texts[q["page"] - 1], q["text"]):
            errs.append(f"{where}: a quote is at most {MAX_SENTENCES} sentences unless it is a whole warning box")
            ok = False
        if not (r and r.ok):
            errs.append(f"{where}: quote not on page {q['page']} ({r.reason if r else 'no such page'})")
        results.append({"key": quote_key(q), "ok": ok, "score": round(r.score, 1) if r else 0.0})
    record = {"record_version": RECORD_VERSION, "card_sha256": card_sha256(card_file),
              "pdf_sha256": pages.file_sha256(pdf), "page_text_sha256": hashes, "quotes": results}
    passed = {q["id"] for (_, q), res in zip(card_quotes(card), results, strict=True) if res["ok"] and "id" in q}
    drawn, drawn_errs = drawn_codes(card, texts, passed)
    errs += drawn_errs
    if ours := fixit_steps(card):  # a person accepts each; the answer is kept while the job and words stay the same
        old = json.loads(rf.read_text()) if (rf := record_path(card_file)).exists() else {}
        said = {(d["job"], d["restores"], d["text"]): d for d in old.get("fixit_steps", [])}
        record["fixit_steps"] = [{"accepted_by": None, "accepted_at": None, **d,
                                  **{k: v for k, v in said.get((d["job"], d["restores"], d["text"]), {}).items()
                                     if k in ("accepted_by", "accepted_at", "rejected_by")}} for d in ours]
    if drawn:  # "read from the drawing, not text-checked"
        said = _said(card_file, str(record["pdf_sha256"]))
        keys = ("confirmed_by", "confirmed_at", "rejected_by")
        record["drawn_codes"] = [{"confirmed_by": None, "confirmed_at": None, **d,
                                  **{k: v for k, v in said.get((d["code"], d["page"], d["meaning"]), {}).items()
                                     if k in keys}} for d in drawn]
    old = json.loads(rf.read_text()) if (rf := record_path(card_file)).exists() else {}
    if old.get("lifted_zones") and old.get("card_sha256") == record["card_sha256"]:  # the build's, for this version
        record["lifted_zones"] = old["lifted_zones"]
    return errs, record


def _said(card_file: Path, pdf_sha: str) -> dict[tuple[str, int, str], dict[str, Any]]:
    """The answers a person gave about this manual's drawn codes, kept across rebuilds of the card: what they
    confirmed is the PDF's own drawing (this file, page, code and meaning), not one version of the card."""
    old = json.loads(rf.read_text()) if (rf := record_path(card_file)).exists() else {}
    if old.get("pdf_sha256") != pdf_sha:
        return {}
    return {(d["code"], d["page"], d.get("meaning", "")): d for d in old.get("drawn_codes", [])}


def check_ci(card_file: Path) -> list[str]:
    card = read_card(card_file)
    errs = structure_errors(card)
    rec_file = record_path(card_file)
    if not rec_file.exists():
        return errs + [f"no check record: run `make check-pr` with the manual, and commit {rec_file.name}"]
    rec = json.loads(rec_file.read_text())
    if rec.get("record_version") != RECORD_VERSION:
        errs.append(f"record: version {rec.get('record_version')}, not {RECORD_VERSION}: run `make check-pr` again")
    if rec.get("card_sha256") != card_sha256(card_file):
        errs.append("record: it was made for another version of this card file: run `make check-pr` again")
    if rec.get("page_text_sha256") != card["manual"]["page_text_sha256"]:
        errs.append("record: page hashes differ from the card's (the card changed after its check, or another PDF)")
    passed = {q["key"] for q in rec.get("quotes", []) if q.get("ok")}
    errs += [f"{where}: not in the check record as passed" for where, q in card_quotes(card)
             if quote_key(q) not in passed]
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="make check-pr")
    ap.add_argument("cards", nargs="*", type=Path)
    ap.add_argument("--ci", action="store_true", help="no PDF: check the schema, structure and the record")
    ap.add_argument("--pdf", type=Path, help="the manual, for a single card")
    a = ap.parse_args(argv)
    files = [c.resolve() for c in a.cards] or all_cards()
    failed = 0
    for f in files:
        name = f.relative_to(ROOT) if f.is_relative_to(ROOT) else f
        if a.ci:
            errs = check_ci(f)
        else:
            card = read_card(f)
            pdf = a.pdf or pages.manuals_dir() and pages.manual_path(card["brand"], f.stem)
            if not pdf or not Path(pdf).exists():
                errs = check_ci(f)
                print(f"{name}: no local manual; checked the record only")
            else:
                errs, record = check_local(f, Path(pdf))
                if not errs:
                    record_path(f).write_text(json.dumps(record, indent=1) + "\n")
        for e in errs:
            print(f"{name}: {e}")
        print(f"{name}: {'FAIL' if errs else 'ok'}")
        failed += bool(errs)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

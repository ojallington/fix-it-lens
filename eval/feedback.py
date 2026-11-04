"""Feedback after a pilot session: the records, and the summary the site and the video read.

    python -m eval.feedback summary [--db PATH] [--out FILE]        # totals and consented quotes, as JSON
    python -m eval.feedback fresh SUMMARY [--db PATH]               # exit 1 if the store changed since SUMMARY
    python -m eval.feedback import FILLED.pdf --household ID [--db PATH]  # a filled PDF form -> one record

The form is eval/consent/feedback.md. Each answer is one JSON line in FIXIT_PRIVATE/feedback/feedback.jsonl, written
by the server after a session in the app or by `import` from a filled PDF. Both write through app/feeds/feedback.py,
the one module that names the file, under its lock: so an import never races a server write, a withdrawal or an erase.

    {"id": "fb-…", "household": "…", "at": ISO, "source": "app"|"pdf", "lang": "nl"|"en"|null, "form_version": 1,
     "answers": {"would_use": "yes"|"maybe"|"no"|null, "would_use_why": TEXT|null,
                 "practical": "yes"|"partly"|"no"|null, "hard": TEXT|null, "unsafe": "no"|"yes"|null,
                 "unsafe_which": TEXT|null, "monthly": TEXT|null, "friend": TEXT|null},
     "quote": "none"|"anonymous"|"named", "quote_name": TEXT|null, "withdrawn_at": ISO|null}

The summary counts one answer per household, its latest, and only households of the pilot cohort (eval.pilot.cohort:
consented, not deleted, not a test, not our own, joined by invite), so a deleted household's words go with it. It
prints the respondents (n), the tick-box totals, and every written answer of a household that ticked a quote box,
word for word (only the surrounding whitespace trimmed), critical ones included. A household that ticked "do not
quote me", or withdrew, adds to the totals and nothing else: its words are never printed. Nothing here rewrites,
shortens or picks among the answers. An import is refused for a household outside the cohort, asked again under
the lock, so a home deleted while the form was being read gets nothing written.

The website and the video read quotes only from a summary made at their build time: never copied by hand, never in
the README. So a household that deletes its home, or withdraws, is gone at the next build. A summary states when it
was made (generated_at) and the fingerprint of what it read: the feedback lines and the pilot households. A delete,
a withdrawal or a new answer changes the fingerprint, and `fresh` then refuses the summary; the site build runs it
first and stops on exit 1. (The store keeps no time of a delete, since a deleted home's rows are gone, so the check
compares what was read rather than times.)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FORM_VERSION = 1
# tick-box questions: the values, in the order of the boxes on the form
CHOICES: dict[str, tuple[str, ...]] = {"would_use": ("yes", "maybe", "no"), "practical": ("yes", "partly", "no"),
                                       "unsafe": ("no", "yes")}
WRITTEN = ("would_use_why", "hard", "unsafe_which", "monthly", "friend")
QUOTE = ("none", "anonymous", "named")
# characters in one written answer, from the contract; the server refuses a longer one (422), never cuts it
MAX_TEXT: int = json.loads((Path(__file__).resolve().parents[1] / "contracts" / "feedback.schema.json").read_text())[
    "properties"]["answers"]["properties"]["hard"]["maxLength"]


class FeedbackError(Exception):
    pass


def records_path() -> Path:
    from calls import private_dir

    priv = private_dir()
    if priv is None:
        raise FeedbackError("feedback lives in FIXIT_PRIVATE/feedback/; set FIXIT_PRIVATE")
    return priv / "feedback" / "feedback.jsonl"


def check(r: dict[str, Any]) -> dict[str, Any]:
    """The record against the contract (contracts/feedback.schema.json), the same check the server makes on every
    line: all eight answers, the text limit, and a name only on a named quote (a withdrawn one may have lost it)."""
    from jsonschema import ValidationError

    from contracts import validate

    try:
        validate("feedback", r)
    except ValidationError as e:
        raise FeedbackError(f"{r.get('id')}: {e.message}") from None
    return r


def read(path: Path) -> list[dict[str, Any]]:
    return [check(json.loads(x)) for x in path.read_text().splitlines() if x.strip()] if path.exists() else []


def summary(records: list[dict[str, Any]], cohort: set[str] | None) -> dict[str, Any]:
    """cohort: the households that count (eval.pilot.cohort); None only in tests."""
    latest: dict[str, dict[str, Any]] = {}
    for r in sorted(records, key=lambda r: r["at"]):
        if cohort is None or r["household"] in cohort:
            latest[r["household"]] = r
    rows = list(latest.values())
    out: dict[str, Any] = {"n": len(rows)}
    for k, vals in CHOICES.items():
        out[k] = {v: sum(1 for r in rows if r["answers"].get(k) == v) for v in vals}
        out[k]["no_answer"] = sum(1 for r in rows if r["answers"].get(k) is None)
    quotes = []
    for r in sorted(rows, key=lambda r: r["at"]):
        if r["quote"] == "none" or r.get("withdrawn_at"):
            continue
        for k in WRITTEN:
            text = (r["answers"].get(k) or "").strip()
            if text:
                quotes.append({"question": k, "lang": r.get("lang"), "text": text,
                               "by": r["quote_name"] if r["quote"] == "named" else None})
    out["quotes"] = quotes
    out["quoted_households"] = len({r["household"] for r in rows if r["quote"] != "none" and not r.get("withdrawn_at")})
    return out


def fingerprint(path: Path, cohort: set[str]) -> str:
    """What a summary read: the feedback file's bytes and the pilot households. Changes on any delete or answer."""
    h = hashlib.sha256(path.read_bytes() if path.exists() else b"")
    h.update(b"\0" + "\n".join(sorted(cohort)).encode())
    return h.hexdigest()


def from_fields(fields: dict[str, Any], household: str, lang: str | None) -> dict[str, Any]:
    """A filled PDF's field values -> a record. Two ticks on one question count as no answer; two on the quote
    question as "do not quote me"."""
    def ticked(key: str, n: int) -> list[int]:
        return [i for i in range(n) if str(fields.get(f"{key}_{i + 1}") or "/Off") not in ("/Off", "Off", "")]

    answers: dict[str, Any] = {}
    for k, vals in CHOICES.items():
        t = ticked(k, len(vals))
        answers[k] = vals[t[0]] if len(t) == 1 else None
    for k in WRITTEN:
        text = fields.get(k)
        answers[k] = str(text) if text and str(text).strip() else None
    q = ticked("quote", len(QUOTE))
    quote = QUOTE[q[0]] if len(q) == 1 else "none"
    name = str(fields.get("quote_as") or "").strip() or None
    if quote == "named" and not name:
        quote = "none"  # "quote me as" with no name written: nobody to credit, so not quoted
    from jsonschema import ValidationError

    from app.feeds import feedback as feed

    try:
        return feed.record(household, source="pdf", lang=lang, answers=answers, quote=quote,
                           quote_name=name if quote == "named" else None)
    except ValidationError as e:
        raise FeedbackError(f"{household}: {e.message}") from None


def import_pdf(private: Path, rec: dict[str, Any], db: Path) -> None:
    """Append through the app's feedback module, asking the store under its lock whether the household is still a
    pilot home (the server asks its own delete state; this process cannot, so it asks the store the delete changes)."""
    from app.feeds import feedback as feed
    from eval.pilot import cohort

    def gone(household: str) -> bool:
        with sqlite3.connect(db) as conn:
            return household not in set(cohort(conn, own=False))

    try:
        written = feed.append(private, rec, deleting=gone)
    except feed.FeedbackError as e:
        raise FeedbackError(f"{rec['household']}: {e.detail}") from None
    if not written:
        raise FeedbackError(f"{rec['household']} is not a pilot household (deleted, or never invited): not imported")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.feedback")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("summary")
    s.add_argument("--db", type=Path)
    s.add_argument("--out", type=Path)
    fr = sub.add_parser("fresh")
    fr.add_argument("summary", type=Path)
    fr.add_argument("--db", type=Path)
    i = sub.add_parser("import")
    i.add_argument("pdf", type=Path)
    i.add_argument("--household", required=True)
    i.add_argument("--lang", choices=("nl", "en"))
    i.add_argument("--db", type=Path)
    a = ap.parse_args(argv)
    try:
        path = records_path()
        from eval.pilot import cohort, default_db

        db = a.db or default_db()
        if not db.exists():
            raise FeedbackError(f"no store at {db}: feedback counts pilot households only, so it needs it")
        if a.cmd == "import":
            from pypdf import PdfReader  # type: ignore[import-not-found,unused-ignore]

            fields = {k: v.get("/V") for k, v in (PdfReader(a.pdf).get_fields() or {}).items()}
            rec = from_fields(fields, a.household, a.lang)
            import_pdf(path.parent.parent, rec, db)
            print(f"{rec['id']}: imported (quote: {rec['quote']})")  # never the answers: they are the household's
            return 0
        with sqlite3.connect(db) as conn:
            homes = set(cohort(conn, own=False))
        if a.cmd == "fresh":
            made = json.loads(a.summary.read_text())
            if made.get("fingerprint") != fingerprint(path, homes):
                print(f"feedback: {a.summary} (made {made.get('generated_at')}) is stale: a home was deleted, or an "
                      "answer added or withdrawn, since. Make a new summary.", file=sys.stderr)
                return 1
            return 0
        out = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
               "fingerprint": fingerprint(path, homes), **summary(read(path), homes)}
        text = json.dumps(out, ensure_ascii=False, indent=2) + "\n"
        if a.out:
            a.out.write_text(text)
        else:
            print(text, end="")
        return 0
    except FeedbackError as e:
        print(f"feedback: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

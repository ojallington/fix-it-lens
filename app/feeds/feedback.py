"""What pilot households say after a session (contracts/feedback.schema.json): one JSON line per answer in
FIXIT_PRIVATE/feedback/feedback.jsonl, the folder 0700 and the file 0600. In the server only this module names the
file, and no route serves it back; offline, `python -m eval.feedback summary` prints the totals and consented quotes.
No model call reads it.

Only a household in the store's pilot_cohort view writes a line; written answers are kept exactly as typed. A household
can withdraw its quote (withdrawn_at is set and the name blanked; the totals keep counting it), and a deleted
household's lines go with it (app/erase.py calls erase). Appending, withdrawing and erasing hold one flock, and each
call opens the lock file itself, so threads and other processes (the PDF import) exclude each other: a line written
while the file is rewritten is neither lost nor kept by mistake.

    python -m app.feeds.feedback withdraw fb-0123456789ab    # a household asked by email
"""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import secrets
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import contracts

DIR, FILE, LOCK = "feedback", "feedback.jsonl", "feedback.lock"
MAX_LINES_PER_HOUSEHOLD = 20  # a form after each session; more than this is not feedback


class FeedbackError(Exception):
    """A refused line or withdrawal: `error` is the API's code (not_found, too_many)."""
    def __init__(self, error: str, detail: str) -> None:
        super().__init__(detail)
        self.error, self.detail = error, detail


def _dir(private: Path) -> Path:
    d = private / DIR
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


@contextlib.contextmanager
def _locked(private: Path) -> Iterator[Path]:
    """The file's path, held against every other append, withdrawal and erase."""
    d = _dir(private)
    fd = os.open(d / LOCK, os.O_RDWR | os.O_CREAT, 0o600)  # an fd of its own, so threads exclude each other too
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield d / FILE
    finally:
        os.close(fd)  # closing releases the flock


def _read(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _rewrite(path: Path, lines: list[dict[str, Any]]) -> None:
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.writelines(json.dumps(r, ensure_ascii=False) + "\n" for r in lines)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _iso(at: datetime | None) -> str:
    return (at or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def record(household_id: str, *, source: str, lang: str | None, answers: dict[str, Any], quote: str,
           quote_name: str | None, at: datetime | None = None) -> dict[str, Any]:
    """A new line, checked against the contract (jsonschema's ValidationError when it does not hold)."""
    rec = {"id": "fb-" + secrets.token_hex(6), "household": household_id, "at": _iso(at), "source": source,
           "lang": lang, "form_version": 1, "answers": answers, "quote": quote, "quote_name": quote_name,
           "withdrawn_at": None}
    contracts.validate("feedback", rec)
    return rec


def append(private: Path, rec: dict[str, Any], *, deleting: Callable[[str], bool]) -> bool:
    """Add one checked line. A household being deleted (`deleting`, asked under the lock) gets nothing written:
    False. Past MAX_LINES_PER_HOUSEHOLD, FeedbackError too_many."""
    contracts.validate("feedback", rec)
    with _locked(private) as path:
        if deleting(rec["household"]):
            return False
        if sum(r["household"] == rec["household"] for r in _read(path)) >= MAX_LINES_PER_HOUSEHOLD:
            raise FeedbackError("too_many", f"this home has sent {MAX_LINES_PER_HOUSEHOLD} answers already")
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    return True


def withdraw(private: Path, feedback_id: str, *, household_id: str | None = None, at: datetime | None = None) -> None:
    """Withdraw a line's quote: withdrawn_at set (kept from the first time) and the name blanked. With
    `household_id`, only that household's line; a line not found is FeedbackError not_found."""
    with _locked(private) as path:
        lines = _read(path)
        mine = [r for r in lines if r["id"] == feedback_id and household_id in (None, r["household"])]
        if not mine:
            raise FeedbackError("not_found", "no such answer from this home")
        for r in mine:
            r["withdrawn_at"] = r["withdrawn_at"] or _iso(at)
            r["quote_name"] = None
            contracts.validate("feedback", r)
        _rewrite(path, lines)


def erase(private: Path, household_id: str) -> int:
    """Remove every line of a household (app/erase.py calls this when the household is deleted); how many went."""
    with _locked(private) as path:
        lines = _read(path)
        kept = [r for r in lines if r["household"] != household_id]
        if len(kept) != len(lines):
            _rewrite(path, kept)
        return len(lines) - len(kept)


def main(argv: list[str] | None = None) -> int:
    from app.config import private_dir

    ap = argparse.ArgumentParser(prog="feedback", description="Withdraw a household's quote (asked by email).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("withdraw", help="never print this answer's words again; its name is blanked")
    w.add_argument("id", help="the answer's id, fb-<12 hex>")
    a = ap.parse_args(argv)
    private = private_dir()
    if private is None:
        print("no FIXIT_PRIVATE here, so no feedback either")
        return 1
    try:
        withdraw(private, a.id)
    except FeedbackError as e:
        print(e.detail)
        return 1
    print(f"withdrawn: {a.id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

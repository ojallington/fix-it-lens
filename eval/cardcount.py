"""Manual to card, in counts: what one committed card holds and how many of its quotes passed the citation check.

    python -m eval.cardcount bosch/SMV4HAX40N --as bosch    # claims card.bosch.{pages,jobs,steps,quotes_checked}

Read from the committed files only: cards/<brand>/<model>.yaml and its check record <model>.check.json (hashes, no
page text). The record must be current, as CI checks it (ingest.check_pr.check_ci): made for these card bytes and
these page hashes, every quote passed. A stale or failing record fails the row instead of printing a count. These are
whole counts of one card, not a sample, so they carry no interval.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ingest.cards import CARDS, read_card
from ingest.check_pr import card_quotes, check_ci, quote_key, record_path


def claims(card_file: Path, name: str) -> list[dict[str, Any]]:
    errs = check_ci(card_file)
    if errs:
        raise ValueError(f"{card_file.name}: check record not current: {errs[0]}")
    card = read_card(card_file)
    rec = json.loads(record_path(card_file).read_text())
    pages = len(rec["page_text_sha256"])
    steps = sum(len(j["steps"]) for j in card["jobs"])
    quotes = card_quotes(card)
    passed = {q["key"] for q in rec["quotes"] if q["ok"]}
    ok = sum(1 for _, q in quotes if quote_key(q) in passed)
    src = f"{card['brand']} {card['model']} card ({card_file.parent.name}/{card_file.name})"

    def c(what: str, value: int, n: int, unit: str, note: str) -> dict[str, Any]:
        return {"id": f"card.{name}.{what}", "value": value, "unit": unit, "n": n, "ci": None,
                "note": f"{src}; {note}"}

    return [
        c("pages", pages, pages, "manual pages", "pages whose text hash the check record holds"),
        c("jobs", len(card["jobs"]), len(card["jobs"]), "repair jobs on the card", "the card's jobs"),
        c("steps", steps, steps, "steps across the card's jobs", "every step carries a manual quote"),
        c("quotes_checked", ok, len(quotes), "quotes of n passing the citation check",
          "every quote on the card (red zones, quotes, steps), checked against its page of the manual"),
    ]


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.cardcount")
    ap.add_argument("card", help="brand/model, as under cards/")
    ap.add_argument("--as", dest="name", required=True, help="the claim id's middle part, e.g. bosch")
    a = ap.parse_args(argv)
    for x in claims(CARDS / f"{a.card}.yaml", a.name):
        print(json.dumps(x, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

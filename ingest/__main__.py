"""fixit ingest: turn a maker's manual into a checked card.

    FIXIT_CALLS=record uv run python -m ingest build manual.pdf --brand bosch --model SMV4HAX40N \\
        --url https://media3.bosch-home.com/... [--out cards/] [--report report.json] [--pr [--no-push]]

Prints the rail line, every rejected quote and every page correction. A failed ingest writes no card and says so.
The report holds quotes and pages only, never page text. --pr checks the card on the same PDF (make check-pr) and opens
a card pull request from a branch card/<brand>-<model> (ingest/pr.py).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

from calls.config import ROOT
from ingest import appliance_file, lookup, pr
from ingest.cards import write_card


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fixit ingest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="build a card from a manual PDF")
    b.add_argument("pdf", type=Path)
    b.add_argument("--brand", required=True)
    b.add_argument("--model", required=True)
    b.add_argument("--url", required=True, help="the manual's URL on the maker's own support site")
    b.add_argument("--alias", action="append", default=[])
    b.add_argument("--country", default="nl")
    b.add_argument("--out", type=Path, default=ROOT / "cards")
    b.add_argument("--report", type=Path)
    b.add_argument("--no-write", action="store_true")
    b.add_argument("--pr", action="store_true", help="check the card on this PDF and open a card pull request")
    b.add_argument("--no-push", action="store_true", help="with --pr: branch and commit only")
    a = ap.parse_args(argv)

    res = asyncio.run(appliance_file.build(a.pdf, brand=a.brand, model=a.model, source_url=a.url,
                                           aliases=a.alias, country=a.country,
                                           source="maker_site" if lookup.maker_url_ok(a.url) else None))
    print(res.rail)
    for r in res.rejected:
        print(f"  rejected  {r.where} p{r.page} ({r.reason}): {r.quote!r} → dropped {r.dropped}")
    for where, frm, to in res.corrected:
        print(f"  corrected {where} p{frm} → p{to}")
    print(f"  usd {res.usd:.4f}  model {res.model}")
    if a.report:
        a.report.write_text(json.dumps({"rail": res.rail, "rejected": [asdict(r) for r in res.rejected],
                                        "corrected": res.corrected, "usd": res.usd, "error": res.error},
                                       ensure_ascii=False, indent=2))
    if res.card is None:
        print(f"no card: {res.error}. The manual stays as plain pages.", file=sys.stderr)
        return 1
    if a.no_write:
        return 0
    path = write_card(res.card, a.out)
    print(f"  wrote {path}")
    if a.pr:
        try:
            print(f"  pull request: {pr.open_pr(path, a.pdf, res, push=not a.no_push, country=a.country)}")
        except pr.PrError as e:
            print(f"no pull request: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

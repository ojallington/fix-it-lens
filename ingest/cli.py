"""fixit: the contributor's commands. `fixit ingest build …` (add --pr for a card pull request), `fixit check-pr`."""

from __future__ import annotations

import sys

from ingest import __main__ as ingest_main
from ingest import check_pr


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args[:1] == ["ingest"]:
        return ingest_main.main(args[1:])
    if args[:1] == ["check-pr"]:
        return check_pr.main(args[1:])
    print("usage: fixit ingest build <pdf> --brand … --model … --url … [--pr] | fixit check-pr [--ci]", file=sys.stderr)
    return 2

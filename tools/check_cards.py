"""The card loader's rules check over cards, for `make check-pr` and `fixit ingest --pr` (ingest/).

    python -m tools.check_cards [--country nl] [--json] [card.yaml ...]   # default: every card under cards/

It runs `app.cards.load`, the same check a session's card goes through, and says per card:
- a whole-card refusal (its red zones or rings, or no job left): `FAIL`, and the exit status is 1;
- each refused job, with its rule and reason: the card passes, but those jobs leave the card at load, so the line
  reads `pass, N jobs refused` and never as a clean pass.
The citation check is `make check-pr`'s own (ingest.check_pr); this tool reports only the rules.
It lives in tools/ because only app/ imports rules/, and ingest/ imports neither.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app import cards, config


def check(card_file: Path, country: str) -> dict[str, Any]:
    loaded = cards.load(card_file, country, None)
    rules = loaded.card.checks.rules if loaded.card.checks else None
    status = rules.status if rules else "fail"
    card_wide = [f for f in (rules.failures or [] if rules else []) if not f.startswith("job ")]
    if status == "fail" and not card_wide:
        card_wide = ["every job is refused"]
    refused = {jid: why.split("; ") for jid, why in loaded.refused.items()}
    shown = card_file.resolve()
    shown = shown.relative_to(config.ROOT) if config.ROOT in shown.parents else shown
    return {"card": str(shown), "country": country, "status": status, "failures": card_wide, "refused": refused}


def line(result: dict[str, Any]) -> str:
    refused = result["refused"]
    if result["status"] == "fail":
        head = f"{result['card']}: FAIL, the card is refused"
    elif refused:
        head = f"{result['card']}: pass, {len(refused)} job{'s' if len(refused) != 1 else ''} refused"
    else:
        head = f"{result['card']}: pass"
    out = [head] + [f"  card: {f}" for f in result["failures"]]
    out += [f"  refused {jid}: {why}" for jid, whys in refused.items() for why in whys]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.check_cards")
    ap.add_argument("cards", nargs="*", type=Path)
    ap.add_argument("--country", default="nl")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    files = a.cards or sorted(config.CARDS_DIR.rglob("*.yaml"))
    results = [check(f, a.country) for f in files]
    print(json.dumps(results) if a.json else "\n".join(line(r) for r in results))
    return 1 if any(r["status"] == "fail" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())

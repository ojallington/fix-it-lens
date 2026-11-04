"""The fixit-rules CLI. The eval's G0 rows and the guarded-step list shell out to it (only app/ imports rules).

    fixit-rules check-sentence --country nl --lang en "Remove the back panel."      # one JSON line
    fixit-rules check-sentence --country nl --lang en --jsonl < sentences.txt         # one JSON line per input line
    fixit-rules guarded --card cards/acme/wm-0000.yaml [--step refit_filter]          # true/false, or {step: bool}
    fixit-rules check-card --card cards/acme/wm-0000.yaml --country nl
    fixit-rules stop-script --country nl --lang nl nl.gas.tap
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import yaml

from contracts.models.card import Card
from rules.cards import check_card
from rules.engine import stop_script
from rules.g0 import Hold, check_sentence
from rules.steps import guarded


def _card(path: str | None) -> Card | None:
    if not path:
        return None
    text = Path(path).read_text()
    data = json.loads(text) if path.endswith(".json") else yaml.safe_load(text)
    return Card.model_validate(data)


def _sentence(text: str, lang: str, card: Card | None, country: str) -> dict[str, Any]:
    t0 = time.perf_counter()
    r = check_sentence(text, lang, card, None, country=country)
    ms = (time.perf_counter() - t0) * 1000
    if isinstance(r, Hold):
        return {"result": "hold", "rule_id": r.rule_id, "line": r.line, "words": r.words, "ms": round(ms, 3)}
    return {"result": "pass", "ms": round(ms, 3)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fixit-rules")
    sub = ap.add_subparsers(dest="cmd", required=True)
    cs = sub.add_parser("check-sentence")
    cs.add_argument("--country", required=True)
    cs.add_argument("--lang", required=True)
    cs.add_argument("--card")
    cs.add_argument("--jsonl", action="store_true", help="read sentences from stdin, one per line")
    cs.add_argument("text", nargs="?")
    gd = sub.add_parser("guarded")
    gd.add_argument("--card", required=True)
    gd.add_argument("--step")
    cc = sub.add_parser("check-card")
    cc.add_argument("--card", required=True)
    cc.add_argument("--country", required=True)
    ss = sub.add_parser("stop-script")
    ss.add_argument("--country", required=True)
    ss.add_argument("--lang", required=True)
    ss.add_argument("rule_id")
    a = ap.parse_args(argv)

    if a.cmd == "check-sentence":
        card = _card(a.card)
        if a.jsonl:
            for line in sys.stdin:
                if line.strip():
                    print(json.dumps({"text": line.rstrip("\n"), **_sentence(line.rstrip("\n"), a.lang, card,
                                                                            a.country)}))
            return 0
        if a.text is None:
            ap.error("check-sentence needs text or --jsonl")
        print(json.dumps(_sentence(a.text, a.lang, card, a.country)))
        return 0
    if a.cmd == "guarded":
        card = _card(a.card)
        assert card is not None
        steps = {s.id: s for j in card.jobs for s in j.steps}
        if a.step:
            if a.step not in steps:
                print(f"no step {a.step} on the card", file=sys.stderr)
                return 2
            print(json.dumps(guarded(steps[a.step], card)))
        else:
            print(json.dumps({sid: guarded(s, card) for sid, s in steps.items()}))
        return 0
    if a.cmd == "check-card":
        card = _card(a.card)
        assert card is not None
        failures, rule_ids = check_card(card, a.country)
        print(json.dumps({"status": "fail" if failures else "pass", "failures": failures, "rule_ids": rule_ids}))
        return 1 if failures else 0
    print(stop_script(a.country, a.rule_id, a.lang))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

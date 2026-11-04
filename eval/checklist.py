"""Job checklists per appliance type: what a manual of that type usually gives, so a reader asks for each item
instead of rediscovering the jobs. A checklist asks; it never writes a step (every step still cites its page).

    python -m eval.checklist check        # every checklist is well formed; with FIXIT_PRIVATE, also against gold
    python -m eval.checklist show washer  # the items, with how many gold cards hold each

eval/checklists/<appliance_kind>.yaml, derived from the gold cards (eval/gold.py) and holding names only, never a
manual's words:

    appliance_kind: washer
    gold_cards: [<gold key>, ...]           # the gold cards of this kind it was derived from
    items:
      - id: washer.drain_pump_filter        # stable; <kind>.<slug> for a user item, <kind>.pro.<slug> otherwise
        names: {en: ..., nl: ...}
        kind: upkeep                        # upkeep or fix (user items only)
        who: user                           # user: the manual gives the user a procedure; professional: it
                                            # sends the job to a technician
        evidence: 7                         # how many gold manuals hold it, out of len(gold_cards); an item
                                            # with evidence 1 is a weak prompt, and no list is complete
        note: ...                           # optional
        gold: [<gold key>:<job id>, ...]    # user item: the gold jobs it stands for; professional: the gold cards

Against gold: every gold job of the kind is in exactly one user item, every reference names a real gold job or
card, and every gold card of the kind is listed. The types with no gold card yet (dryer, hob) have no checklist.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

DIR = Path(__file__).parent / "checklists"
WHO = {"user", "professional"}
KINDS = {"upkeep", "fix"}


@dataclass(frozen=True)
class Item:
    id: str
    names: dict[str, str]
    who: str
    kind: str | None
    gold: list[str]
    evidence: int = 0
    note: str = ""

    @property
    def cards(self) -> set[str]:
        return {g.split(":", 1)[0] for g in self.gold}


@dataclass(frozen=True)
class Checklist:
    appliance_kind: str
    gold_cards: list[str]
    items: list[Item]

    def user_items(self) -> list[Item]:
        return [i for i in self.items if i.who == "user"]


def parse(data: dict[str, Any]) -> Checklist:
    items = [Item(id=str(i["id"]), names=dict(i.get("names") or {}), who=str(i.get("who")),
                  kind=i.get("kind"), gold=[str(g) for g in i.get("gold") or []],
                  evidence=int(i.get("evidence") or 0), note=str(i.get("note") or ""))
             for i in data.get("items") or []]
    return Checklist(str(data.get("appliance_kind")), [str(c) for c in data.get("gold_cards") or []], items)


def load(kind: str, root: Path = DIR) -> Checklist:
    return parse(yaml.safe_load((root / f"{kind}.yaml").read_text(encoding="utf-8")))


def kinds(root: Path = DIR) -> list[str]:
    return sorted(p.stem for p in root.glob("*.yaml"))


def problems(cl: Checklist) -> list[str]:
    """What is wrong with a checklist on its own."""
    errs = []
    seen: set[str] = set()
    for it in cl.items:
        if it.id in seen:
            errs.append(f"{it.id}: id used twice")
        seen.add(it.id)
        prefix = f"{cl.appliance_kind}.pro." if it.who == "professional" else f"{cl.appliance_kind}."
        if not it.id.startswith(prefix) or (it.who == "user" and it.id.startswith(f"{cl.appliance_kind}.pro.")):
            errs.append(f"{it.id}: id must start with {prefix}")
        if not it.names.get("en") or not it.names.get("nl"):
            errs.append(f"{it.id}: needs names in en and nl")
        if it.who not in WHO:
            errs.append(f"{it.id}: who must be user or professional")
        if it.who == "user" and it.kind not in KINDS:
            errs.append(f"{it.id}: a user item's kind is upkeep or fix")
        if not it.gold:
            errs.append(f"{it.id}: no gold reference")
        if it.evidence != len(it.cards):
            errs.append(f"{it.id}: evidence is {it.evidence}, but gold names {len(it.cards)} gold cards")
        for g in it.gold:
            card = g.split(":", 1)[0]
            if card not in cl.gold_cards:
                errs.append(f"{it.id}: {card} is not in gold_cards")
            if (it.who == "user") != (":" in g):
                errs.append(f"{it.id}: {g!r}: a user item names <card>:<job>, a professional one <card>")
    return errs


def gold_problems(cl: Checklist, gold: dict[str, dict[str, Any]]) -> list[str]:
    """What is wrong with a checklist against the gold cards ({key: gold card}) of every kind."""
    errs = []
    mine = {k for k, g in gold.items() if g.get("appliance_kind") == cl.appliance_kind}
    errs += [f"gold card {k} is not in gold_cards" for k in sorted(mine - set(cl.gold_cards))]
    errs += [f"{k} in gold_cards is not a {cl.appliance_kind} gold card" for k in sorted(set(cl.gold_cards) - mine)]
    jobs = {f"{k}:{j['id']}" for k in mine for j in gold[k].get("jobs") or []}
    placed: dict[str, str] = {}
    for it in cl.user_items():
        for g in it.gold:
            if g not in jobs:
                errs.append(f"{it.id}: {g} is not a gold job")
            elif g in placed:
                errs.append(f"{g} is in both {placed[g]} and {it.id}")
            else:
                placed[g] = it.id
    errs += [f"gold job {j} is in no item" for j in sorted(jobs - set(placed))]
    for it in cl.items:
        if it.who == "professional":
            errs += [f"{it.id}: {c} lists no professional job" for c in sorted(it.cards & mine)
                     if not (gold[c].get("gold_notes") or {}).get("professional")]
    return errs


def _gold() -> dict[str, dict[str, Any]] | None:
    import calls

    priv = calls.private_dir()
    if priv is None or not (priv / "gold").is_dir():
        return None
    return {f.stem: yaml.safe_load(f.read_text(encoding="utf-8")) for f in sorted((priv / "gold").glob("*.yaml"))}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.checklist")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    s = sub.add_parser("show")
    s.add_argument("kind")
    args = ap.parse_args(argv)
    if args.cmd == "show":
        cl = load(args.kind)
        n = len(cl.gold_cards)
        for it in cl.items:
            print(f"{it.id:38} {it.who:12} {it.kind or '':6} {it.evidence}/{n}  {it.names['en']}")
        return 0
    gold = _gold()
    bad = 0
    for kind in kinds():
        cl = load(kind)
        errs = problems(cl) + (gold_problems(cl, gold) if gold is not None else [])
        bad += bool(errs)
        users = len(cl.user_items())
        print(f"{kind}: {users} user items, {len(cl.items) - users} professional, "
              f"{len(cl.gold_cards)} gold cards: {'ok' if not errs else f'{len(errs)} problems'}")
        for e in errs:
            print(f"  {e}")
    if gold is None:
        print("(no FIXIT_PRIVATE/gold: checked the format only)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

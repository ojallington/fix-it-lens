"""Gold cards: what a careful reader of the whole manual would put on the card, to score built cards against.

    python -m eval.gold text                 # each manual's pages as numbered text, for the readers
    python -m eval.gold check [FILE ...]     # every gold card loads and its quotes are on their pages
    python -m eval.gold score DRAFTS_DIR     # eval.goldscore's table: the cards under DRAFTS_DIR against gold
    python -m eval.gold score --raw DIR      # the same for raw drafts (<brand>-<model>.draft.json, page_map.json)

Gold cards and everything taken from a manual live in FIXIT_PRIVATE/gold/, never in the repo:
    gold/manuals.toml                       which manual each gold card reads (brand, model, pages.json)
    gold/text/<key>.txt                     the pages, "=== p12 ===" before each (1-based PDF index, as cards cite)
    gold/<key>.yaml                         the gold card (reader A)
    gold/second/<key>.yaml                  an independent second reading of a few manuals (reader B)
<key> is "<brand>__<model slug>", e.g. miele__G_7793.

A gold card is an ordinary card (schema/card.md; contracts.load_card must accept it once `gold_notes` is taken off)
plus `gold_notes`, which holds what the card schema cannot:

    gold_notes:
      main_job: clean_filter            # the job this appliance's owner most needs: the headline upkeep
      jobs:
        <job id>:                       # one entry per job in `jobs`
          match: [filter, zeef]         # words that name this job, in English and the manual's language
          warnings:                     # every caution the manual attaches to this job, in order
            - {page: 47, quote: "<verbatim, the manual's language>", en: "...", match: [hot, heet]}
          removed: [filter]             # part ids taken out or off during the job
          refitted: [filter]            # part ids put back; the step that does it has role refit
          unplug: s1                    # the step that cuts the power (null if the job does not)
          plug_back: s9                 # the step that restores it (null if none)
          roles: {s1: unplug, s2: remove, s3: clean, s4: refit, s9: plug_back}
          alt_pages: {s2: [83]}         # the same instruction in the manual's other language sections
          interval_ok: [[4, month]]     # other intervals the manual allows ("every 2 to 4 months")
      professional:                     # jobs the manual says only a technician or the maker's service may do
        - {title: "Replace the mains cable", page: 12, quote: "<verbatim>", match: [mains cable, netsnoer]}
      notes: |                          # anything else a scorer or reader should know
        ...

Roles are unplug, remove, clean, refit, plug_back, check, prepare, run and other. A `match` list is lower case;
a built card carries a warning or names a job when any of its words is in the card's text for that job.
"""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path
from typing import Any

import yaml

import calls
from ingest.pages import page_hash

ROLES = {"unplug", "remove", "clean", "refit", "plug_back", "check", "prepare", "run", "other"}


def gold_dir() -> Path:
    priv = calls.private_dir()
    if priv is None:
        raise SystemExit("FIXIT_PRIVATE is not set and the default private folder is missing; gold lives there")
    return priv / "gold"


def manuals(root: Path) -> dict[str, dict[str, str]]:
    """manuals.toml: {key: {brand, model, pages}} with `pages` relative to FIXIT_PRIVATE."""
    found: dict[str, dict[str, str]] = tomllib.loads((root / "manuals.toml").read_text())["manual"]
    return found


def page_texts(root: Path, key: str) -> list[str]:
    import json

    entry = manuals(root)[key]
    data = json.loads((root.parent / entry["pages"]).read_text())
    return [p["text"] for p in data["pages"]]


def write_text(root: Path) -> list[Path]:
    out = root / "text"
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for key in manuals(root):
        pages = page_texts(root, key)
        f = out / f"{key}.txt"
        f.write_text("".join(f"=== p{i} ===\n{t}\n" for i, t in enumerate(pages, 1)), encoding="utf-8")
        written.append(f)
        m = out / f"{key}.manual.yaml"  # the card's `manual` block, for the reader to copy (lang and source_url too)
        block = {"source_url": f"urn:fixit:gold:{key}", "lang": "nl", "page_count": len(pages),
                 "page_text_sha256": [page_hash(t) for t in pages]}
        m.write_text(yaml.safe_dump({"manual": block}, sort_keys=False), encoding="utf-8")
        written.append(m)
    return written


def split(data: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """The card part and the gold_notes of a gold card."""
    card = {k: v for k, v in data.items() if k != "gold_notes"}
    return card, data.get("gold_notes") or {}


def notes_problems(card: dict[str, Any], notes: dict[str, Any]) -> list[str]:
    jobs = {j["id"]: j for j in card.get("jobs") or []}
    parts = {p["id"] for p in card.get("parts") or []}
    errs = []
    if notes.get("main_job") not in jobs:
        errs.append(f"gold_notes.main_job {notes.get('main_job')!r} is not a job")
    njobs = notes.get("jobs") or {}
    errs += [f"gold_notes.jobs lacks {jid}" for jid in jobs if jid not in njobs]
    errs += [f"gold_notes.jobs.{jid} is not a job" for jid in njobs if jid not in jobs]
    for jid, n in njobs.items():
        if jid not in jobs:
            continue
        steps = {s["id"] for s in jobs[jid]["steps"]}
        if not n.get("match"):
            errs.append(f"{jid}: no match words")
        for k in ("unplug", "plug_back"):
            if n.get(k) is not None and n[k] not in steps:
                errs.append(f"{jid}.{k}: {n[k]!r} is not a step of the job")
        for sid, role in (n.get("roles") or {}).items():
            if sid not in steps:
                errs.append(f"{jid}.roles: {sid!r} is not a step of the job")
            if role not in ROLES:
                errs.append(f"{jid}.roles.{sid}: unknown role {role!r}")
        errs += [f"{jid}: removed part {p!r} is not in parts" for p in n.get("removed") or [] if p not in parts]
        errs += [f"{jid}: refitted part {p!r} is not in parts" for p in n.get("refitted") or [] if p not in parts]
        for w in n.get("warnings") or []:
            if not w.get("match") or not w.get("quote") or not isinstance(w.get("page"), int):
                errs.append(f"{jid}: a warning needs page, quote and match")
    for p in notes.get("professional") or []:
        if not p.get("match") or not p.get("title"):
            errs.append("a professional job needs title and match")
    return errs


def quote_problems(card: dict[str, Any], notes: dict[str, Any], pages: list[str]) -> list[str]:
    from ingest.citation import verify

    quotes = [(f"{j['id']}/{s['id']}", s["quote"]["page"], s["quote"]["text"]) for j in card["jobs"]
              for s in j["steps"]]
    quotes += [(f"quote/{q['id']}", q["page"], q["text"]) for q in card.get("quotes") or []]
    quotes += [(f"red_zone/{z['id']}", z["quote"]["page"], z["quote"]["text"]) for z in card.get("red_zones") or []]
    quotes += [(f"warning/{jid}", w["page"], w["quote"]) for jid, n in (notes.get("jobs") or {}).items()
               for w in n.get("warnings") or []]
    quotes += [(f"professional/{p['title']}", p["page"], p["quote"]) for p in notes.get("professional") or []
               if p.get("quote")]
    errs = []
    for where, page, text in quotes:
        r = verify(pages, page, text)
        if not r.ok:
            errs.append(f"{where} p{page}: {r.reason}")
        elif r.page != page:
            errs.append(f"{where}: cited p{page}, found on p{r.page}")
    return errs


def check(root: Path, path: Path) -> list[str]:
    from contracts import ContractError, load_card

    key = path.stem
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    card, notes = split(data)
    errs: list[str] = []
    try:
        load_card(card)
    except (ContractError, ValueError) as e:
        return [f"load_card: {e}"]
    errs += notes_problems(card, notes)
    if key in manuals(root):
        errs += quote_problems(card, notes, page_texts(root, key))
    else:
        errs.append(f"{key} is not in manuals.toml")
    return errs


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.gold")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("text")
    c = sub.add_parser("check")
    c.add_argument("files", nargs="*", type=Path)
    s = sub.add_parser("score")
    s.add_argument("drafts", type=Path)
    s.add_argument("--gold", type=Path, default=None)
    s.add_argument("--raw", action="store_true", help="DRAFTS_DIR holds raw whole-manual drafts")
    s.add_argument("--only-found", action="store_true", help="leave out gold cards with no draft")
    args = ap.parse_args(argv)
    root = gold_dir()
    if args.cmd == "text":
        for f in write_text(root):
            print(f)
        return 0
    if args.cmd == "check":
        files = args.files or sorted(root.glob("*.yaml"))
        bad = 0
        for f in files:
            errs = check(root, f)
            bad += bool(errs)
            print(f"{f.name}: {'ok' if not errs else f'{len(errs)} problems'}")
            for e in errs:
                print(f"  {e}")
        return 1 if bad else 0
    from eval import goldscore

    groot = args.gold or root
    if args.raw:
        rows = goldscore.score_raw_dir(args.drafts, groot)
    else:
        rows = goldscore.score_dir(args.drafts, groot, only_found=args.only_found, pages_root=root)
    print(goldscore.table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())

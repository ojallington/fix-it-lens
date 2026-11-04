"""Any appliance, first time: run unprepared manuals through the real ingest path and time it, one row per run.

    python -m tools.first_time trial.jsonl [--only N ...] [--tag baseline] [--household rerun] [--fixtures DIR]

Each line of `trial.jsonl` names one appliance: {n, brand, model, kind, lang_wanted, saved_path, pdf_url,
pdf_robots, support_page_robots}. A run goes the way a user's would:
- lookup: the maker's lookup (one request) when ingest has one for the brand, else "none";
- the manual: fetched by ingest from the maker's URL when robots.txt allows it, else brought by the user (the PDF
  bytes, the upload path), and the clock starts at the upload;
- the build: `ingest_appliance`, then the card loader's rules check (`app.cards.load`) for refused jobs.
Rows go to FIXIT_PRIVATE/first_time/runs.jsonl: counts, seconds and dollars only, never manual text or a serial.
The card itself is written by ingest as for a user: FIXIT_PRIVATE/cards/, or cards/homes/<household>/ with
--household (so a rerun never overwrites the cards an earlier run was graded on). Live calls go through calls/ and
the ledger on lane l3. With --fixtures (a folder under FIXIT_PRIVATE: a draft quotes the manual), FIXIT_CALLS=record
keeps every response there, and FIXIT_CALLS=replay rebuilds the same cards from them for nothing, so a fix in code
after the draft is measured again without a second live run.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import calls
from app import cards
from app.ports import IngestManuals
from ingest import flow, lookup, pages


class HouseholdManuals(IngestManuals):
    """The loader's manuals, read as for this household: its own copy first."""

    def __init__(self, household: str | None) -> None:
        self.household = household

    def manual_pages(self, brand: str, model: str) -> list[Any]:
        if self.household is None:
            return super().manual_pages(brand, model)
        from app.ports import Page
        try:
            return [Page(p.number, p.text, p.ocr) for p in pages.manual_pages(brand, model, self.household)]
        except FileNotFoundError:
            return []


def _ledger_rows(client: calls.Client) -> list[dict[str, Any]]:
    if client.ledger is None or not client.ledger.path.exists():
        return []
    return [json.loads(line) for line in client.ledger.path.read_text().splitlines() if line.strip()]


def run_usd(rows: list[dict[str, Any]], brand: str, model: str) -> float:
    """What this add cost, by run tag: the add-appliance and card-build rows of this appliance, among the rows the
    run wrote. Never by lane, which counts anything else spent meanwhile (another build, a fixture re-record)."""
    tags = (f"add-appliance:{brand}/{model}:", f"card-build:{brand}/{model}:")
    return sum(float(r.get("usd") or 0.0) for r in rows if str(r.get("run") or "").startswith(tags))


def _lookup(brand: str, model: str) -> dict[str, Any]:
    if brand not in lookup.MAKERS:
        return {"lookup": "none"}
    t = time.monotonic()
    ref = lookup.lookup(brand, model)
    took = round(time.monotonic() - t, 2)
    if isinstance(ref, lookup.Miss):
        return {"lookup": "miss", "lookup_reason": ref.reason, "lookup_s": took}
    return {"lookup": "found", "lookup_url": ref.url, "lookup_s": took}


async def run_one(row: dict[str, Any], client: calls.Client, tag: str,
                  household: str | None = None) -> dict[str, Any]:
    brand, model = row["brand"].lower(), row["model"]
    out: dict[str, Any] = {"n": row["n"], "tag": tag, "brand": brand, "model": model, "kind": row.get("kind"),
                           "lang": row.get("lang_wanted"), "pages": row.get("pages")}
    out |= _lookup(brand, model)
    use_url = row.get("pdf_url") if row.get("pdf_robots") == "allow" and lookup.maker_url_ok(row.get("pdf_url") or "")\
        else None
    out["manual_from"] = "maker_url" if use_url else "upload"
    pdf = None if use_url else Path(row["saved_path"]).read_bytes()
    stages: dict[str, float] = {}
    seen = len(_ledger_rows(client))
    t0 = time.monotonic()

    def progress(stage: str, detail: str) -> None:
        stages.setdefault(stage, round(time.monotonic() - t0, 2))

    try:
        res = await flow.ingest_appliance(brand=brand, model=model, pdf=pdf, pdf_url=use_url, kind=row.get("kind"),
                                          on_progress=progress, client=client, household=household,
                                          household_lang=row.get("lang_wanted"))
    except Exception as e:  # a crash is a result too
        out |= {"status": "crash", "error": f"{type(e).__name__}: {str(e)[:200]}"}
        res = None
    out["seconds"] = round(time.monotonic() - t0, 1)
    out["stage_s"] = stages
    out["usd"] = round(run_usd(_ledger_rows(client)[seen:], brand, model), 4)
    if res is None:
        return out
    out["status"] = res.status
    out["rail"] = res.rail
    if res.error:  # the runs file is private: the error may quote the draft
        out["error"] = res.error
    if res.notice:
        out["notice"] = res.notice["kind"]
    m = re.search(r"(\d+) rejected", res.rail)
    out["quotes_rejected"] = int(m.group(1)) if m else 0
    # where and why only, never the quote: e.g. "jobs[clean_pump].steps[unscrew] not_found → job clean_pump"
    out["rejected"] = [f"{r.where} {r.reason.split(':')[0]} → {r.dropped}" for r in res.rejected]
    # the whole reason, for the builder's own read of why a job dropped (this file is private: it may quote the draft)
    out["rejected_why"] = [f"{r.where}: {r.reason}" for r in res.rejected]
    out["coverage"] = list(res.coverage)
    if res.card is not None and res.card_path is not None:
        card = res.card
        out |= {"jobs": len(card["jobs"]), "steps": sum(len(j["steps"]) for j in card["jobs"]),
                "red_zones": len(card["red_zones"]), "intervals": sum(1 for j in card["jobs"] if j["interval"]),
                "quotes": len(card["quotes"]), "card": str(res.card_path),
                "fault_rows": len(card.get("troubleshooting", [])), "refused_at_build": len(card.get("refused", [])),
                "models": card["manual"].get("models", []), "covers": res.covers, "covers_reason": res.covers_reason,
                "origin": res.origin}
        # loaded as the server loads it: a private card's quotes are checked against its manual's pages
        loaded = cards.load(res.card_path, row.get("lang_wanted") or "nl", HouseholdManuals(household))
        out["usable"] = loaded.usable
        out["citation"] = loaded.citation_label
        out["jobs_refused"] = sorted(loaded.refused)
        out["jobs_shown"] = len(loaded.card.jobs) if loaded.usable else 0
    return out


async def main_async(args: argparse.Namespace) -> int:
    rows = [json.loads(x) for x in Path(args.trial).read_text().splitlines() if x.strip()]
    rows = [r for r in rows if r.get("saved_path") or r.get("pdf_robots") == "allow"]
    if args.only:
        rows = [r for r in rows if r["n"] in args.only]
    priv = calls.private_dir()
    assert priv is not None, "FIXIT_PRIVATE is needed for a live run"
    if args.fixtures is not None:
        root = args.fixtures.resolve()
        if not root.is_relative_to(priv.resolve()):
            raise SystemExit("--fixtures must be under FIXIT_PRIVATE: recorded drafts quote the manuals")
        mode = os.environ.get("FIXIT_CALLS", "replay")
        if mode not in ("record", "replay"):
            raise SystemExit("--fixtures needs FIXIT_CALLS=record or replay")
        client = calls.make_client(mode, fixtures_root=root)  # type: ignore[arg-type]
    else:
        client = calls.default_client()
    dest = priv / "first_time" / "runs.jsonl"
    dest.parent.mkdir(parents=True, exist_ok=True)
    for row in rows:
        result = await run_one(row, client, args.tag, args.household)
        with dest.open("a") as f:
            f.write(json.dumps(result, sort_keys=True) + "\n")
        print(json.dumps({k: result.get(k) for k in ("n", "brand", "model", "status", "seconds", "usd", "jobs",
                                                     "jobs_shown", "quotes_rejected", "error", "notice")}))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tools.first_time")
    ap.add_argument("trial", type=Path)
    ap.add_argument("--only", type=int, nargs="*")
    ap.add_argument("--tag", default="baseline")
    ap.add_argument("--household", help="build into this household's own cards (cards/homes/<household>/)")
    ap.add_argument("--fixtures", type=Path, help="record into or replay from this private folder")
    return asyncio.run(main_async(ap.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())

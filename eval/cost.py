"""What the model calls cost, read from the spend ledger: per call, per card build, per added appliance, per repair.

    python -m eval.cost collect                 # FIXIT_PRIVATE/ledger.jsonl -> eval/results/cost_calls.jsonl
    python -m eval.cost score --part calls      # $ per guide turn, judge call, SAM image, transcription; local mode
    python -m eval.cost score --part card       # $ per card build, per appliance added (new card, known card)
    python -m eval.cost score --part session    # $ per filmed repair session
    python -m eval.cost score --part idle|energy|home

`collect` keeps one line per ledger call with its role, mode, provider (none means Meta), status, tokens and $, and
nothing that names a home or a session: calls made for a household are left out, and a call's session becomes a short
label only when the session is one of our own filmed repairs listed in eval/labels/cost-sessions.txt. Such a repair
runs in our own home (households.is_own, no invite link), so its calls are kept, without the household. A card build
and the add around it are grouped by the run tag ingest writes on every call ("card-build:<brand>/<model>:<id>",
"add-appliance:<brand>/<model>:<id>"). The per-call rows count Meta calls only, so a partner's price never mixes in.

Every number is measured: a median and its n, with the 10th and 90th percentile (nearest rank) from five values up.
A timed-out call is left out, because the ledger stores an estimate for it, not a charge; the note says how many. A
part with no data prints its claims with n=0, so they are listed as not measured yet. The yearly cost per home is the
one projected number, and its unit says so.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from collections.abc import Set as AbstractSet
from pathlib import Path
from typing import Any

from eval.stats import median, percentile

EVAL = Path(__file__).resolve().parent
SESSIONS = EVAL / "labels" / "cost-sessions.txt"
RESULTS = EVAL / "results" / "cost_calls.jsonl"
KEEP = ("input_tokens", "cached_tokens", "output_tokens", "images_processed", "usd")
# role -> (what one call is, in a unit; only calls made inside a session)
ROLES = {"guide": ("guide turn", True), "judge": ("step-check judge call", False), "sam": ("SAM image", False),
         "transcribe": ("transcription", False)}
SPREAD_MIN = 5  # fewer calls than this: the median alone, as the percentiles would only repeat it
PARTS = ("calls", "card", "session", "idle", "energy", "home")


def _run(tag: str | None) -> tuple[str, str, str] | None:
    """"card-build:bosch/SMV4HAX40N:3f9a" -> (kind, model, id)."""
    if not tag or ":" not in tag:
        return None
    kind, _, rest = tag.partition(":")
    model, _, rid = rest.rpartition(":")
    return (kind, model, rid) if kind in ("card-build", "add-appliance") and model and rid else None


def own_households(db: Path) -> set[str]:
    """Our own homes in the store: is_own, and not a judge's (no invite link)."""
    import sqlite3

    if not db.exists():
        return set()
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        return {h for (h,) in conn.execute("SELECT id FROM households WHERE is_own = 1 AND invite_link_id IS NULL")}


def collect(ledger_rows: list[dict[str, Any]], session_ids: list[str],
            own: AbstractSet[str] = frozenset()) -> list[dict[str, Any]]:
    label = {sid: f"s{i + 1:02d}" for i, sid in enumerate(session_ids)}
    out: list[dict[str, Any]] = []
    for r in ledger_rows:
        role = r.get("role") or r.get("probe")
        sid = r.get("session_id")
        filmed = sid in label and r.get("household") in own
        if (r.get("household") and not filmed) or not role or r.get("lane") == "probe":
            continue
        row: dict[str, Any] = {"role": role, "mode": r.get("mode") or "api", "provider": r.get("provider") or "meta",
                               "status": r.get("status"),
                               "in_session": sid is not None, "session": label.get(sid or "")}
        row |= {k: r.get(k) for k in KEEP}
        if run := _run(r.get("run")):
            row["run"] = ":".join(run)
        out.append(row)
    return out


def _sig3(v: float) -> float:
    """Three significant digits: $1.07 a card, $0.00813 a guide turn."""
    return round(v, max(2, 2 - math.floor(math.log10(abs(v))))) if v else 0.0


def _spread(cid: str, values: list[float], unit: str, note: str) -> list[dict[str, Any]]:
    if not values:
        return [{"id": f"{cid}_median", "value": None, "unit": f"{unit} (median)", "n": 0, "ci": None, "note": note}]
    n = len(values)
    qs = [("median", "median", median(values))]
    if n >= SPREAD_MIN:
        qs += [("p10", "10th percentile", percentile(values, 10)), ("p90", "90th percentile", percentile(values, 90))]
    return [{"id": f"{cid}_{k}", "value": _sig3(v), "unit": f"{unit} ({w})", "n": n, "ci": None, "note": note}
            for k, w, v in qs]


def _charged(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    done = [r for r in rows if r["status"] == "completed"]
    return done, sum(1 for r in rows if r["status"] == "timeout")


def part_calls(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for role, (what, in_session) in ROLES.items():
        mine = [r for r in rows if r["role"] == role and r["mode"] == "api" and r.get("provider", "meta") == "meta"
                and (r["in_session"] or not in_session)]
        done, late = _charged(mine)
        note = "Meta Model API calls, from the spend ledger" + (", made inside a guide session" if in_session else "")
        if late:
            note += f"; {late} timed-out calls left out (the ledger holds an estimate for them, not a charge)"
        out += _spread(f"cost.call.{role}.usd", [float(r["usd"] or 0) for r in done], f"$ per {what}", note)
    local, _ = _charged([r for r in rows if r["mode"] == "local"])
    out.append({"id": "cost.local.api_usd", "value": round(sum(float(r["usd"] or 0) for r in local), 4),
                "unit": "$ in API charges, over n local-mode calls", "n": len(local), "ci": None,
                "note": "SAM, video SAM and the judge run on this machine's GPU; the electricity is a separate row"})
    return out


def part_card(rows: list[dict[str, Any]], covers: set[str] | None = None,
              passes: dict[str, set[str]] | None = None) -> list[dict[str, Any]]:
    """covers: the "<maker>/<model>" builds to count (the any-appliance roster), so a test build is never a price.
    passes: the build tags ids of each any-appliance run (eval/labels/anyappliance.jsonl, run_tag), so each run is its
    own price: cost.card_build.usd_* for the baseline, cost.card_build.<run>.usd_* for a later one."""
    runs: dict[str, float] = defaultdict(float)
    for r in rows:
        run = _run(r.get("run"))
        if run and r["status"] == "completed" and (covers is None or run[1] in covers):
            runs[r["run"]] += float(r["usd"] or 0)
    build = {k.split(":")[2]: v for k, v in runs.items() if k.startswith("card-build:")}
    add = {k.split(":")[2]: v for k, v in runs.items() if k.startswith("add-appliance:")}
    note = "summed over every call that carries the run's tag in the spend ledger" + (
        "; builds of the any-appliance roster's manuals" if covers is not None else "")
    if passes is None:
        out = _spread("cost.card_build.usd", list(build.values()), "$ per card built from a manual", note)
    else:
        out = []
        for name, ids in passes.items():
            missing = sorted(ids - set(build))
            if missing:
                raise ValueError(f"{name} run: build tags {missing} are not in eval/results/cost_calls.jsonl; collect")
            out += _spread("cost.card_build.usd" if name == "baseline" else f"cost.card_build.{name}.usd",
                           [build[i] for i in sorted(ids)], "$ per card built from a manual",
                           f"{note}, {name} run; a build that made no card counts too, since its calls were paid")
    return (out
            + _spread("cost.add_appliance.new.usd", [v + build[i] for i, v in add.items() if i in build],
                      "$ per appliance added whose card had to be built", note)
            + _spread("cost.add_appliance.known.usd", [v for i, v in add.items() if i not in build],
                      "$ per appliance added whose model already had a card", note))


def part_session(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    per: dict[str, float] = defaultdict(float)
    for r in rows:
        if r.get("session") and r["status"] == "completed":
            per[r["session"]] += float(r["usd"] or 0)
    return _spread("cost.repair_session.usd", list(per.values()), "$ per filmed repair session",
                   "every model call of the session (guide turns, judge, SAM, transcription), from the spend ledger")


def pending_only(part: str) -> list[dict[str, Any]]:
    one = {"idle": ("cost.idle_month.usd", "$ in model calls per home per month with no repair (reminders, push, "
                    "feeds)"),
           "energy": ("cost.local.energy_eur_per_session", "€ of GPU electricity per local-mode repair session"),
           "home": ("cost.per_home_year.usd", "$ per home per year, projected: its cards built once, plus the "
                    "repairs a year the schedule plans, plus the months with no repair, at the measured medians")}[part]
    return [{"id": one[0], "value": None, "unit": one[1], "n": 0, "ci": None, "note": "not measured yet"}]


def claims(part: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if part == "calls":
        return part_calls(rows)
    if part == "card":
        from eval.anyappliance import RUNS, read, roster_models

        labels = read()
        passes = {run: {str(r["run_tag"]).rsplit(":", 1)[-1] for r in labels
                        if r["record"] == "run" and r["run"] == run} for run in RUNS}
        return part_card(rows, roster_models(labels), passes)
    if part == "session":
        return part_session(rows)
    return pending_only(part)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.cost")
    ap.add_argument("cmd", choices=["collect", "score"])
    ap.add_argument("--part", choices=PARTS, default="calls")
    a = ap.parse_args(argv)
    if a.cmd == "collect":
        from calls import private_dir

        priv = private_dir()
        if priv is None:
            print("collect needs FIXIT_PRIVATE (the spend ledger)")
            return 2
        ledger = [json.loads(x) for x in (priv / "ledger.jsonl").read_text().splitlines() if x.strip()]
        ids = [x.strip() for x in SESSIONS.read_text().splitlines()
               if x.strip() and not x.startswith("#")] if SESSIONS.exists() else []
        rows = collect(ledger, ids, own_households(priv / "store.sqlite"))
        RESULTS.parent.mkdir(parents=True, exist_ok=True)
        RESULTS.write_text("".join(json.dumps(r) + "\n" for r in rows))
        print(f"{len(rows)} calls -> {RESULTS}")
        return 0
    rows = [json.loads(x) for x in RESULTS.read_text().splitlines() if x.strip()]
    for c in claims(a.part, rows):
        print(json.dumps(c, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

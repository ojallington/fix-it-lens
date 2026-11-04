"""Any appliance, first time: a manual nobody prepared, through the real ingest path, one row per appliance.

    python -m eval.anyappliance                  # claims anyappliance.baseline.* and anyappliance.rerun.*

Reads eval/labels/anyappliance.jsonl: a roster frozen before any build (record "roster"), then one record "run" per
appliance and run ("baseline", later "rerun"). Counts carry a Wilson 95% CI in counts. "Checked by code" means the
card loaded and every quote on it passed the citation check against its page. The verdict (usable, partly, wrong) is
the builder's own read of the card against the manual by the rule written before the run, so it is self-assessed, and
the unit says so. Seconds count the cards built only; each run states its basis (seconds_basis): "wall", wall-clock
from the manual to a loaded card, or "model", the model calls' span in the spend ledger. A run's seconds are compared
only within that run. The dollars per build are not read from these rows: each run line names its build's ledger
tag (run_tag), and eval/cost.py sums that tag's calls (cost.card_build.*, per run).
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from eval.stats import median, percentile, wilson_count

LABELS = Path(__file__).resolve().parent / "labels" / "anyappliance.jsonl"
RUNS = ("baseline", "rerun")
VERDICTS = ("usable", "partly", "wrong")
# how a run's seconds were timed: the unit, and what the note says about it
SECONDS_BASIS = {"wall": ("from manual to card", "wall-clock, lookup and build"),
                 "model": ("of model calls from manual to card",
                       "model time from the spend ledger, the first call's start to the last call's end; it leaves out "
                       "the manual fetch and the code after the last call, so it runs under the wall-clock")}
CLASSES = {1: "a precondition dropped", 2: "a fragment, or a state left changed", 3: "the main upkeep task missing",
           4: "G0 refused a safe job", 5: "the wrong language section", 6: "a step its quote does not back"}


def read(path: Path = LABELS) -> list[dict[str, Any]]:
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def roster_models(rows: list[dict[str, Any]]) -> set[str]:
    """"<maker>/<model>" of every roster appliance that is built: the manuals this measurement covers."""
    return {f"{r['maker']}/{r['model']}" for r in rows if r["record"] == "roster" and r.get("build")}


def _counter(pre: str, n: int, note: str) -> Callable[..., dict[str, Any]]:
    def count(cid: str, k: int, unit: str, extra: str = "") -> dict[str, Any]:
        return {"id": f"{pre}.{cid}", "value": k, "unit": unit, "n": n, "ci": list(wilson_count(k, n)),
                "note": note + extra}
    return count


def claims(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    built = sum(1 for r in rows if r["record"] == "roster" and r.get("build"))
    out: list[dict[str, Any]] = []
    for run in RUNS:
        rs = [r for r in rows if r["record"] == "run" and r["run"] == run]
        pre = f"anyappliance.{run}"
        if not rs:
            out += [{"id": f"{pre}.{k}", "value": None, "unit": u, "n": 0, "ci": None} for k, u in (
                ("checked_by_code", "cards checked by code"), ("usable", "cards usable, by the builder's own read"),
                ("seconds_median", "s from manual to card (median)"))]
            continue
        if len(rs) != built or len({r["appliance_id"] for r in rs}) != built:
            raise ValueError(f"{run}: {len(rs)} rows for {built} roster appliances")
        n = len(rs)
        note = f"{run} run, {n} manuals nobody prepared (eval/labels/anyappliance.jsonl)"
        count = _counter(pre, n, note)

        out.append(count("build_ok", sum(1 for r in rs if r["build_ok"]), "cards built"))
        out.append(count("checked_by_code", sum(1 for r in rs if r["checked_by_code"]), "cards checked by code",
                         "; loaded, every quote passed the citation check against its page"))
        by = f"; verdict by {', '.join(sorted({r['verdict_by'] for r in rs}))}, not independent"
        for v in VERDICTS:
            out.append(count(v, sum(1 for r in rs if r["verdict"] == v), f"cards {v}, by the builder's own read", by))
        for c, what in CLASSES.items():
            out.append(count(f"class{c}", sum(1 for r in rs if c in r["classes"]), f"cards with {what}", by))
        # seconds only where a card came out: "manual to card" has no end for a failed build
        secs = [float(r["seconds"]) for r in rs if r["build_ok"]]
        basis = {r.get("seconds_basis", "wall") for r in rs}
        if len(basis) != 1 or not basis <= set(SECONDS_BASIS):
            raise ValueError(f"{run}: one seconds_basis per run, from {sorted(SECONDS_BASIS)}; got {sorted(basis)}")
        what, how = SECONDS_BASIS[basis.pop()]
        for k, w, q in (("median", "median", median(secs)), ("p10", "10th percentile", percentile(secs, 10)),
                        ("p90", "90th percentile", percentile(secs, 90))):
            out.append({"id": f"{pre}.seconds_{k}", "value": round(q, 1), "unit": f"s {what} ({w})",
                        "n": len(secs), "ci": None, "note": f"{note}; {how}, over the {len(secs)} cards built"})
    return out


def main(argv: list[str]) -> int:
    for c in claims(read()):
        print(json.dumps(c, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

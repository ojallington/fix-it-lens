"""The eval gate for free-credit routing: a role moves to Muse Spark Open on a partner only if it does no worse than
Muse Spark on the same labelled set, and fits the role's timeout.

    FIXIT_CALLS=record python -m eval.partner run --provider baseten    # live, on the partner's free credit
    python -m eval.partner cost                                          # ledger -> per-provider cost, private
    python -m eval.partner score                                         # claims, no network

`run` puts the step-check clips (eval.stepcheck, the 120 pre-registered clips) through the production step check
with the judge on the partner and SAM on Meta, as a routed public session has it, and writes
eval/results/partner/<provider>/judge_sam.jsonl. It needs the partner's key file (it says "waits on key" otherwise)
and the labelled clips. The judge runs at its own reasoning effort ("minimal"), as Muse Spark's does, so the
comparison is like for like. If the partner refuses that effort, the run falls back to "low" (a refused request
costs nothing) and <provider>/run.json says so; `score` repeats it in every claim's note.

`score` compares each provider's predictions with Muse Spark's (eval/results/stepcheck/judge_sam.jsonl). The judge
passes when:
- its false "done" rate is not worse beyond chance: the Wilson 95% intervals overlap, or it is lower;
- its done-recall is not worse beyond chance, by the same test;
- its p95 latency is within the judge role's timeout.

The guide has no recorded-turn set yet, so its gate is unmeasured, and the guide does not move on this row.

`cost` reads FIXIT_PRIVATE/ledger.jsonl and writes eval/results/partner/cost.jsonl: one line per session that a
partner served, anonymised (s01, s02, ...), with what it spent on the partner and on Meta (SAM, voice). `score` then
gives each provider's sessions, spend and median cost per session.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from eval.stats import percentile, wilson
from eval.stepcheck.results import LABELS, RESULTS, Label, Prediction, load_labels, load_predictions, write_predictions

EVAL = Path(__file__).resolve().parent
OUT = EVAL / "results" / "partner"
COST = OUT / "cost.jsonl"
VARIANT = "judge_sam"  # production: the clause judge at minimal and the SAM witness
PROVIDERS = ("baseten", "fireworks", "modal")


FALLBACK_EFFORT = "low"


def effort_refused(e: Exception) -> bool:
    """A partner's 4xx that names the reasoning effort: the request was refused before any work, at no cost."""
    m = str(e).lower()
    return "http_4" in m and ("reasoning_effort" in m or "reasoning effort" in m)


class Split:
    """A routed public session's calls: the partner roles on the partner, everything else (SAM) on Meta.

    `fallback` builds the partner client again with reasoning at "low"; it is used once, the first time the partner
    refuses the role's own effort, and `refused` keeps that refusal for run.json."""

    def __init__(self, meta: Any, partner: Any, fallback: Callable[[], Any] | None = None) -> None:
        self.meta, self.partner, self.fallback = meta, partner, fallback
        self.config = meta.config
        self.refused: str | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.meta, name)

    async def respond(self, role: str, input: Any, **kw: Any) -> Any:
        if role not in self.partner.config.partner_roles:
            return await self.meta.respond(role, input, **kw)
        try:
            return await self.partner.respond(role, input, **kw)
        except Exception as e:
            if self.fallback is None or self.refused is not None or not effort_refused(e):
                raise
            self.refused = str(e)[:300]
            self.partner = self.fallback()
            return await self.partner.respond(role, input, **kw)

    def run_note(self, role_effort: str | None) -> dict[str, Any]:
        used = FALLBACK_EFFORT if self.refused else role_effort
        return {"reasoning_effort": used, "asked": role_effort, "fell_back": self.refused is not None,
                "refusal": self.refused}


def _rate(k: int, n: int) -> tuple[float, float]:
    return wilson(k, n) if n else (0.0, 1.0)


def not_worse(k_new: int, n_new: int, k_base: int, n_base: int, *, lower_is_better: bool) -> bool:
    """The new rate is not worse than the base beyond chance: their Wilson 95% intervals overlap, or it is better."""
    lo_n, hi_n = _rate(k_new, n_new)
    lo_b, hi_b = _rate(k_base, n_base)
    return lo_n <= hi_b if lower_is_better else hi_n >= lo_b


def _counts(labels: dict[str, Label], preds: list[Prediction]) -> dict[str, Any]:
    run = min(p.run for p in preds)
    by = {p.clip_id: p for p in preds if p.run == run}
    ids = [i for i in sorted(labels) if i in by]
    nd = [i for i in ids if labels[i].truth == "not_done"]
    dn = [i for i in ids if labels[i].truth == "done"]
    lat = [float(by[i].latency_ms or 0.0) for i in ids if by[i].latency_ms is not None]
    return {"n": len(ids), "missing": len(labels) - len(ids),
            "false_done": (sum(by[i].result == "done" for i in nd), len(nd)),
            "done_recall": (sum(by[i].result == "done" for i in dn), len(dn)),
            "p95_ms": round(percentile(lat, 95)) if lat else None,
            "usd": round(sum(by[i].usd or 0.0 for i in ids), 4)}


def gate(new: dict[str, Any], base: dict[str, Any], timeout_ms: float) -> tuple[bool, str]:
    why = []
    if new["missing"]:
        why.append(f"{new['missing']} clips unpredicted")
    if not not_worse(*new["false_done"], *base["false_done"], lower_is_better=True):
        why.append("more false done than Muse Spark beyond chance")
    if not not_worse(*new["done_recall"], *base["done_recall"], lower_is_better=False):
        why.append("lower done-recall than Muse Spark beyond chance")
    if new["p95_ms"] is None or new["p95_ms"] > timeout_ms:
        why.append(f"p95 {new['p95_ms']} ms is over the judge's {timeout_ms:g} ms timeout")
    return not why, "; ".join(why) or "not worse than Muse Spark, within the timeout"


def session_costs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per session a partner served: the provider, and the USD spent there and on Meta. Session ids are dropped."""
    by: dict[str, dict[str, Any]] = {}
    for r in rows:
        sid = r.get("session_id")
        if not sid:
            continue
        t = by.setdefault(str(sid), {"provider": None, "partner_usd": 0.0, "meta_usd": 0.0, "calls": 0})
        who = str(r.get("provider") or "meta")
        usd = float(r.get("usd") or 0.0)
        t["calls"] += 1
        if who in PROVIDERS:
            t["provider"] = t["provider"] or who  # a session that moved pool is booked to the first
            t["partner_usd"] += usd
        elif who == "meta":
            t["meta_usd"] += usd
    served = [t for _, t in sorted(by.items()) if t["provider"]]
    return [{"session": f"s{i:02d}", **t, "partner_usd": round(t["partner_usd"], 6),
             "meta_usd": round(t["meta_usd"], 6)} for i, t in enumerate(served, 1)]


def cost_claims(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in PROVIDERS:
        mine = [r for r in rows if r["provider"] == p]
        each = [r["partner_usd"] + r["meta_usd"] for r in mine]
        out += [
            {"id": f"partner.{p}.sessions", "value": len(mine), "unit": "public sessions", "n": len(mine),
             "ci": None},
            {"id": f"partner.{p}.usd", "value": round(sum(r["partner_usd"] for r in mine), 4) if mine else None,
             "unit": "USD of free credit", "n": len(mine), "ci": None},
            {"id": f"partner.{p}.usd_per_session", "value": round(percentile(each, 50), 4) if each else None,
             "unit": "USD (median, partner plus Meta's SAM and voice)", "n": len(mine), "ci": None},
        ]
    return out


def _unmeasured(provider: str, role: str, why: str) -> dict[str, Any]:
    return {"id": f"partner.{provider}.{role}.gate", "value": None, "unit": "pass or fail", "n": 0, "ci": None,
            "note": why}


def claims(labels: dict[str, Label] | None = None, root: Path = OUT, base_root: Path = RESULTS) -> list[dict[str, Any]]:
    from calls import load_config

    labels = load_labels(LABELS) if labels is None else labels
    timeout_ms = load_config().role("judge").timeout_s * 1000
    try:
        base = _counts(labels, load_predictions(VARIANT, base_root)) if labels else None
    except FileNotFoundError:
        base = None
    out: list[dict[str, Any]] = []
    for p in PROVIDERS:
        path = root / p / f"{VARIANT}.jsonl"
        if not labels or base is None or not path.exists():
            out.append(_unmeasured(p, "judge", "waits on key and the labelled clips" if not path.exists()
                                   else "waits on Muse Spark's results for the same clips"))
        else:
            new = _counts(labels, load_predictions(VARIANT, root / p))
            ok, why = gate(new, base, timeout_ms)
            at = effort_note(root / p)
            k, n = new["false_done"]
            out += [
                {"id": f"partner.{p}.judge.gate", "value": "pass" if ok else "fail", "unit": "pass or fail",
                 "n": new["n"], "ci": None, "note": why + at},
                {"id": f"partner.{p}.judge.false_done", "value": k, "unit": "clips of n planted not-done", "n": n,
                 "ci": list(wilson(k, n)) if n else None, "note": f"Muse Spark: {base['false_done'][0]} of "
                                                                   f"{base['false_done'][1]}{at}"},
                {"id": f"partner.{p}.judge.latency_p95", "value": new["p95_ms"], "unit": "ms", "n": new["n"],
                 "ci": None, "note": f"the judge's timeout is {timeout_ms:g} ms{at}"},
            ]
        out.append(_unmeasured(p, "guide", "no recorded guide-turn set yet; the guide does not move on this row"))
    cost = [json.loads(x) for x in COST.read_text().splitlines() if x.strip()] if root == OUT and COST.exists() \
        else []
    return out + cost_claims(cost)


async def run(provider: str) -> Path:
    import calls
    from calls.backends.partner import key_problem
    from eval.stepcheck import l1
    from eval.stepcheck.run import load_variants, run_variant

    cfg = calls.load_config()
    why = key_problem(cfg.partners[provider])
    if why is not None:
        raise SystemExit(f"eval.partner: {why}")
    labels = load_labels(LABELS)
    if not labels:
        raise SystemExit("eval.partner: waits on the labelled step-check clips (eval/labels/stepcheck.jsonl)")
    import os
    from dataclasses import replace

    mode = os.environ.get("FIXIT_CALLS", "replay")
    meta = calls.make_client(mode)  # type: ignore[arg-type]
    low = replace(cfg, partners={**cfg.partners, provider: replace(cfg.partners[provider],
                                                                   reasoning_effort=FALLBACK_EFFORT)})
    split = Split(meta, calls.make_client(mode, partner=provider),  # type: ignore[arg-type]
                  lambda: calls.make_client(mode, config=low, partner=provider))  # type: ignore[arg-type]
    app_stepcheck: Any = __import__("app.stepcheck", fromlist=["check_clip"])
    plain = app_stepcheck.check_clip

    async def routed(*a: Any, **kw: Any) -> Any:
        return await plain(*a, **{**kw, "client": split})

    app_stepcheck.check_clip = routed  # eval.stepcheck.l1 reaches the production step check through this name
    try:
        preds = await run_variant(labels, load_variants()[VARIANT], l1.checker)
    finally:
        app_stepcheck.check_clip = plain
    path = write_predictions(VARIANT, preds, OUT / provider)
    note = split.run_note(cfg.partners[provider].reasoning_effort or cfg.role("judge").effort)
    (OUT / provider / "run.json").write_text(json.dumps(note, indent=1) + "\n")
    return path


def effort_note(root: Path) -> str:
    """What effort the run used, from run.json: "at minimal", or "at low: the partner refused minimal"."""
    try:
        r = json.loads((root / "run.json").read_text())
    except FileNotFoundError:
        return ""
    if r.get("fell_back"):
        return f"; at reasoning_effort {r['reasoning_effort']}: the partner refused {r['asked']}"
    return f"; at reasoning_effort {r['reasoning_effort']}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.partner")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--provider", choices=PROVIDERS, required=True)
    sub.add_parser("cost")
    sub.add_parser("score")
    args = ap.parse_args(argv)
    if args.cmd == "run":
        print(asyncio.run(run(args.provider)))
        return 0
    if args.cmd == "cost":
        from calls import private_dir

        priv = private_dir()
        if priv is None or not (priv / "ledger.jsonl").exists():
            raise SystemExit("eval.partner cost: needs FIXIT_PRIVATE/ledger.jsonl")
        rows = [json.loads(x) for x in (priv / "ledger.jsonl").read_text().splitlines() if x.strip()]
        COST.parent.mkdir(parents=True, exist_ok=True)
        COST.write_text("".join(json.dumps(r) + "\n" for r in session_costs(rows)))
        print(COST)
        return 0
    for c in claims():
        print(json.dumps(c, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

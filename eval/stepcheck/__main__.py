"""python -m eval.stepcheck score [--variant V ...] | table [--variant V] | misses [--variant V] | run --variant V

score   prints the claims (JSON lines) for the variants named, plus the grey and shuffle controls
table   prints the README confusion matrix with claim markers
misses  prints every false "done" with the judge's per-clause evidence (the README's failure list, "A real miss")
ablation prints the README ablation table (single frame -> clause judge -> + crop -> + video witness)
run     re-runs the checker over the clips and rewrites eval/results/stepcheck/<variant>.jsonl

--set heldout|power scores another clip set (eval.stepcheck.results.SETS) as its own row: heldout is the
design's column of the neighbours' clips, power the no-part power steps (unplug). The production variant only,
claims under <prefix>.*, no controls.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from eval.stepcheck.results import (
    SETS,
    Label,
    Prediction,
    load_labels,
    load_predictions,
    write_predictions,
)
from eval.stepcheck.run import load_variants, run_variant
from eval.stepcheck.score import (
    claims_for,
    grey_control,
    runs_of,
    score_run,
    shuffle_control,
    table,
    unmeasured_claims,
)

HEADLINE = "judge_sam"
SCORED = ("judge_sam", "judge", "judge_sam_low")
ABLATION = (("abl_single", "single frame"), ("abl_clauses", "clause judge"), ("judge", "+ crop"),
            ("judge_sam", "+ video witness"))


def ablation(labels: dict[str, Label], preds: dict[str, list[Prediction]]) -> str:
    """The ablation table, each number marked with its claim; a rung with no recorded results is left out."""
    out = ["| rung | balanced accuracy | false \"done\" |", "|---|---|---|"]
    for v, name in ABLATION:
        if v not in preds:
            continue
        s = score_run(labels, preds[v], v, min(runs_of(preds[v])))
        k, n = s.false_done
        pre = f"stepcheck.{v}"
        out.append(f"| {name} | {round(100 * s.confusion.balanced_accuracy(), 1)}%<!--c:{pre}.balanced_accuracy--> | "
                   f"{k}<!--c:{pre}.false_done--> of {n}<!--c:{pre}.false_done:n--> |")
    return "\n".join(out) + "\n"


def misses(labels: dict[str, Label], preds: list[Prediction]) -> str:
    run1 = [p for p in preds if p.run == min(runs_of(preds))]
    out = []
    for p in run1:
        lab = labels[p.clip_id]
        if lab.truth != "not_done" or p.result != "done":
            continue
        guarded = " (a stop guards this step)" if lab.guarded else ""
        out.append(f"### `{p.clip_id}`{guarded}\n")
        out.append(f"Judge: {p.judge_result}; SAM witness: {p.sam_result}; verdict: {p.result}.\n")
        for c in p.clauses:
            out.append(f"- `{c.clause_id}` {c.state} (visible: {str(c.visible).lower()}): {c.evidence}")
        out.append("")
    return "\n".join(out) if out else "No false \"done\".\n"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.stepcheck")
    ap.add_argument("cmd", choices=["score", "table", "misses", "ablation", "run"])
    ap.add_argument("--variant", action="append")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--set", choices=list(SETS), default="main")
    a = ap.parse_args(argv)
    cs = SETS[a.set]
    other = cs.name != "main"
    labels = load_labels(cs.labels)
    root = cs.results
    if not labels:
        if a.cmd == "score":  # registered, not measured yet: n=0 claims, which make eval reports and never writes
            for c in unmeasured_claims(cs.prefix, HEADLINE, headline=not other, controls=not other):
                print(json.dumps(c, ensure_ascii=False))
            return 0
        print(f"no labels for the {a.set} set yet", file=sys.stderr)
        return 1
    abl = [v for v, _ in ABLATION if v not in SCORED and (root / f"{v}.jsonl").exists()]
    variants = a.variant or ([HEADLINE] if a.cmd in ("table", "misses") or other else [*SCORED, *abl])
    if a.cmd == "run":
        from eval.stepcheck.l1 import checker  # the app's production path; imported only to run

        allv = load_variants()
        for v in variants:
            preds = asyncio.run(run_variant(labels, allv[v], checker, runs=a.runs))
            print(write_predictions(v, preds, root))
        return 0
    if a.cmd == "score":
        for v in variants:
            preds = load_predictions(v, root)
            scores = [score_run(labels, preds, v, r) for r in runs_of(preds)]
            for c in claims_for(v, scores, headline=v == HEADLINE and not other, prefix=cs.prefix):
                print(json.dumps(c, ensure_ascii=False))
        if other:
            return 0
        head = load_predictions(HEADLINE)
        print(json.dumps(shuffle_control(labels, head, HEADLINE)))
        print(json.dumps(grey_control(load_predictions("grey"))))
        return 0
    if a.cmd == "ablation":
        have = {v: load_predictions(v, root) for v, _ in ABLATION if (root / f"{v}.jsonl").exists()}
        print(ablation(labels, have), end="")
        return 0
    preds = load_predictions(variants[0], root)
    if a.cmd == "table":
        print(table(variants[0], score_run(labels, preds, variants[0], min(runs_of(preds)))), end="")
    else:
        print(misses(labels, preds))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

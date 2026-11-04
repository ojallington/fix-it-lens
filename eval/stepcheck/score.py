"""Score recorded step-check predictions against the labels: the confusion matrix, false "done" with its Wilson CI,
false "done" on the steps a stop guards, balanced accuracy, done recall, latency, cost, and the controls.

Run 1 of each variant is the published pass; later runs (the model is not deterministic) give the range.
Every number printed here is a claims.json line: `score` prints the claims as JSON lines for the harness, and
`table` prints the README's markdown with the <!--c:id--> markers already in place.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from eval.stats import CLASSES, Confusion, confusion, percentile, shuffled_balanced_accuracy, wilson_count, wilson_pct
from eval.stepcheck.results import Label, Prediction

TARGETS = {"balanced_accuracy": 90.0, "false_done": "2 of 40", "false_done_guarded": "0", "grey_cant_see": "all"}


class ScoreError(ValueError):
    pass


@dataclass(frozen=True)
class RunScore:
    variant: str
    run: int
    confusion: Confusion
    false_done: tuple[int, int]
    false_done_guarded: tuple[int, int]
    false_done_clips: list[str]
    latency_ms: list[float]
    usd: float


def score_run(labels: dict[str, Label], preds: list[Prediction], variant: str, run: int) -> RunScore:
    mine = [p for p in preds if p.run == run]
    by_clip = {p.clip_id: p for p in mine}
    if len(by_clip) != len(mine):
        raise ScoreError(f"{variant} run {run}: a clip is predicted twice")
    missing = sorted(set(labels) - set(by_clip))
    if missing:
        raise ScoreError(f"{variant} run {run}: no prediction for {len(missing)} labelled clips, e.g. {missing[:3]}")
    extra = sorted(set(by_clip) - set(labels))
    if extra:
        raise ScoreError(f"{variant} run {run}: predictions for unlabelled clips {extra[:3]}")
    ids = sorted(labels)
    cm = confusion([labels[i].truth for i in ids], [by_clip[i].result for i in ids])
    fd_clips = [i for i in ids if labels[i].truth == "not_done" and by_clip[i].result == "done"]
    guarded_nd = [i for i in ids if labels[i].truth == "not_done" and labels[i].guarded]
    fd_guarded = sum(1 for i in guarded_nd if by_clip[i].result == "done")
    lat = [p.latency_ms for p in mine if p.latency_ms is not None]
    return RunScore(variant, run, cm, cm.false_done(), (fd_guarded, len(guarded_nd)), fd_clips, lat,
                    sum(p.usd or 0.0 for p in mine))


def runs_of(preds: list[Prediction]) -> list[int]:
    return sorted({p.run for p in preds})


def _pct(x: float) -> float:
    return round(100 * x, 1)


def claims_for(variant: str, scores: list[RunScore], *, headline: bool = False,
               prefix: str = "stepcheck") -> list[dict[str, Any]]:
    """Claims for one variant: run 1 is published, the other runs give the range in the note."""
    s = scores[0]
    cm = s.confusion
    pre = f"{prefix}.{variant}"
    k, n = s.false_done
    kg, ng = s.false_done_guarded
    others = "; ".join(f"run {r.run}: {r.false_done[0]} of {r.false_done[1]}" for r in scores[1:])
    out: list[dict[str, Any]] = [
        {"id": f"{pre}.balanced_accuracy", "value": _pct(cm.balanced_accuracy()), "unit": "%", "n": cm.n,
         "ci": None},
        {"id": f"{pre}.false_done", "value": k, "unit": "clips of n planted not-done", "n": n,
         "ci": list(wilson_count(k, n)) if n else None,
         "note": "ci is Wilson 95%, in clips" + (f"; other runs {others}" if others else "")},
        {"id": f"{pre}.false_done_guarded", "value": kg, "unit": "clips of n guarded not-done", "n": ng,
         "ci": list(wilson_count(kg, ng)) if ng else None},
    ]
    done_recall = cm.recall("done")
    if done_recall is not None:
        out.append({"id": f"{pre}.done_recall", "value": _pct(done_recall), "unit": "%", "n": cm.row_total("done"),
                    "ci": list(wilson_pct(cm.cell("done", "done"), cm.row_total("done")))})
    if s.latency_ms:
        out.append({"id": f"{pre}.latency_p95", "value": round(percentile(s.latency_ms, 95)), "unit": "ms",
                    "n": len(s.latency_ms), "ci": None})
    out.append({"id": f"{pre}.usd_per_check", "value": round(s.usd / cm.n, 4), "unit": "USD", "n": cm.n,
                "ci": None})
    for t in CLASSES:
        for p in CLASSES:
            out.append({"id": f"{pre}.cm.{t}.{p}", "value": cm.cell(t, p), "unit": "clips", "n": cm.n, "ci": None})
    if headline:
        for c in out:
            key = c["id"].removeprefix(f"{pre}.")
            if key in TARGETS:
                c["target"] = TARGETS[key]
    return out


def unmeasured_claims(prefix: str, variant: str, *, headline: bool, controls: bool) -> list[dict[str, Any]]:
    """The headline claims before any clip is labelled: n=0, so `make eval` lists them as not measured yet and the
    README, site and video can say so instead of saying nothing."""
    pre = f"{prefix}.{variant}"
    out: list[dict[str, Any]] = [
        {"id": f"{pre}.balanced_accuracy", "value": None, "unit": "%", "n": 0, "ci": None},
        {"id": f"{pre}.false_done", "value": None, "unit": "clips of n planted not-done", "n": 0, "ci": None},
        {"id": f"{pre}.false_done_guarded", "value": None, "unit": "clips of n guarded not-done", "n": 0, "ci": None},
    ]
    if headline:
        for c in out:
            c["target"] = TARGETS[c["id"].removeprefix(f"{pre}.")]
    if controls:
        out += [{"id": "stepcheck.control.shuffled_balanced_accuracy", "value": None, "unit": "%", "n": 0,
                 "ci": None},
                {"id": "stepcheck.control.grey_cant_see", "value": None, "unit": "clips of n with grey frames",
                 "n": 0, "ci": None, "target": TARGETS["grey_cant_see"]}]
    return out


def grey_control(preds: list[Prediction]) -> dict[str, Any]:
    run1 = [p for p in preds if p.run == min(runs_of(preds))]
    k = sum(1 for p in run1 if p.result == "cant_see")
    return {"id": "stepcheck.control.grey_cant_see", "value": k, "unit": "clips of n with grey frames", "n": len(run1),
            "ci": list(wilson_count(k, len(run1))) if run1 else None, "target": TARGETS["grey_cant_see"]}


def shuffle_control(labels: dict[str, Label], preds: list[Prediction], variant: str) -> dict[str, Any]:
    run1 = {p.clip_id: p.result for p in preds if p.run == min(runs_of(preds))}
    ids = sorted(labels)
    mean, lo, hi = shuffled_balanced_accuracy([labels[i].truth for i in ids], [run1[i] for i in ids], seed=0)
    return {"id": "stepcheck.control.shuffled_balanced_accuracy", "value": _pct(mean), "unit": "%", "n": len(ids),
            "ci": [_pct(lo), _pct(hi)], "note": f"labels shuffled 200 times (seed 0) against {variant} run 1; "
                                               "ci is the 2.5-97.5 percentile; chance is 33.3%"}


def table(variant: str, s: RunScore) -> str:
    """The README's confusion matrix, each cell already marked with its claim."""
    pre = f"stepcheck.{variant}.cm"
    head = "| truth \\ predicted | done | not done | can't see |\n|---|---|---|---|\n"
    rows = []
    for t in CLASSES:
        cells = " | ".join(f"{s.confusion.cell(t, p)}<!--c:{pre}.{t}.{p}-->" for p in CLASSES)
        rows.append(f"| {t.replace('_', ' ')} | {cells} |")
    return head + "\n".join(rows) + "\n"

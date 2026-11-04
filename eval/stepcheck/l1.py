"""The checker the step-check eval runs: the app's production path in app/, one clip at a time.

It calls the app's one entry point (session.check runs the same judge_and_witness):

    async app.stepcheck.check_clip(card: Card, step_id: str, before: bytes, now: list[tuple[float, bytes]], *,
                                   effort="minimal", witness="sam" | "none", lang="en", lane="l6",
                                   crop=True, clauses=True) -> (Verdict, JudgeOutput | None, usd)

so the eval measures exactly what the app ships. `now` times are seconds. With witness="none" the verdict is never
done (the production rule needs two witnesses), so a no-witness variant is scored on the judge's own finding
(Verdict.witnesses.judge): that is the judge-alone column and the ablation's first three rungs.
"""

from __future__ import annotations

import functools
import importlib
from pathlib import Path
from typing import Any

from eval.shotlist import Step, load_steps
from eval.stepcheck.results import ClauseFinding, Label, Prediction
from eval.stepcheck.run import ClipFrames, Variant

ROOT = Path(__file__).resolve().parents[2]


@functools.cache
def _steps() -> dict[str, Step]:
    steps, _, _ = load_steps()
    return {s.id: s for s in steps}


@functools.cache
def _card(path: str) -> Any:
    from app.cards import read

    return read(ROOT / path)


def _card_step(label: Label) -> tuple[Any, str]:
    """(Card, card step id) for an eval step, from its card and card_step in eval/steps.toml."""
    s = _steps()[label.step_id]
    if s.card is None or s.card_step is None:
        raise NotImplementedError(f"{label.step_id}: eval/steps.toml names no card step yet")
    return _card(s.card), s.card_step


async def checker(clip: ClipFrames, label: Label, variant: Variant, run: int) -> Prediction:
    try:
        stepcheck: Any = importlib.import_module("app.stepcheck")
    except ModuleNotFoundError as e:
        raise SystemExit("app.stepcheck.check_clip is not on this branch yet (app/); nothing to run") from e
    card, step_id = _card_step(label)
    now = [(t / 1000, jpeg) for t, jpeg in zip(clip.now_t_ms, clip.now, strict=True)]  # check_clip takes seconds
    ablation = {k: False for k in ("crop", "clauses") if not getattr(variant, k)}  # sent only when switched off
    verdict, out, usd = await stepcheck.check_clip(card, step_id, clip.before, now, effort=variant.effort,
                                                   witness=variant.witness, lane="l6", **ablation)
    clauses = [ClauseFinding(c.clause_id, c.visible, c.state, c.evidence) for c in (out.clauses if out else [])]
    judged = str(verdict.witnesses.judge)
    result = judged if variant.witness == "none" else str(verdict.result)
    return Prediction(clip.clip_id, variant.id, run, result, judge_result=judged,
                      sam_result=verdict.witnesses.sam_result, clauses=clauses, ask_for=verdict.ask_for,
                      latency_ms=verdict.latency_ms, usd=usd)

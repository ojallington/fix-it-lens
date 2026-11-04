"""The eval's adapter to the app's check_clip: units, the ablation switches, and which result a variant is scored on."""

import asyncio
import sys
import types
from typing import Any

import pytest

from contracts.models.judge import Verdict, Witnesses
from eval.stepcheck import l1
from eval.stepcheck.results import Label
from eval.stepcheck.run import ClipFrames, load_variants

LABEL = Label("dw.filter_out.not_done.t1", "dw.filter_out", "dishwasher", "not_done", 1, False)
CLIP = ClipFrames(LABEL.clip_id, b"before", [b"n1", b"n2"], [500.0, 1500.0])


@pytest.fixture()
def fake(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    async def check_clip(card: Any, step_id: str, before: bytes, now: list[tuple[float, bytes]],
                         **kw: Any) -> tuple[Verdict, None, float]:
        calls.append({"step_id": step_id, "now": now, **kw})
        sam = "done" if kw["witness"] == "sam" else None
        v = Verdict(result="not_done" if kw["witness"] == "none" else "done", clause_ids=[],
                    witnesses=Witnesses(judge="done", sam="video" if sam else "none", sam_result=sam),
                    latency_ms=1200.0)
        return v, None, 0.004

    monkeypatch.setitem(sys.modules, "app.stepcheck", types.SimpleNamespace(check_clip=check_clip))
    monkeypatch.setattr(l1, "_card_step", lambda label: ("CARD", "remove_filter_system"))
    return calls


def test_production_variant_scores_the_verdict(fake: list[dict[str, Any]]) -> None:
    p = asyncio.run(l1.checker(CLIP, LABEL, load_variants()["judge_sam"], 1))
    assert (p.result, p.judge_result, p.sam_result, p.usd) == ("done", "done", "done", 0.004)
    call = fake[0]
    assert call["now"] == [(0.5, b"n1"), (1.5, b"n2")]  # seconds, not ms
    assert "crop" not in call and "clauses" not in call  # production defaults are never sent


def test_no_witness_variant_scores_the_judge(fake: list[dict[str, Any]]) -> None:
    p = asyncio.run(l1.checker(CLIP, LABEL, load_variants()["abl_clauses"], 1))
    assert (p.result, p.judge_result) == ("done", "done")  # the verdict alone would read not_done
    assert fake[0]["crop"] is False and "clauses" not in fake[0]


def test_card_step_from_steps_toml() -> None:
    pytest.importorskip("app.cards")
    card, step = l1._card_step(LABEL)
    assert step == "remove_filter_system" and any(s.id == step for j in card.jobs for s in j.steps)
    with pytest.raises(NotImplementedError):
        l1._card_step(Label("wm.cover_open.done.t1", "wm.cover_open", "washer", "done", 1, True))

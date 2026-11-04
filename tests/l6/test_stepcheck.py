import asyncio
import io
import json
from pathlib import Path

import pytest
from PIL import Image

from eval.shotlist import load_steps, plan
from eval.stepcheck.__main__ import ablation, misses
from eval.stepcheck.results import ClauseFinding, Label, Prediction, load_predictions, write_predictions
from eval.stepcheck.run import ClipFrames, Variant, greyed, load_clip, load_variants, run_variant
from eval.stepcheck.score import ScoreError, claims_for, grey_control, score_run, shuffle_control, table


def _labels() -> dict[str, Label]:
    steps, _, kinds = load_steps()
    return {c.clip_id: Label(c.clip_id, c.step_id, c.appliance, c.truth, c.take, c.guarded)
            for c in plan([s for s in steps if s.set == "main"], kinds)}


def _preds(labels: dict[str, Label], variant: str = "judge_sam", run: int = 1,
           wrong_done: int = 2) -> list[Prediction]:
    out, planted = [], 0
    for cid, lab in sorted(labels.items()):
        result = lab.truth
        if lab.truth == "not_done" and not lab.guarded and planted < wrong_done:
            result, planted = "done", planted + 1
        out.append(Prediction(cid, variant, run, result, judge_result=result, sam_result="done",
                              clauses=[ClauseFinding("c1", True, "satisfied", "filter out of sump")],
                              latency_ms=1000.0 + len(out), usd=0.01))
    return out


def test_plan_is_120_clips_balanced() -> None:
    labels = _labels()
    assert len(labels) == 120
    assert sum(1 for x in labels.values() if x.truth == "not_done") == 40


def test_score_and_claims() -> None:
    labels = _labels()
    s = score_run(labels, _preds(labels), "judge_sam", 1)
    assert s.confusion.n == 120 == sum(map(sum, s.confusion.as_lists()))
    assert s.false_done == (2, 40) and s.false_done_guarded[0] == 0
    claims = {c["id"]: c for c in claims_for("judge_sam", [s], headline=True)}
    fd = claims["stepcheck.judge_sam.false_done"]
    assert (fd["value"], fd["n"], fd["ci"]) == (2, 40, [0.6, 6.6])  # 1.4-16.5% of 40
    assert fd["target"] == "2 of 40"
    assert claims["stepcheck.judge_sam.cm.not_done.done"]["value"] == 2
    assert claims["stepcheck.judge_sam.balanced_accuracy"]["value"] == pytest.approx(100 * (1 + 38 / 40 + 1) / 3, 0.01)
    assert "2<!--c:stepcheck.judge_sam.cm.not_done.done-->" in table("judge_sam", s)


def test_heldout_claims_have_their_own_prefix_and_no_target() -> None:
    labels = _labels()
    s = score_run(labels, _preds(labels), "judge_sam", 1)
    claims = claims_for("judge_sam", [s], prefix="heldout")
    assert all(c["id"].startswith("heldout.judge_sam.") for c in claims)
    assert not any("target" in c for c in claims)


def test_missing_prediction_fails() -> None:
    labels = _labels()
    with pytest.raises(ScoreError, match="no prediction"):
        score_run(labels, _preds(labels)[:-1], "judge_sam", 1)


def test_shuffle_control_within_chance() -> None:
    labels = _labels()
    c = shuffle_control(labels, _preds(labels), "judge_sam")
    assert c["ci"][0] <= 33.3 <= c["ci"][1]


def _jpeg(color: tuple[int, int, int]) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", (64, 48), color).save(b, "JPEG")
    return b.getvalue()


def test_grey_control_runs_on_grey_frames() -> None:
    labels = dict(list(_labels().items())[:6])
    seen = []

    async def checker(clip: ClipFrames, lab: Label, v: Variant, run: int) -> Prediction:
        with Image.open(io.BytesIO(clip.now[0])) as im:
            lo, hi = im.convert("L").getextrema()
            flat = hi - lo <= 3
        seen.append(flat)
        return Prediction(clip.clip_id, v.id, run, "cant_see" if flat else lab.truth)

    grey = Variant("grey", "", "minimal", "sam", grey=True)
    red, green = _jpeg((200, 10, 10)), _jpeg((0, 90, 0))
    preds = asyncio.run(run_variant(labels, grey, checker, loader=lambda cid: ClipFrames(cid, red, [green], [0.0])))
    assert all(seen)
    c = grey_control(preds)
    assert c["value"] == c["n"] == 6
    assert greyed(ClipFrames("x", _jpeg((1, 2, 3)), [_jpeg((9, 9, 9))], [0.0])).now[0] != _jpeg((9, 9, 9))


def test_results_roundtrip_and_misses(tmp_path: Path) -> None:
    labels = _labels()
    write_predictions("judge_sam", _preds(labels), tmp_path)
    back = load_predictions("judge_sam", tmp_path)
    assert len(back) == 120 and back[0].clauses[0].evidence == "filter out of sump"
    text = misses(labels, back)
    assert text.count("### ") == 2 and "filter out of sump" in text


def test_load_clip(tmp_path: Path) -> None:
    d = tmp_path / "dw.x.done.t1" / "frames"
    d.mkdir(parents=True)
    (d / "000_before.jpg").write_bytes(b"b")
    (d / "001_now.jpg").write_bytes(b"n1")
    (d / "002_now.jpg").write_bytes(b"n2")
    c = load_clip("dw.x.done.t1", [tmp_path / "nope", tmp_path])
    assert c.before == b"b" and c.now == [b"n1", b"n2"] and c.now_t_ms == [0.0, 100.0]
    with pytest.raises(FileNotFoundError):
        load_clip("absent", [tmp_path])
    json.dumps([1])


def test_ablation_single_frame_rung_sends_last_frame_only() -> None:
    labels = dict(list(_labels().items())[:2])
    seen: list[tuple[int, list[float]]] = []

    async def checker(clip: ClipFrames, lab: Label, v: Variant, run: int) -> Prediction:
        seen.append((len(clip.now), clip.now_t_ms))
        return Prediction(clip.clip_id, v.id, run, lab.truth)

    v = load_variants()["abl_single"]
    assert (v.frames, v.crop, v.clauses, v.witness) == ("last", False, False, "none")
    asyncio.run(run_variant(labels, v, checker, loader=lambda cid: ClipFrames(cid, b"b", [b"1", b"2", b"3"],
                                                                                 [0.0, 500.0, 1000.0])))
    assert seen == [(1, [1000.0]), (1, [1000.0])]


def test_ablation_table_marks_each_rung() -> None:
    labels = _labels()
    preds = {v: _preds(labels, v, wrong_done=w) for v, w in (("abl_single", 9), ("judge_sam", 2))}
    md = ablation(labels, preds)
    assert "| single frame |" in md and "| + video witness |" in md and "clause judge" not in md
    assert "9<!--c:stepcheck.abl_single.false_done--> of 40<!--c:stepcheck.abl_single.false_done:n-->" in md


def test_power_set_is_its_own_plan_and_row() -> None:
    from eval.shotlist import POWER_PLAN
    from eval.stepcheck.results import SETS

    steps, _, kinds = load_steps()
    power = [s for s in steps if s.set == "power"]
    assert [s.id for s in power] == ["dw.unplug"] and power[0].card_step == "unplug_appliance"
    lines = [json.loads(x) for x in POWER_PLAN.read_text().splitlines()]
    assert len(lines) == 6 and {x["truth"] for x in lines} == {"done", "not_done", "cant_see"}
    assert not any(x["step_id"] == "dw.unplug" for x in map(json.loads, SETS["main"].plan.read_text().splitlines()))
    ps = SETS["power"]
    assert (ps.labels.name, ps.results.name, ps.prefix) == ("power.jsonl", "stepcheck-power", "power")
    shot_list = Path(__file__).resolve().parents[2] / "eval" / "shot-list.md"
    assert "Plug frames for the app's plug-out row" in shot_list.read_text()

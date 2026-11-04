from eval.claims import scan_pending
from eval.harness import RowResult, pending
from eval.rows import Row
from eval.stepcheck.score import unmeasured_claims


def _row(rid: str) -> Row:
    return Row(id=rid, owner="l6", command="x", fixtures=[], readme_anchor="#eval", claims=["*"],
               unmeasured="needs the clips")


def test_pending_lists_the_unmeasured_claims_and_keeps_rows_not_run() -> None:
    sc = RowResult(_row("stepcheck"), unmeasured_claims("stepcheck", "judge_sam", headline=True, controls=False))
    old = [{"id": "other.x", "unit": "u", "rows": ["other"], "needs": ""},
           {"id": "stepcheck.gone", "unit": "u", "rows": ["stepcheck"], "needs": ""}]
    got = {p["id"]: p for p in pending([sc], [], old)}
    assert set(got) == {"other.x", "stepcheck.judge_sam.balanced_accuracy", "stepcheck.judge_sam.false_done",
                        "stepcheck.judge_sam.false_done_guarded"}
    assert got["stepcheck.judge_sam.false_done"] == {"id": "stepcheck.judge_sam.false_done", "unit":
                                                     "clips of n planted not-done", "rows": ["stepcheck"],
                                                     "needs": "needs the clips", "target": "2 of 40"}


def test_a_pending_marker_must_be_pending_and_say_not_measured() -> None:
    ok = "False done on planted steps: not measured yet<!--p:stepcheck.judge_sam.false_done-->\n"
    assert scan_pending("README.md", ok, {"stepcheck.judge_sam.false_done"}, set()) == []
    assert scan_pending("README.md", ok, set(), set())[0].message.startswith("p:stepcheck.judge_sam.false_done is not")
    assert "measured it" in scan_pending("README.md", ok, set(), {"stepcheck.judge_sam.false_done"})[0].message
    bare = "False done: soon<!--p:stepcheck.judge_sam.false_done-->\n"
    assert "not measured" in scan_pending("README.md", bare, {"stepcheck.judge_sam.false_done"}, set())[0].message

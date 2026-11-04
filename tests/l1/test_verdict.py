"""The verdict table. Every combination of clause records gives the right result, and a point outside
the part's mask cannot give done. The judge alone never passes a step."""

from __future__ import annotations

import itertools

import pytest

from app.geometry import Region, parse_path
from app.verdict import JudgeFinding, PowerWitness, SamWitness, combine, judge_finding
from contracts.models.judge import JudgeOutput

MASK = Region.from_mask("f1", 1000, 1000, (400, 400, 600, 600), "M400 400L600 400L600 600L400 600Z", 0.9)
IN, OUT = [500, 500], [100, 100]


def out(*clauses: tuple[str, bool, str, list[int] | None]) -> JudgeOutput:
    return JudgeOutput.model_validate({"clauses": [
        {"clause_id": c, "visible": v, "state": s, "evidence": "e", "point": p} for c, v, s, p in clauses]})


def expected(visible: bool, state: str, point_in: bool) -> str:
    if visible and state == "not_satisfied":
        return "not_done"
    if not (visible and state == "satisfied"):
        return "cant_see"
    return "done" if point_in else "cant_see"


RECORDS = list(itertools.product([True, False], ["satisfied", "not_satisfied", "unclear"], [True, False]))


@pytest.mark.parametrize(("a", "b"), list(itertools.product(RECORDS, RECORDS)))
def test_every_pair_of_clause_records(a: tuple[bool, str, bool], b: tuple[bool, str, bool]) -> None:
    j = out(("c1", a[0], a[1], IN if a[2] else OUT), ("c2", b[0], b[1], IN if b[2] else OUT))
    got = judge_finding(j, ["c1", "c2"], MASK).result
    ea, eb = expected(*a), expected(*b)
    want = "not_done" if "not_done" in (ea, eb) else ("cant_see" if "cant_see" in (ea, eb) else "done")
    assert got == want


def test_point_outside_mask_is_never_done() -> None:
    assert judge_finding(out(("c1", True, "satisfied", OUT)), ["c1"], MASK).result == "cant_see"
    assert judge_finding(out(("c1", True, "satisfied", None)), ["c1"], MASK).result == "cant_see"
    assert judge_finding(out(("c1", True, "satisfied", IN)), ["c1"], None).result == "cant_see"
    # inside the box but outside an L-shaped mask
    lpath = "M0 0L1000 0L1000 200L200 200L200 1000L0 1000Z"
    lshape = Region.from_mask("f1", 1000, 1000, (0, 0, 1000, 1000), lpath, 0.9)
    assert not lshape.contains(600, 600) and lshape.contains(100, 600)
    assert judge_finding(out(("c1", True, "satisfied", [600, 600])), ["c1"], lshape).result == "cant_see"


def test_missing_extra_or_duplicate_clauses_are_cant_see() -> None:
    sat = ("c1", True, "satisfied", IN)
    assert judge_finding(out(sat), ["c1", "c2"], MASK).result == "cant_see"
    assert judge_finding(out(sat, ("c2", True, "satisfied", IN)), ["c1"], MASK).result == "cant_see"
    assert judge_finding(out(sat, sat), ["c1"], MASK).result == "cant_see"
    assert judge_finding(None, ["c1"], MASK).result == "cant_see"


DONE = JudgeFinding("done", ["c1"], None, "")


@pytest.mark.parametrize(("sam", "want"), [
    (SamWitness("video", "done"), "done"),
    (SamWitness("frame", "done"), "done"),
    (SamWitness("video", "not_done"), "not_done"),
    (SamWitness("video", "cant_see"), "cant_see"),
    (SamWitness("none", None), "cant_see"),
])
def test_two_witnesses_must_agree(sam: SamWitness, want: str) -> None:
    v = combine("s1", "c1", 1, DONE, sam, None, 10)
    assert v.result == want and v.witnesses.sam == sam.ran and v.witnesses.judge == "done"


def test_power_witness_gates_done() -> None:
    sam = SamWitness("frame", "done")
    assert combine("s", "c", 1, DONE, sam, PowerWitness("not_done", "plug in"), 1).result == "not_done"
    assert combine("s", "c", 1, DONE, sam, PowerWitness("done", "out"), 1).result == "done"
    nd = JudgeFinding("not_done", ["c1"], None, "")
    assert combine("s", "c", 1, nd, sam, PowerWitness("done", "out"), 1).result == "not_done"


def test_parse_path_reads_sam_paths() -> None:
    polys = parse_path("M-0.5 0L0 -0.5L5.5 -0.5L5.5 10L-0.5 10Z")
    assert len(polys) == 1 and len(polys[0]) == 5

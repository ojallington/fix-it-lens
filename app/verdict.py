"""The step verdict, computed in code. The judge model writes per-clause findings; SAM is the second witness; this
module alone turns them into `done | not_done | cant_see`. No model ever writes a Verdict.

done needs all of:
- the judge's reply is well formed and covers exactly the step's done-when clauses;
- every clause is visible and satisfied, with its evidence point inside the part's mask on NOW_1;
- the SAM witness finds the part present and stable where it belongs;
- on a `power: off` step, the power witness saw the plug out (and HA, where it has a power entity, reads off).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.geometry import Region
from contracts.models.judge import JudgeOutput, Verdict, Witnesses

Result = Literal["done", "not_done", "cant_see"]
AskFor = Literal["closer", "side", "light"]


@dataclass(frozen=True)
class JudgeFinding:
    result: Result
    clause_ids: list[str]
    ask_for: AskFor | None
    reason: str


def judge_finding(out: JudgeOutput | None, expected: list[str], region: Region | None) -> JudgeFinding:
    if out is None:
        return JudgeFinding("cant_see", list(expected), "closer", "malformed judge reply")
    ids = [c.clause_id for c in out.clauses]
    if sorted(ids) != sorted(expected) or len(set(ids)) != len(ids):
        missing = [c for c in expected if c not in ids]
        return JudgeFinding("cant_see", missing or list(expected), "closer", "the reply does not cover the clauses")
    failed = [c.clause_id for c in out.clauses if c.visible and c.state == "not_satisfied"]
    if failed:
        return JudgeFinding("not_done", failed, None, "a visible clause is not satisfied")
    unclear = [c.clause_id for c in out.clauses if not (c.visible and c.state == "satisfied")]
    if unclear:
        return JudgeFinding("cant_see", unclear, "closer", "a clause is not visible or unclear")
    if region is None:
        return JudgeFinding("cant_see", list(expected), "closer", "no outline of the part to ground the evidence")
    outside = [c.clause_id for c in out.clauses
               if c.point is None or not region.contains(c.point.root[0].root, c.point.root[1].root)]
    if outside:
        return JudgeFinding("cant_see", outside, "closer", "evidence point outside the part")
    return JudgeFinding("done", list(expected), None, "every clause visible, satisfied and on the part")


@dataclass(frozen=True)
class SamWitness:
    # track: the local backend's no-SAM witness (Glimmer locate + optical flow)
    ran: Literal["video", "frame", "track", "none"]
    result: Result | None
    reason: str = ""


@dataclass(frozen=True)
class PowerWitness:
    result: Result
    reason: str


def combine(step_id: str, check_id: str, attempt: int, judge: JudgeFinding, sam: SamWitness,
            power: PowerWitness | None, latency_ms: float) -> Verdict:
    result: Result
    clause_ids = judge.clause_ids
    ask_for = judge.ask_for
    if judge.result != "done":
        result = judge.result
    elif sam.result != "done":
        # the judge alone never passes a step: no SAM witness, or a SAM witness that disagrees, is not done
        result = "not_done" if sam.result == "not_done" else "cant_see"
        ask_for = None if result == "not_done" else "closer"
    else:
        result = "done"
    if result == "done" and power is not None and power.result != "done":
        result = power.result
        ask_for = "closer" if result == "cant_see" else None
    return Verdict(
        step_id=step_id, check_id=check_id, attempt=attempt, result=result, clause_ids=clause_ids,
        witnesses=Witnesses(judge=judge.result, sam=sam.ran, sam_result=sam.result),
        latency_ms=round(max(0.0, latency_ms), 1), ask_for=ask_for if result != "done" else None,
    )

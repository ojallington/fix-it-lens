"""The eval gate for free-credit routing, and the cost per provider. No key and no network."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import pytest

import calls
from eval import partner as ev
from eval.stepcheck.results import Label, Prediction


def labels(n_done: int = 20, n_not: int = 20) -> dict[str, Label]:
    out = {f"d{i:02d}": Label(f"d{i:02d}", "s", "washer", "done", 1, False) for i in range(n_done)}
    out |= {f"n{i:02d}": Label(f"n{i:02d}", "s", "washer", "not_done", 1, False) for i in range(n_not)}
    return out


def preds(lab: dict[str, Label], *, false_done: int, missed_done: int, ms: float = 1500.0) -> list[Prediction]:
    out = []
    fd = md = 0
    for cid, lb in sorted(lab.items()):
        if lb.truth == "not_done":
            result = "done" if fd < false_done else "not_done"
            fd += 1
        else:
            result = "not_done" if md < missed_done else "done"
            md += 1
        out.append(Prediction(cid, ev.VARIANT, 1, result, latency_ms=ms, usd=0.002))
    return out


def write(root: Path, ps: list[Prediction]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / f"{ev.VARIANT}.jsonl").write_text("".join(json.dumps(asdict(p)) + "\n" for p in ps))


def by_id(claims: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in claims}


def test_with_nothing_recorded_every_gate_is_unmeasured(tmp_path: Path) -> None:
    c = by_id(ev.claims(labels(), root=tmp_path / "p", base_root=tmp_path / "b"))
    for p in ev.PROVIDERS:
        assert c[f"partner.{p}.judge.gate"]["n"] == 0 and c[f"partner.{p}.guide.gate"]["n"] == 0


def test_as_good_as_spark_passes(tmp_path: Path) -> None:
    lab = labels()
    write(tmp_path / "b", preds(lab, false_done=1, missed_done=2))
    write(tmp_path / "p" / "baseten", preds(lab, false_done=2, missed_done=2))
    c = by_id(ev.claims(lab, root=tmp_path / "p", base_root=tmp_path / "b"))
    assert c["partner.baseten.judge.gate"]["value"] == "pass"
    assert c["partner.baseten.judge.false_done"]["value"] == 2 and c["partner.baseten.judge.false_done"]["n"] == 20
    assert c["partner.fireworks.judge.gate"]["n"] == 0  # nothing recorded there


def test_many_more_false_dones_fail(tmp_path: Path) -> None:
    lab = labels()
    write(tmp_path / "b", preds(lab, false_done=0, missed_done=1))
    write(tmp_path / "p" / "baseten", preds(lab, false_done=9, missed_done=1))
    c = by_id(ev.claims(lab, root=tmp_path / "p", base_root=tmp_path / "b"))
    assert c["partner.baseten.judge.gate"]["value"] == "fail"
    assert "false done" in c["partner.baseten.judge.gate"]["note"]


def test_too_slow_for_the_judges_timeout_fails(tmp_path: Path) -> None:
    lab = labels()
    write(tmp_path / "b", preds(lab, false_done=1, missed_done=1))
    write(tmp_path / "p" / "baseten", preds(lab, false_done=1, missed_done=1, ms=60_000))
    gate = by_id(ev.claims(lab, root=tmp_path / "p", base_root=tmp_path / "b"))["partner.baseten.judge.gate"]
    assert gate["value"] == "fail" and "timeout" in gate["note"]


def test_unpredicted_clips_fail(tmp_path: Path) -> None:
    lab = labels()
    write(tmp_path / "b", preds(lab, false_done=1, missed_done=1))
    write(tmp_path / "p" / "baseten", preds(lab, false_done=1, missed_done=1)[:-3])
    gate = by_id(ev.claims(lab, root=tmp_path / "p", base_root=tmp_path / "b"))["partner.baseten.judge.gate"]
    assert gate["value"] == "fail" and "3 clips unpredicted" in gate["note"]


def test_not_worse_is_an_interval_test() -> None:
    assert ev.not_worse(2, 20, 1, 20, lower_is_better=True)
    assert not ev.not_worse(15, 20, 0, 20, lower_is_better=True)
    assert ev.not_worse(18, 20, 19, 20, lower_is_better=False)
    assert not ev.not_worse(4, 20, 20, 20, lower_is_better=False)


def test_run_says_waits_on_key_without_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import calls

    cfg = calls.load_config()
    gone = replace(cfg.partners["baseten"], key_path=tmp_path / "baseten.key")
    monkeypatch.setattr(calls, "load_config", lambda: replace(cfg, partners={**cfg.partners, "baseten": gone}))
    with pytest.raises(SystemExit, match="baseten: waits on key"):
        asyncio.run(ev.run("baseten"))


def test_cost_per_session_and_provider() -> None:
    rows = [
        {"session_id": "a", "provider": "baseten", "usd": 0.04},
        {"session_id": "a", "provider": "baseten", "usd": 0.02},
        {"session_id": "a", "usd": 0.005},  # SAM on Meta (no provider: Meta)
        {"session_id": "b", "provider": "fireworks", "usd": 0.03},
        {"session_id": "c", "provider": "meta", "usd": 1.0},  # a session no partner served: not counted
        {"session_id": None, "provider": "baseten", "usd": 9.0},  # no session: an eval call, not a session
    ]
    costs = ev.session_costs(rows)
    assert [(r["session"], r["provider"]) for r in costs] == [("s01", "baseten"), ("s02", "fireworks")]
    assert costs[0]["partner_usd"] == pytest.approx(0.06) and costs[0]["meta_usd"] == pytest.approx(0.005)
    c = by_id(ev.cost_claims(costs))
    assert c["partner.baseten.sessions"]["value"] == 1
    assert c["partner.baseten.usd_per_session"]["value"] == pytest.approx(0.065)
    assert c["partner.modal.usd"]["value"] is None and c["partner.modal.sessions"]["n"] == 0
    assert set(costs[0]) == {"session", "provider", "partner_usd", "meta_usd", "calls"}  # no session id kept


async def test_split_sends_only_the_partner_roles_to_the_partner() -> None:
    class C:
        def __init__(self, name: str, roles: tuple[str, ...] = ()) -> None:
            self.name = name
            self.config = type("Cfg", (), {"partner_roles": roles})()

        async def respond(self, role: str, input: Any, **kw: Any) -> str:
            return f"{self.name}:{role}"

        async def sam_image(self, *a: Any, **kw: Any) -> str:
            return f"{self.name}:sam"

    s = ev.Split(C("meta"), C("baseten", ("guide", "judge")))
    assert await s.respond("judge", "x") == "baseten:judge"
    assert await s.respond("locate", "x") == "meta:locate"
    assert await s.sam_image(b"") == "meta:sam"


# ---- reasoning effort: like for like with Muse Spark, low only if refused ---------------------------------------


class Refusing:
    """A partner client that refuses the role's effort (as a 400 would arrive through calls/) until built at low."""

    def __init__(self, effort: str, refuse: bool = True) -> None:
        self.effort, self.refuse, self.calls = effort, refuse, 0
        self.config = type("Cfg", (), {"partner_roles": ("judge",)})()

    async def respond(self, role: str, input: Any, **kw: Any) -> str:
        self.calls += 1
        if self.refuse and self.effort == "minimal":
            raise calls.CallFailed('judge: error: {"code": "http_400", "message": "reasoning_effort minimal is not '
                                   'supported"}')
        return f"{self.effort}:{role}"


async def test_minimal_refused_falls_back_to_low_once_and_says_so() -> None:
    built: list[Refusing] = []

    def low() -> Refusing:
        built.append(Refusing("low"))
        return built[-1]

    s = ev.Split(Refusing("minimal", refuse=False), Refusing("minimal"), low)
    assert await s.respond("judge", "x") == "low:judge"
    assert await s.respond("judge", "y") == "low:judge"
    assert len(built) == 1  # rebuilt once, not per call
    note = s.run_note("minimal")
    assert note["reasoning_effort"] == "low" and note["fell_back"] is True and "http_400" in note["refusal"]


async def test_minimal_accepted_stays_minimal() -> None:
    s = ev.Split(Refusing("minimal", refuse=False), Refusing("minimal", refuse=False), lambda: pytest.fail("rebuilt"))
    assert await s.respond("judge", "x") == "minimal:judge"
    assert s.run_note("minimal") == {"reasoning_effort": "minimal", "asked": "minimal", "fell_back": False,
                                     "refusal": None}


async def test_other_failures_are_not_taken_for_a_refused_effort() -> None:
    class Down(Refusing):
        async def respond(self, role: str, input: Any, **kw: Any) -> str:
            raise calls.CallFailed('judge: error: {"code": "http_503", "message": "overloaded"}')

    s = ev.Split(Refusing("minimal", refuse=False), Down("minimal"), lambda: pytest.fail("rebuilt"))
    with pytest.raises(calls.CallFailed):
        await s.respond("judge", "x")
    assert s.refused is None


def test_the_claims_say_which_effort_the_run_used(tmp_path: Path) -> None:
    lab = labels()
    write(tmp_path / "b", preds(lab, false_done=1, missed_done=2))
    write(tmp_path / "p" / "fireworks", preds(lab, false_done=1, missed_done=2))
    (tmp_path / "p" / "fireworks" / "run.json").write_text(json.dumps(
        {"reasoning_effort": "low", "asked": "minimal", "fell_back": True, "refusal": "http_400"}))
    c = by_id(ev.claims(lab, root=tmp_path / "p", base_root=tmp_path / "b"))
    for k in ("gate", "false_done", "latency_p95"):
        assert c[f"partner.fireworks.judge.{k}"]["note"].endswith("at reasoning_effort low: the partner refused "
                                                                   "minimal")


def test_the_judge_asks_partners_for_its_own_effort() -> None:
    cfg = calls.load_config()
    assert cfg.role("judge").effort == "minimal"
    assert all(p.reasoning_effort is None for p in cfg.partners.values())  # none overridden: like for like

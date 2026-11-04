import json
import subprocess
from pathlib import Path

import pytest

from eval.harness import EvalFailure, compare, run_row, write
from eval.rows import Row, load_rows

CLAIM = {"id": "toy.recall", "value": 95.0, "unit": "%", "n": 40, "ci": [83.5, 98.6]}
PRINT = "python -c \"import json,os; print('noise'); print(json.dumps(%s)); print(os.environ['FIXIT_CALLS'])\""


def _row(tmp: Path, fixtures: list[str]) -> Row:
    return Row(id="toy", owner="l6", command=PRINT % repr(CLAIM), fixtures=fixtures, claims=["toy.recall"])


def test_missing_fixture_fails_before_running(tmp_path: Path) -> None:
    with pytest.raises(EvalFailure, match="fixture fixtures/l6/none is missing"):
        run_row(_row(tmp_path, ["fixtures/l6/none"]), root=tmp_path)
    (tmp_path / "fixtures/l6/none").mkdir(parents=True)
    with pytest.raises(EvalFailure, match="missing"):  # an empty fixture dir is missing too
        run_row(_row(tmp_path, ["fixtures/l6/none"]), root=tmp_path)


def test_row_runs_in_replay_and_reproduces(tmp_path: Path) -> None:
    (tmp_path / "fx").mkdir()
    (tmp_path / "fx" / "a.jsonl").write_text("{}\n")
    res = run_row(_row(tmp_path, ["fx"]), root=tmp_path)
    assert res.claims == [CLAIM]
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"],
                   cwd=tmp_path, check=True)
    claims = write([res], [], root=tmp_path)
    assert claims[0].rows == ["toy"] and len(claims[0].commit) == 40
    assert compare([res], claims) == []
    drifted = [c.model_copy(update={"value": 94.0}) for c in claims]
    assert "!= measured" in compare([res], drifted)[0]
    assert "no line" in compare([res], [])[0]
    # an unchanged number keeps its first date and commit
    again = write([res], claims, root=tmp_path)
    assert again[0].measured_at == claims[0].measured_at


def test_row_must_print_exactly_its_claims(tmp_path: Path) -> None:
    row = Row(id="toy", owner="l6", command=PRINT % repr(CLAIM), fixtures=[], claims=["toy.recall", "toy.other"])
    with pytest.raises(EvalFailure, match="listed but not printed"):
        run_row(row, root=tmp_path)


def test_failing_command_fails(tmp_path: Path) -> None:
    row = Row(id="toy", owner="l6", command="python -c \"raise SystemExit('MissingFixture: x')\"", fixtures=[],
              claims=[])
    with pytest.raises(EvalFailure, match="MissingFixture"):
        run_row(row, root=tmp_path)


def test_repo_rows_and_claims_load() -> None:
    load_rows()
    root = Path(__file__).resolve().parents[2]
    assert isinstance(json.loads((root / "eval" / "claims.json").read_text()), list)


def test_n0_claim_is_unmeasured_never_written_and_does_not_fail(tmp_path: Path) -> None:
    from eval.harness import unmeasured

    zero = {"id": "toy.redteam", "value": 0, "unit": "struck", "n": 0, "ci": [0.0, 100.0]}
    cmd = f"python -c \"import json; print(json.dumps({CLAIM!r})); print(json.dumps({zero!r}))\""
    row = Row(id="toy", owner="l6", command=cmd, fixtures=[], claims=["toy.recall", "toy.redteam"],
              unmeasured="needs the red-team sessions")
    res = run_row(row, root=tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x"],
                   cwd=tmp_path, check=True)
    claims = write([res], [], root=tmp_path)
    assert [c.id for c in claims] == ["toy.recall"]  # 0 of 0 is never stored
    assert compare([res], claims) == []  # and does not fail
    assert unmeasured([res], claims) == ["toy: not measured in this build (1 claim) — needs the red-team sessions"]
    # a number that was measured must not quietly become unknown
    was = claims + [claims[0].model_copy(update={"id": "toy.redteam", "value": 3.0, "n": 40})]
    assert "now prints n=0" in compare([res], was)[0]


def test_unmeasured_needs_carry_no_names_or_dates() -> None:
    import re

    from eval.rows import load_rows

    dated = re.compile(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b|\d")
    assert not [(r.id, r.unmeasured) for r in load_rows() if dated.search(r.unmeasured)]


def test_a_timing_row_reproduces_under_its_budget_not_exactly() -> None:
    from contracts.models.claim import Claim
    from eval.harness import RowResult

    line = {"id": "toy.p95", "value": 0.39, "unit": "ms per sentence", "n": 2500, "ci": None}
    claims = [Claim.model_validate({**line, "measured_at": "2026-10-27T00:00:00Z", "commit": "abc1234",
                                    "command": "x", "rows": ["toy"], "readme_anchor": None})]
    row = Row(id="toy", owner="l4", command="x", fixtures=[], claims=["toy.p95"], timing=True, budget=20.0)
    assert compare([RowResult(row, [{**line, "value": 0.78}])], claims) == []  # a loaded box is slower, not wrong
    assert compare([RowResult(row, [{**line, "value": 21.0}])], claims)  # over the budget fails
    assert compare([RowResult(row, [{**line, "value": 0.5, "n": 100}])], claims)  # a different n fails
    exact = Row(id="toy", owner="l4", command="x", fixtures=[], claims=["toy.p95"])
    assert compare([RowResult(exact, [{**line, "value": 0.40}])], claims)


def test_a_timing_row_needs_a_budget(tmp_path: Path) -> None:
    f = tmp_path / "rows.toml"
    f.write_text('[[row]]\nid = "t"\nowner = "l4"\ncommand = "x"\nfixtures = []\nclaims = ["t.p95"]\ntiming = true\n')
    with pytest.raises(ValueError, match="budget"):
        load_rows(f)


def test_write_notes_the_machine_a_timing_ran_on(monkeypatch: pytest.MonkeyPatch) -> None:
    from eval.harness import RowResult, write

    monkeypatch.setattr("eval.harness.head_commit", lambda root=None: "abc1234")
    monkeypatch.setattr("eval.harness.machine", lambda: "AMD Ryzen 9 7950X")
    row = Row(id="toy", owner="l4", command="x", fixtures=[], claims=["toy.p95"], timing=True, budget=20.0)
    line = {"id": "toy.p95", "value": 0.4, "unit": "ms", "n": 10, "ci": None, "note": "p95"}
    [c] = write([RowResult(row, [line])], [])
    assert c.note == "p95; measured on AMD Ryzen 9 7950X"


def test_a_full_write_keeps_a_timing_under_budget_and_a_named_row_remeasures(monkeypatch: pytest.MonkeyPatch) -> None:
    from eval.harness import RowResult, write

    monkeypatch.setattr("eval.harness.head_commit", lambda root=None: "abc1234")
    row = Row(id="toy", owner="l4", command="x", fixtures=[], claims=["toy.p95"], timing=True, budget=20.0)
    [old] = write([RowResult(row, [{"id": "toy.p95", "value": 0.4, "unit": "ms", "n": 10, "ci": None}])], [])
    again = [RowResult(row, [{"id": "toy.p95", "value": 0.5, "unit": "ms", "n": 10, "ci": None}])]
    assert write(again, [old])[0].value == 0.4
    assert write(again, [old], rewrite_timing=True)[0].value == 0.5
    over = [RowResult(row, [{"id": "toy.p95", "value": 25.0, "unit": "ms", "n": 10, "ci": None}])]
    assert write(over, [old])[0].value == 25.0


def test_a_row_printing_a_percent_ci_on_a_count_fails(tmp_path: Path) -> None:
    from eval.harness import EvalFailure, run_row

    (tmp_path / "row.py").write_text('print(\'{"id": "t.k", "value": 21, "unit": "of n", "n": 300, '
                                     '"ci": [4.6, 10.5]}\')\n')
    row = Row(id="t", owner="l6", command="python row.py", fixtures=[], claims=["t.k"])
    with pytest.raises(EvalFailure, match="outside its ci"):
        run_row(row, root=tmp_path)


def test_unchanged_numbers_keep_their_date_but_take_a_reworded_note(monkeypatch: pytest.MonkeyPatch) -> None:
    from eval.harness import RowResult, write

    monkeypatch.setattr("eval.harness.head_commit", lambda root=None: "abc1234")
    row = Row(id="toy", owner="l6", command="x", fixtures=[], claims=["toy.k"])
    line = {"id": "toy.k", "value": 3, "unit": "of n", "n": 9, "ci": None, "note": "old words"}
    [old] = write([RowResult(row, [line])], [])
    monkeypatch.setattr("eval.harness.head_commit", lambda root=None: "def5678")
    [new] = write([RowResult(row, [{**line, "note": "new words"}])], [old])
    assert (new.note, new.commit, new.measured_at) == ("new words", "abc1234", old.measured_at)

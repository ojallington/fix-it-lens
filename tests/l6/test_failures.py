import json
import subprocess
from pathlib import Path

from eval.claims import SQUASHED
from eval.failures import Failure, check, collect, load_writeups, render
from eval.stepcheck.results import ClauseFinding, Label, Prediction, write_predictions

ROOT = Path(__file__).resolve().parents[2]
HEAD = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def _setup(tmp: Path) -> tuple[tuple[str, Path, Path], ...]:
    labels = [Label("a.nd.t1", "a", "dishwasher", "not_done", 1, True),
              Label("b.nd.t1", "b", "dishwasher", "not_done", 1, False),
              Label("c.d.t1", "c", "dishwasher", "done", 1, False)]
    lp = tmp / "labels.jsonl"
    lp.write_text("".join(json.dumps(lab.__dict__) + "\n" for lab in labels))
    preds = [Prediction("a.nd.t1", "judge_sam", 1, "done", "done", "done",
                        [ClauseFinding("c1", True, "satisfied", "filter lifted")]),
             Prediction("b.nd.t1", "judge_sam", 1, "not_done"), Prediction("c.d.t1", "judge_sam", 1, "done"),
             Prediction("b.nd.t1", "judge_sam", 2, "done")]  # run 2 is not the published pass
    write_predictions("judge_sam", preds, tmp / "res")
    safe = [{"id": "s7", "lang": "en", "text": "Rinse the filter.", "struck": True, "rule": "r1"},
            {"id": "s8", "struck": False}]
    (tmp / "safe.jsonl").write_text("".join(json.dumps(r) + "\n" for r in safe))
    return (("main", lp, tmp / "res"),)


def test_collect_finds_run1_false_done_and_strikes(tmp_path: Path) -> None:
    fs = collect(_setup(tmp_path), tmp_path / "safe.jsonl")
    assert [(f.kind, f.ref, f.guarded) for f in fs] == [("false_done", "a.nd.t1", True), ("false_strike", "s7", False)]
    assert "filter lifted" in fs[0].detail


def test_check_needs_a_writeup_per_failure_and_none_spare(tmp_path: Path) -> None:
    fs = collect(_setup(tmp_path), tmp_path / "safe.jsonl")
    p = tmp_path / "failures.toml"
    p.write_text(f"""
[[failure]]
kind = "false_done"
ref = "a.nd.t1"
root_cause = "glare"
change = "crop"
rerun = "not done on re-run"
rerun_commit = "{HEAD}"

[[failure]]
kind = "false_strike"
ref = "s99"
root_cause = "x"
change = "y"
rerun = "z"
rerun_commit = "{HEAD}"
""")
    errs = check(fs, load_writeups(p))
    assert any("s7: no write-up" in e for e in errs)
    assert any("s99: written up, but" in e for e in errs)
    assert not any("a.nd.t1" in e for e in errs)
    md = render(fs, load_writeups(p))
    assert "**Root cause.** glare" in md and "(a stop guards this step)" in md and "**Not yet written up.**" in md


def test_rerun_commit_must_be_in_history() -> None:
    w = {("false_done", "x"): {"rerun_commit": "0" * 40}}
    assert "not in HEAD's history" in check([Failure("false_done", "x", "")], w)[0]


def test_a_rerun_commit_passes_if_listed_and_an_invented_one_never(tmp_path: Path) -> None:
    (tmp_path / "eval").mkdir()
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / SQUASHED).write_text("# the cut\n" + "a1b2c3d4" * 5 + "\n")
    f = [Failure("false_done", "x", "")]
    assert check(f, {("false_done", "x"): {"rerun_commit": "a1b2c3d"}}, tmp_path) == []
    for invented in ("d4c3b2a", "a1b2c3", ""):
        assert "not in HEAD's history" in check(f, {("false_done", "x"): {"rerun_commit": invented}}, tmp_path)[0]


def test_repo_writeups_cover_recorded_failures() -> None:
    assert check(collect(), load_writeups()) == []


def test_fixed_before_run_needs_no_current_failure_and_is_listed_apart(tmp_path: Path) -> None:
    fs = collect(_setup(tmp_path), tmp_path / "safe.jsonl")
    p = tmp_path / "failures.toml"
    body = 'root_cause = "r"\nchange = "c"\nrerun = "z"\nrerun_commit = "' + HEAD + '"\nstatus = "fixed_before_run"\n'
    p.write_text(f'[[failure]]\nkind = "false_strike"\nref = "s1"\n{body}\n'
                 f'[[failure]]\nkind = "false_strike"\nref = "s7"\n{body}')
    errs = check(fs, load_writeups(p))
    assert any("s7: marked fixed before the run, but" in e for e in errs)  # still failing: not fixed
    assert not any("s1" in e for e in errs)
    assert "fixed before the published run (2)" in render(fs, load_writeups(p))

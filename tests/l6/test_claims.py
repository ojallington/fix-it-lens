import json
import re
import subprocess
from pathlib import Path

import pytest

from contracts.models.claim import Claim
from eval.claims import ROOT, SQUASHED, check, dump_claims, load_claims, scan_text, squashed_commits


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, check=True).stdout.strip()


def _claim(cid: str, commit: str, **kw: object) -> Claim:
    base = {"id": cid, "value": 90.0, "unit": "%", "n": 120, "ci": None, "measured_at": "2026-10-30T12:00:00+00:00",
            "commit": commit, "command": "python -m eval.stepcheck score", "rows": ["stepcheck"],
            "readme_anchor": "#eval"}
    return Claim.model_validate({**base, **kw})


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "x")
    (tmp_path / "eval").mkdir()
    (tmp_path / "eval" / "rows.toml").write_text(
        '[[row]]\nid = "stepcheck"\nowner = "l6"\ncommand = "true"\nfixtures = []\nclaims = []\n')
    (tmp_path / "eval" / "claims.toml").write_text(
        '[scan]\nfiles = ["README.md", "docs/video/*.srt"]\n[[allow]]\ntext = "95% CI"\nreason = "level"\n')
    return tmp_path


def _write(repo: Path, readme: str, claims: list[Claim]) -> None:
    (repo / "README.md").write_text(readme)
    dump_claims(claims, repo / "eval" / "claims.json")


def test_marked_numbers_pass(repo: Path) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    c = _claim("stepcheck.false_done", head, value=2, unit="of n", n=40, ci=[0.6, 6.6])
    b = _claim("stepcheck.bacc", head)
    _write(repo, "Balanced accuracy 90%<!--c:stepcheck.bacc-->; false done 2<!--c:stepcheck.false_done--> of "
                 "40<!--c:stepcheck.false_done:n--> (Wilson 95% CI 0.6<!--c:stepcheck.false_done:ci0-->–6.6"
                 "<!--c:stepcheck.false_done:ci1-->).\n\n```\nmake eval  # 123 in code is not prose\n```\n"
                 "`v1.2` and [link](https://x.invalid/3) are skipped.\n", [c, b])
    assert check(repo) == []


def test_planted_number_without_a_line_fails(repo: Path) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    _write(repo, "Balanced accuracy 90%<!--c:stepcheck.bacc-->, done recall 97%.\n", [_claim("stepcheck.bacc", head)])
    problems = check(repo)
    assert len(problems) == 1 and "97" in str(problems[0]) and "README.md:1" in str(problems[0])


def test_marked_number_that_drifted_fails(repo: Path) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    _write(repo, "Balanced accuracy 92%<!--c:stepcheck.bacc-->\n", [_claim("stepcheck.bacc", head)])
    assert "does not match" in str(check(repo)[0])


def test_marker_naming_a_missing_claim_fails(repo: Path) -> None:
    _write(repo, "Recall 90%<!--c:nope-->\n", [])
    assert "no line in claims.json" in str(check(repo)[0])


def test_commit_not_in_history_fails(repo: Path) -> None:
    _write(repo, "Balanced accuracy 90%<!--c:stepcheck.bacc-->\n", [_claim("stepcheck.bacc", "deadbeef" * 5)])
    problems = check(repo)
    assert len(problems) == 1 and "not in HEAD's history" in str(problems[0])


def test_a_listed_squashed_commit_passes_and_an_invented_one_still_fails(repo: Path) -> None:
    listed, invented = "a1b2c3d4" * 5, "d4c3b2a1" * 5
    (repo / SQUASHED).write_text(f"# the cut\n{listed}\n")
    for commit in (listed, listed[:7]):
        _write(repo, "Balanced accuracy 90%<!--c:stepcheck.bacc-->\n", [_claim("stepcheck.bacc", commit)])
        assert check(repo) == []
    for commit in (invented, invented[:7], listed[:39] + "0"):
        _write(repo, "Balanced accuracy 90%<!--c:stepcheck.bacc-->\n", [_claim("stepcheck.bacc", commit)])
        assert "not in HEAD's history" in str(check(repo)[0]), commit


def test_the_squashed_list_is_full_shas_and_real_where_the_history_is_here() -> None:
    listed = squashed_commits()
    assert listed and all(re.fullmatch("[0-9a-f]{40}", s) for s in listed)
    for sha in listed:
        if subprocess.run(["git", "cat-file", "-e", sha], cwd=ROOT, capture_output=True).returncode == 0:
            assert subprocess.run(["git", "merge-base", "--is-ancestor", sha, "HEAD"], cwd=ROOT).returncode == 0, sha


def test_unknown_row_fails(repo: Path) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    _write(repo, "", [_claim("x", head, rows=["ghost"])])
    assert "ghost" in str(check(repo)[0])


def test_caption_numbers_match_by_value(repo: Path) -> None:
    head = _git(repo, "rev-parse", "HEAD")
    _write(repo, "", [_claim("stepcheck.false_done", head, value=2, unit="of n", n=40, ci=[0.6, 6.6])])
    srt = repo / "docs" / "video"
    srt.mkdir(parents=True)
    (srt / "en.srt").write_text("1\n00:02:30,000 --> 00:02:33,500\nIt said done on 2 of 40 not-done steps.\n")
    assert check(repo) == []
    (srt / "en.srt").write_text("1\n00:02:30,000 --> 00:02:33,500\nIt said done on 3 of 40 not-done steps.\n")
    problems = check(repo)
    assert len(problems) == 1 and "3 has no line" in str(problems[0])


def test_scan_ignores_identifiers_and_versions() -> None:
    text = "Apache-2.0, CC-BY-4.0, muse-spark-1.3, call 0800-9009, sam-3.1, #12, <img width=\"300\">\n"
    assert scan_text("x.md", text, {}, [], caption=False) == []


def test_claims_file_roundtrip_one_line_each(tmp_path: Path) -> None:
    p = tmp_path / "claims.json"
    dump_claims([_claim("b", "abc1234"), _claim("a", "abc1234")], p)
    lines = p.read_text().splitlines()
    assert lines[0] == "[" and lines[-1] == "]" and len(lines) == 4
    assert json.loads(lines[1].rstrip(","))["id"] == "a"
    assert [c.id for c in load_claims(p)] == ["a", "b"]


def _timing_repo(repo: Path) -> Claim:
    (repo / "eval" / "rows.toml").write_text(
        '[[row]]\nid = "lat"\nowner = "l4"\ncommand = "true"\nfixtures = []\nclaims = ["lat.p95"]\ntiming = true\n'
        "budget = 20.0\n")
    return _claim("lat.p95", _git(repo, "rev-parse", "HEAD"), value=0.39, unit="ms per sentence", n=2500,
                  rows=["lat"], note="p95; measured on AMD Ryzen 9 7950X", measured_at="2026-10-27T01:13:25Z")


def test_a_timing_value_is_shown_with_its_budget_never_bare(repo: Path) -> None:
    from eval.claims import timing_wording

    c = _timing_repo(repo)
    assert timing_wording(c, 20.0) == "under 20 ms per sentence, measured 0.39 ms on AMD Ryzen 9 7950X, 2026-10-27"
    _write(repo, f"G0 checks each sentence {timing_wording(c, 20.0, marked=True)}.\n", [c])
    assert check(repo) == []
    _write(repo, "G0 checks each sentence in 0.39<!--c:lat.p95--> ms.\n", [c])
    [p] = check(repo)
    assert "holds to its budget" in str(p) and "under 20 ms per sentence" in str(p)
    _write(repo, "G0 checks each sentence in under 25<!--c:lat.p95:budget--> ms.\n", [c])
    assert "does not match" in str(check(repo)[0])


def test_a_caption_timing_needs_the_budget_on_its_line(repo: Path) -> None:
    c = _timing_repo(repo)
    _write(repo, "", [c])
    srt = repo / "docs" / "video"
    srt.mkdir(parents=True)
    (srt / "en.srt").write_text("1\n00:01:00,000 --> 00:01:03,000\nUnder 20 ms a sentence; we measured 0.39 ms.\n")
    assert check(repo) == []
    (srt / "en.srt").write_text("1\n00:01:00,000 --> 00:01:03,000\nEach sentence takes 0.39 ms.\n")
    assert "timing value" in str(check(repo)[0])


def test_a_timing_claim_names_its_machine(repo: Path) -> None:
    c = _timing_repo(repo)
    _write(repo, "", [c.model_copy(update={"note": "p95"})])
    assert "measured on <machine>" in str(check(repo)[0])


def test_value_and_ci_share_the_claims_unit(repo: Path) -> None:
    from eval.claims import ci_problem
    from eval.stats import wilson_count

    assert "outside its ci" in str(ci_problem({"value": 21, "unit": "of 300", "n": 300, "ci": [4.6, 10.5]}))
    assert "in percent" in str(ci_problem({"value": 2, "unit": "clips", "n": 40, "ci": [1.4, 16.5]}))
    assert ci_problem({"value": 2, "unit": "clips", "n": 40, "ci": list(wilson_count(2, 40))}) is None
    assert ci_problem({"value": 7.0, "unit": "%", "n": 300, "ci": [4.6, 10.5]}) is None
    assert ci_problem({"value": 69, "unit": "of 100", "n": 100, "ci": list(wilson_count(69, 100))}) is None
    head = _git(repo, "rev-parse", "HEAD")
    _write(repo, "", [_claim("stepcheck.fd", head, value=21, unit="of n", n=300, ci=[4.6, 10.5])])
    assert "outside its ci" in str(check(repo)[0])


def test_wilson_count_holds_its_count() -> None:
    from eval.stats import wilson_count

    for k, n in ((0, 40), (2, 40), (21, 300), (40, 40)):
        lo, hi = wilson_count(k, n)
        assert 0 <= lo <= k <= hi <= n

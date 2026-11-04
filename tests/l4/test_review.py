"""The two-approval check on safety changes to the country files."""

from __future__ import annotations

import ast
import copy
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from rules import review
from rules.country import country_path
from rules.review import approvals, reviewers, safety_changes

ROOT = Path(__file__).resolve().parents[2]

NL: dict[str, Any] = yaml.safe_load(country_path("nl").read_text())


def _edit(rule_id: str, **kw: Any) -> dict[str, Any]:
    d = copy.deepcopy(NL)
    r = next(x for x in d["rules"] if x["id"] == rule_id)
    r.update(kw)
    return d


def test_no_change_needs_nothing() -> None:
    assert safety_changes(NL, copy.deepcopy(NL), "nl.yaml") == []


def test_gas_and_mains_changes_need_approvals() -> None:
    assert safety_changes(NL, _edit("nl.gas.tap", line={"nl": "x", "en": "y"}), "nl.yaml")
    assert safety_changes(NL, _edit("nl.mains.casing", ring_cm=3), "nl.yaml")
    d = copy.deepcopy(NL)
    d["rules"] = [r for r in d["rules"] if r["id"] != "nl.mains.wiring"]
    assert any("removed" in c for c in safety_changes(NL, d, "nl.yaml"))


def test_allow_list_emergency_languages_and_new_files() -> None:
    for key, val in (("emergency", {"en": "x"}), ("languages", ["nl"]), ("allow_lists", {"sealed_boiler": []})):
        d = copy.deepcopy(NL)
        d[key] = val
        assert safety_changes(NL, d, "nl.yaml") == [f"nl.yaml: {key} changed"]
    assert safety_changes(None, NL, "rules/fr.yaml") == ["rules/fr.yaml: new country file"]
    assert safety_changes(NL, None, "rules/nl.yaml") == ["rules/nl.yaml: country file removed"]


def test_other_families_may_only_grow_holds() -> None:
    hot = next(r for r in NL["rules"] if r["id"] == "nl.heat.hot_water")
    grown = {**hot["parts"], "en": [*hot["parts"]["en"], "boiling water"]}
    assert safety_changes(NL, _edit("nl.heat.hot_water", parts=grown), "nl.yaml") == []
    assert safety_changes(NL, _edit("nl.heat.hot_water", script={"nl": "a", "en": "b"}), "nl.yaml") == []
    narrowed = {**hot["parts"], "en": hot["parts"]["en"][1:]}
    assert safety_changes(NL, _edit("nl.heat.hot_water", parts=narrowed), "nl.yaml")
    assert safety_changes(NL, _edit("nl.heat.hot_water", action="ALLOW"), "nl.yaml")
    assert safety_changes(NL, _edit("nl.water.flood", look_only_exemptions=["0" * 64]), "nl.yaml")


def test_a_new_allow_rule_needs_approvals_but_a_new_stop_does_not() -> None:
    base = next(r for r in NL["rules"] if r["id"] == "nl.heat.hot_water")
    for action, needs in (("STOP", False), ("ALLOW", True)):
        d = copy.deepcopy(NL)
        d["rules"].append({**copy.deepcopy(base), "id": "nl.heat.steam", "zone": "steam", "action": action})
        assert bool(safety_changes(NL, d, "nl.yaml")) is needs


def test_reviewers_come_from_codeowners() -> None:
    text = "*  @a\n/rules/*.yaml  @Ana @Bea  # safety-reviewers\n/rules/g1*  @c\n"
    assert reviewers(text) == {"ana", "bea"}


def test_author_and_outsiders_do_not_count_and_the_latest_review_wins() -> None:
    team = {"ana", "bea", "cy"}
    reviews = [
        {"user": {"login": "ana"}, "state": "APPROVED"},  # the author
        {"user": {"login": "bea"}, "state": "APPROVED"},
        {"user": {"login": "zed"}, "state": "APPROVED"},  # not on the team
        {"user": {"login": "cy"}, "state": "APPROVED"},
        {"user": {"login": "cy"}, "state": "CHANGES_REQUESTED"},
        {"user": {"login": "bea"}, "state": "COMMENTED"},  # a comment does not undo an approval
    ]
    assert approvals(reviews, "Ana", team) == {"bea"}


def _rules_imports(path: Path) -> set[str]:
    """The rules/ modules this file imports, as repo paths."""
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "rules":
            names = [node.module] if node.module != "rules" else [f"rules.{a.name}" for a in node.names]
            out.update(names)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names if a.name.startswith("rules."))
    return {f"{m.replace('.', '/')}.py" for m in out if (ROOT / f"{m.replace('.', '/')}.py").exists()}


def test_guarded_code_covers_everything_g0_the_step_check_and_the_engine_import() -> None:
    seen: set[str] = set()
    todo = ["rules/g0.py", "rules/steps.py", "rules/engine.py"]
    while todo:
        p = todo.pop()
        if p not in seen:
            seen.add(p)
            todo += sorted(_rules_imports(ROOT / p))
    assert seen <= set(review.GUARDED_CODE), sorted(seen - set(review.GUARDED_CODE))


def test_every_guarded_module_is_owned_and_triggers_the_workflow() -> None:
    owners = (ROOT / ".github/CODEOWNERS").read_text().split()
    workflow = (ROOT / ".github/workflows/safety-review.yml").read_text()
    for path in review.GUARDED_CODE:
        assert f"/{path}" in owners, path
        assert f'"{path}"' in workflow, path


def _git(repo: Path, *args: str) -> None:
    who = ["-c", "user.email=t@t", "-c", "user.name=t", "-c", "core.hooksPath=/dev/null"]
    subprocess.run(["git", *who, *args], cwd=repo, check=True, capture_output=True)


def test_a_pull_request_cannot_name_its_own_reviewers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run from the base with --tree on the PR: the PR's CODEOWNERS and its friends' approvals count for nothing."""
    (tmp_path / "rules").mkdir()
    (tmp_path / ".github").mkdir()
    (tmp_path / "rules/g0.py").write_text("STRICT = True\n")
    (tmp_path / ".github/CODEOWNERS").write_text("/rules/*.yaml  @someone\n")
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    _git(tmp_path, "checkout", "-q", "-b", "pr")
    (tmp_path / "rules/g0.py").write_text("STRICT = False\n")
    (tmp_path / ".github/CODEOWNERS").write_text("/rules/*.yaml  @friend-a @friend-b\n")
    _git(tmp_path, "commit", "-q", "-am", "weaken the filter")

    def github(path: str) -> Any:
        if path.endswith("/reviews?per_page=100"):
            return [{"user": {"login": who}, "state": "APPROVED"} for who in ("friend-a", "friend-b")]
        return {"user": {"login": "author"}}

    monkeypatch.setattr(review, "_github", github)
    monkeypatch.setattr(review, "TREE", review.TREE)  # main() sets it: put it back after the test
    assert review.main(["--tree", str(tmp_path), "--base", "main", "--pr", "1", "--repo", "x/y"]) == 1
    assert review.all_changes("main") == ["rules/g0.py: safety code changed"]


def test_the_workflow_runs_the_base_branchs_check_on_the_pull_requests_tree() -> None:
    flow = yaml.safe_load((ROOT / ".github/workflows/safety-review.yml").read_text())
    events = flow[True]  # yaml reads the key `on` as true
    assert "pull_request_target" in events and "pull_request" not in events
    steps = flow["jobs"]["two-approvals"]["steps"]
    checkouts = {s["with"]["path"]: s["with"]["ref"] for s in steps if "checkout" in s.get("uses", "")}
    assert checkouts["base"] == "${{ github.event.pull_request.base.sha }}" and "/head" in checkouts["pr"]
    run = next(s for s in steps if "rules.review" in s.get("run", ""))
    assert run["working-directory"] == "base" and "--tree ../pr" in run["run"] and "${{" not in run["run"]


def test_every_action_is_pinned_by_sha() -> None:
    for name in ("ci.yml", "safety-review.yml"):
        flow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
        uses = [s["uses"] for job in flow["jobs"].values() for s in job["steps"] if "uses" in s]
        assert uses and all(re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", u) for u in uses), uses

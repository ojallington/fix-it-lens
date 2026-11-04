"""Two approvals for a safety change: `python -m rules.review --base <ref> [--pr <n>]`.

A change to a country file needs two approvals from the safety reviewers, and its author's own approval does not
count, when it touches any of: a gas or mains rule, the sealed-boiler allow-list, a ring size, a look-only hash, the
emergency text or the answer languages, or a rule's removal. CI runs this on every pull request. With no `--pr` it only
lists the changes that would need the approvals.

A change to the code that reads the files needs the same: G0, the step check and every rules module they import
(`GUARDED_CODE`; a test keeps the list complete). A code change can weaken the filter as surely as a table change.

The team is the handles CODEOWNERS gives `/rules/*.yaml`. While that is one person, no safety change can pass this
check: that is the point.

CI runs this file from the base branch's checkout and points `--tree` at the pull request's, so a pull request cannot
change the check or its reviewers: the code and CODEOWNERS are the base's, only the diff and the tables are the PR's.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
TREE = ROOT  # the checkout under review; --tree sets it
NEEDED = 2
GUARDED_FAMILIES = frozenset({"gas", "mains"})
# G0, the step check, the safety engine, the rules modules they import (test_review checks the closure), and this check.
GUARDED_CODE = ("rules/g0.py", "rules/reading.py", "rules/compose.py", "rules/steps.py", "rules/engine.py",
                "rules/text.py", "rules/country.py",
                "rules/disclaimer.py", "rules/review.py")


def safety_changes(old: dict[str, Any] | None, new: dict[str, Any] | None, name: str) -> list[str]:
    """What in this file change needs two approvals. Parsed YAML in, one line per change out."""
    if old == new:
        return []
    if old is None:
        return [f"{name}: new country file"]
    if new is None:
        return [f"{name}: country file removed"]
    out: list[str] = []
    for key in ("languages", "emergency", "allow_lists"):
        if old.get(key) != new.get(key):
            out.append(f"{name}: {key} changed")
    before = {r["id"]: r for r in old.get("rules", [])}
    after = {r["id"]: r for r in new.get("rules", [])}
    for rid in sorted(before.keys() - after.keys()):
        out.append(f"{name}: rule {rid} removed")
    for rid in sorted(after):
        a, b = before.get(rid), after[rid]
        if a == b:
            continue
        if b.get("family") in GUARDED_FAMILIES or (a and a.get("family") in GUARDED_FAMILIES):
            out.append(f"{name}: {b.get('family')} rule {rid} {'changed' if a else 'added'}")
        elif a is None:
            if b.get("action") == "ALLOW" or b.get("look_only_exemptions"):
                out.append(f"{name}: {rid} added, and it lets text through")
        else:
            out += [f"{name}: {rid} {why}" for why in _weakens(a, b)]
    return out


def _weakens(a: dict[str, Any], b: dict[str, Any]) -> list[str]:
    """Outside gas and mains, a rule may gain part names and verbs, and its wording and sources may change, without
    two approvals. Anything else can let text through or shrink a zone."""
    out = [f"{k} changed" for k in ("action", "ring_cm", "look_only_exemptions", "landlord", "family", "zone")
           if a.get(k) != b.get(k)]
    for k in ("parts", "verbs"):
        old, new = a.get(k) or {}, b.get(k) or {}
        removed = any(not set(old[lang]) <= set(new.get(lang, [])) for lang in old)
        if removed or (b.get("action") == "ALLOW" and old != new):
            out.append(f"{k} narrowed" if removed else f"{k} of an ALLOW rule widened")
    return out


def reviewers(codeowners: str) -> set[str]:
    """The handles CODEOWNERS gives the country files (the `/rules/*.yaml` line)."""
    for line in codeowners.splitlines():
        parts = line.split("#", 1)[0].split()
        if parts and parts[0] == "/rules/*.yaml":
            return {h.lstrip("@").lower() for h in parts[1:]}
    return set()


def approvals(reviews: list[dict[str, Any]], author: str, team: set[str]) -> set[str]:
    """Team members whose latest review is an approval, the PR's author excepted."""
    latest: dict[str, str] = {}
    for r in reviews:  # GitHub lists reviews oldest first
        login = str(r.get("user", {}).get("login", "")).lower()
        if r.get("state") in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
            latest[login] = str(r["state"])
    return {u for u, s in latest.items() if s == "APPROVED" and u in team and u != author.lower()}


def _git_yaml(ref: str, path: str) -> dict[str, Any] | None:
    r = subprocess.run(["git", "show", f"{ref}:{path}"], cwd=TREE, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    data = yaml.safe_load(r.stdout)
    return data if isinstance(data, dict) else None


def _changed_files(base: str) -> list[str]:
    r = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD", "--", "rules/"], cwd=TREE,
                       capture_output=True, text=True, check=True)
    return [p for p in r.stdout.split() if p.endswith(".yaml") and len(Path(p).stem) == 2]


def _changed_code(base: str) -> list[str]:
    r = subprocess.run(["git", "diff", "--name-only", f"{base}...HEAD", "--", *GUARDED_CODE], cwd=TREE,
                       capture_output=True, text=True, check=True)
    return r.stdout.split()


def all_changes(base: str) -> list[str]:
    out: list[str] = [f"{path}: safety code changed" for path in _changed_code(base)]
    for path in _changed_files(base):
        cur = TREE / path
        new = yaml.safe_load(cur.read_text()) if cur.exists() else None
        out += safety_changes(_git_yaml(base, path), new, path)
    return out


def _github(path: str) -> Any:
    req = urllib.request.Request(f"https://api.github.com{path}", headers={
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
    })
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (fixed https host)
        return json.load(resp)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m rules.review")
    ap.add_argument("--base", required=True, help="the PR's base ref, e.g. origin/main")
    ap.add_argument("--pr", type=int, help="PR number; reads its reviews from the GitHub API (GITHUB_TOKEN)")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    ap.add_argument("--tree", type=Path, default=ROOT, help="the checkout under review; default: this one")
    args = ap.parse_args(argv)

    global TREE
    TREE = args.tree.resolve()
    changes = all_changes(args.base)
    for c in changes:
        print(f"safety change: {c}")
    if not changes:
        print("no safety change in the country files")
        return 0
    if args.pr is None:
        print(f"needs {NEEDED} approvals from the safety-reviewers team")
        return 0
    team = reviewers((ROOT / ".github" / "CODEOWNERS").read_text())
    pr = _github(f"/repos/{args.repo}/pulls/{args.pr}")
    reviews = _github(f"/repos/{args.repo}/pulls/{args.pr}/reviews?per_page=100")
    got = approvals(reviews, pr["user"]["login"], team)
    print(f"safety-reviewers: {sorted(team)}; approvals counted: {sorted(got)} (author excluded)")
    if len(got) < NEEDED:
        print(f"FAIL: a safety change needs {NEEDED} approvals from the safety-reviewers team; it has {len(got)}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

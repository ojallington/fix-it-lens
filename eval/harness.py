"""`make eval` and `make eval-live`: run every row in eval/rows.toml and hold its numbers against eval/claims.json.

`make eval` runs each row's command in replay mode (FIXIT_CALLS=replay: recorded responses only, no key, no
network). A row whose fixtures are missing fails before it runs, and a command that hits a missing fixture fails
with it. The printed claims must equal claims.json in value, unit, n and CI; `--write` stores them instead, with
the time and HEAD's commit (refused on a dirty tree, so the commit names the code that made the number).

`make eval-live` runs each row's live_command in record mode, through calls/ and the ledger (lane l6's budget).
It records fresh fixtures; `make eval` then reproduces from them.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from contracts.models.claim import Claim
from eval.claims import CLAIMS, PENDING, ci_problem, dump_claims, dump_pending, load_claims, load_pending
from eval.rows import ROWS, Row, load_rows

ROOT = Path(__file__).resolve().parent.parent
COMPARED = ("value", "unit", "n", "ci")


class EvalFailure(Exception):
    pass


@dataclass(frozen=True)
class RowResult:
    row: Row
    claims: list[dict[str, Any]]


def check_fixtures(row: Row, root: Path = ROOT) -> None:
    for f in row.fixtures:
        p = root / f
        if not p.exists() or (p.is_dir() and not any(p.iterdir())):
            raise EvalFailure(f"row {row.id}: fixture {f} is missing; `make eval` never calls a live model")


def run_row(row: Row, *, live: bool = False, root: Path = ROOT) -> RowResult:
    if live:
        if not row.live_command:
            raise EvalFailure(f"row {row.id}: no live_command")
        command, mode = row.live_command, "record"
    else:
        check_fixtures(row, root)
        command, mode = row.command, "replay"
    env = {**os.environ, "FIXIT_CALLS": mode, "PYTHONHASHSEED": "0"}
    argv = shlex.split(command)
    if argv and argv[0] == "python":
        argv[0] = sys.executable
    proc = subprocess.run(argv, cwd=root, env=env, capture_output=True, text=True)
    if proc.returncode != 0:
        raise EvalFailure(f"row {row.id}: `{command}` exited {proc.returncode}\n{proc.stderr.strip()[-2000:]}")
    out = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        out.append(json.loads(line))
    printed = {c["id"] for c in out}
    exact = {c for c in row.claims if not c.endswith("*")}
    globs = [c for c in row.claims if c.endswith("*")]
    stray = sorted(p for p in printed if p not in exact and not any(fnmatch(p, g) for g in globs))
    lacking = sorted(exact - printed) + [g for g in globs if not any(fnmatch(p, g) for p in printed)]
    for c in out:
        if why := ci_problem(c):
            raise EvalFailure(f"row {row.id}: {c['id']}: {why}")
    if stray or lacking:
        raise EvalFailure(f"row {row.id}: printed ids rows.toml does not list {stray}; listed but not printed "
                          f"{lacking}")
    return RowResult(row, out)


def head_commit(root: Path = ROOT) -> str:
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True,
                          check=True).stdout.strip()


def tree_dirty(root: Path = ROOT) -> bool:
    out = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, capture_output=True,
                         text=True, check=True).stdout
    # claims.json and pending.json are what --write changes; they may be dirty from an earlier write.
    written = ("eval/claims.json", "eval/pending.json")
    return any(not line.endswith(written) for line in out.splitlines() if line.strip())


def _norm(c: dict[str, Any]) -> dict[str, Any]:
    ci = c.get("ci")
    return {"value": c.get("value"), "unit": c.get("unit"), "n": c.get("n"), "ci": list(ci) if ci else None}


def is_unmeasured(c: dict[str, Any]) -> bool:
    return c.get("n") == 0


def unmeasured(results: list[RowResult], claims: list[Claim]) -> list[str]:
    """One line per row whose n=0 claims claims.json has no line for: what the row still needs to be measured."""
    stored = {c.id for c in claims}
    out = []
    for r in results:
        k = sum(1 for c in r.claims if is_unmeasured(c) and c["id"] not in stored)
        if k:
            need = f" — {r.row.unmeasured}" if r.row.unmeasured else ""
            out.append(f"{r.row.id}: not measured in this build ({k} claim{'s' * (k > 1)}){need}")
    return out


def pending(results: list[RowResult], claims: list[Claim], old: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """eval/pending.json after this run: the run rows' n=0 claims that claims.json has no line for, plus the entries
    of rows not run this time (minus any claim measured since)."""
    stored = {c.id for c in claims}
    ran = {r.row.id for r in results}
    out = [p for p in old if not set(p["rows"]) & ran and p["id"] not in stored]
    for r in results:
        for c in r.claims:
            if is_unmeasured(c) and c["id"] not in stored:
                e = {"id": c["id"], "unit": c.get("unit"), "rows": [r.row.id], "needs": r.row.unmeasured}
                out.append({**e, "target": c["target"]} if c.get("target") is not None else e)
    return out


def compare(results: list[RowResult], claims: list[Claim]) -> list[str]:
    stored = {c.id: _norm(c.model_dump(mode="json")) for c in claims}
    diffs = []
    for r in results:
        for c in r.claims:
            want = stored.get(c["id"])
            got = _norm(c)
            if is_unmeasured(c) and want is None:
                continue
            if is_unmeasured(c):
                diffs.append(f"{c['id']}: claims.json has a measured line {want}, but the row now prints n=0")
            elif want is None:
                diffs.append(f"{c['id']}: measured {got}, but claims.json has no line (run with --write)")
            elif r.row.timing and _within_budget(want, got, r.row.budget):
                continue
            elif want != got:
                diffs.append(f"{c['id']}: claims.json {want} != measured {got}")
    return diffs


def _within_budget(want: dict[str, Any], got: dict[str, Any], budget: float | None) -> bool:
    """A wall-clock number never repeats, and a loaded machine is slower: a timing row reproduces while the new value
    is still under the row's budget (the claim the README makes), with the same unit and n. The stored line keeps the
    published value until --write."""
    g = got.get("value")
    if budget is None or not isinstance(g, (int, float)):
        return False
    return want["unit"] == got["unit"] and want["n"] == got["n"] and 0 < g <= budget


def machine() -> str:
    """What a timing was measured on, for the claim's note: the CPU model, never the hostname (it can name a home)."""
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                return re.sub(r"\s+(\d+-Core\s+)?Processor$", "", line.split(":", 1)[1].strip())
    except OSError:
        pass
    return platform.processor() or platform.machine()


def write(results: list[RowResult], claims: list[Claim], root: Path = ROOT, *,
          rewrite_timing: bool = False) -> list[Claim]:
    commit = head_commit(root)
    now = datetime.now(UTC).isoformat(timespec="seconds")
    by_id = {c.id: c for c in claims}
    for r in results:
        for c in r.claims:
            if is_unmeasured(c):
                continue  # 0 of 0 is not a number: never stored
            old = by_id.get(c["id"])
            if old is not None and _norm(old.model_dump(mode="json")) == _norm(c):
                # unchanged numbers keep the date and commit they were first measured at; a reworded note is taken
                if not r.row.timing and c.get("note") != old.note:
                    by_id[c["id"]] = old.model_copy(update={"note": c.get("note")})
                continue
            if (old is not None and r.row.timing and not rewrite_timing
                    and _within_budget(_norm(old.model_dump(mode="json")), _norm(c), r.row.budget)):
                continue  # a timing still under budget keeps its published value; --row <id> re-measures it
            if r.row.timing:  # `make claims` shows a timing with where it was measured
                c = {**c, "note": "; ".join(x for x in (c.get("note"), f"measured on {machine()}") if x)}
            by_id[c["id"]] = Claim.model_validate({
                "readme_anchor": r.row.readme_anchor, **c, "measured_at": now, "commit": commit,
                "command": r.row.command, "rows": [r.row.id],
            })
    return list(by_id.values())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval run")
    ap.add_argument("--live", action="store_true", help="record mode: live models through calls/ and the ledger")
    ap.add_argument("--write", action="store_true", help="store the measured numbers in claims.json")
    ap.add_argument("--row", action="append", help="only these row ids")
    ap.add_argument("--rows", type=Path, default=ROWS)
    ap.add_argument("--claims", type=Path, default=CLAIMS)
    args = ap.parse_args(argv)
    rows = [r for r in load_rows(args.rows) if not args.row or r.id in args.row]
    if args.live:
        rows = [r for r in rows if r.live_command]
    if args.write and tree_dirty():
        print("make eval --write: commit the code first, so each line's commit names the code that measured it")
        return 2
    try:
        results = [run_row(r, live=args.live) for r in rows]
    except EvalFailure as e:
        print(f"make eval: FAIL {e}")
        return 1
    claims = load_claims(args.claims)
    pending_path = args.claims.with_name(PENDING.name)
    if args.write:
        claims = write(results, claims, rewrite_timing=bool(args.row))
        dump_claims(claims, args.claims)
        dump_pending(pending(results, claims, load_pending(pending_path)), pending_path)
        n = sum(1 for r in results for c in r.claims if not is_unmeasured(c))
        print(f"make eval: wrote {n} claims from {len(results)} rows")
        return 0
    diffs = compare(results, claims)
    old = load_pending(pending_path)
    if sorted(map(json.dumps, pending(results, claims, old))) != sorted(map(json.dumps, old)):
        diffs.append("eval/pending.json does not list exactly the registered claims not measured yet "
                     "(run with --write)")
    for d in diffs:
        print(f"make eval: {d}")
    if diffs:
        return 1
    for u in unmeasured(results, claims):
        print(f"make eval: {u}")
    print(f"make eval: {len(results)} rows reproduce claims.json")
    return 0

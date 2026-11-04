"""`make claims`: every number in the README and the video captions has a line in eval/claims.json.

The README marks each measured number with an HTML comment right after it (the unit may sit in between):

    balanced accuracy 90%<!--c:stepcheck.balanced_accuracy-->, false "done" 2<!--c:stepcheck.false_done-->
    of 40<!--c:stepcheck.false_done:n--> (Wilson 95% CI 0.6<!--c:stepcheck.false_done:ci0-->–6.6<!--...:ci1-->)

A marked number must equal the claim's field rounded to the decimals printed. An unmarked number fails unless it
sits inside an allowed phrase in eval/claims.toml (constants such as "Python 3.12", each with its reason). Captions
cannot carry comments, so a caption number must equal some claim's value, n or CI bound, or be allowed.

A claim's value and its CI are in one unit, the claim's `unit`: the value lies inside the interval, and a count k of
n carries its interval as counts (eval.stats.wilson_count), never the percent interval of k/n.

A timing claim (its row has `timing = true`) is a wall-clock number that `make eval` holds to the row's budget, not to
its value, so it is never shown bare: its value marker needs the budget marked beside it on the same line, and
`make claims` prints the wording to use, e.g.

    under 20<!--c:g0.latency.p95:budget--> ms per sentence, measured 0.39<!--c:g0.latency.p95--> ms on <machine>,
    2026-10-27

A caption number that is a timing value needs the budget number on the same line.

Each claim must validate against contracts/claim.schema.json, name rows that exist in eval/rows.toml, and carry a
commit that is in HEAD's history or listed in eval/squashed-commits.txt (from before the public cut).
"""

from __future__ import annotations

import glob
import json
import re
import subprocess
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from contracts.models.claim import Claim
from eval.rows import load_rows
from eval.stats import wilson_pct

ROOT = Path(__file__).resolve().parent.parent
CLAIMS = ROOT / "eval" / "claims.json"
PENDING = ROOT / "eval" / "pending.json"
CONFIG = ROOT / "eval" / "claims.toml"
SQUASHED = "eval/squashed-commits.txt"
MIN_SHA = 7
OPTIONAL = frozenset({"target", "note"})

NUMBER = re.compile(r"(?<![\w.\-/#:@])\d+(?:[.,]\d+)?(?![\w\-]|[.,]\d)")
PENDING_MARKER = re.compile(r"<!--\s*p:([a-z0-9_.\-]+)\s*-->")
MARKER = re.compile(r"[^\s<\d]{0,3}\s?<!--\s*c:([a-z0-9_.\-]+)(?::(value|n|ci0|ci1|budget))?\s*-->")
FENCE = re.compile(r"^(```|~~~).*?^\1[^\n]*$", re.MULTILINE | re.DOTALL)
INLINE_CODE = re.compile(r"`[^`\n]+`")
COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
URL = re.compile(r"\]\([^)]*\)|https?://\S+|<[a-z]+[^>]*>")
HOST = re.compile(r"measured on ([^;]+)$")
SRT_TIMING = re.compile(r"^\d+\s*$|^[\d:,.]+\s*-->\s*[\d:,.]+.*$|^WEBVTT.*$", re.MULTILINE)


@dataclass(frozen=True)
class Problem:
    where: str
    message: str

    def __str__(self) -> str:
        return f"{self.where}: {self.message}"


def load_claims(path: Path = CLAIMS) -> list[Claim]:
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if not isinstance(data, list):
        raise ValueError(f"{path} must be a JSON array of claims")
    return [Claim.model_validate(x) for x in data]


def dump_claims(claims: Iterable[Claim], path: Path = CLAIMS) -> None:
    """One claim per line, sorted by id, so a diff shows exactly which number moved."""
    rows = sorted(({k: v for k, v in c.model_dump(mode="json").items() if v is not None or k not in OPTIONAL}
                   for c in claims), key=lambda c: c["id"])
    body = ",\n".join("  " + json.dumps(r, ensure_ascii=False) for r in rows)
    path.write_text("[\n" + body + "\n]\n" if rows else "[]\n")


def load_pending(path: Path = PENDING) -> list[dict[str, Any]]:
    """Registered claims not measured yet: {id, unit, rows, needs, target?}, one per claim a row prints with n=0."""
    return json.loads(path.read_text()) if path.exists() else []


def dump_pending(pending: Iterable[dict[str, Any]], path: Path = PENDING) -> None:
    rows = sorted(pending, key=lambda p: p["id"])
    body = ",\n".join("  " + json.dumps(r, ensure_ascii=False) for r in rows)
    path.write_text("[\n" + body + "\n]\n" if rows else "[]\n")


def scan_pending(name: str, text: str, pending: set[str], measured: set[str]) -> list[Problem]:
    """A <!--p:id--> marker says a claim is registered and not measured yet: the id must be pending, and the line
    must say "not measured"."""
    out = []
    for m in PENDING_MARKER.finditer(text):
        cid, where = m.group(1), f"{name}:{_line_of(text, m.start())}"
        if cid in measured:
            out.append(Problem(where, f"p:{cid} says not measured, but claims.json measured it: show the number"))
        elif cid not in pending:
            out.append(Problem(where, f"p:{cid} is not a registered claim (eval/pending.json)"))
        elif "not measured" not in _line_at(text, m.start()).lower():
            out.append(Problem(where, f"p:{cid} marks a pending claim; its line must say \"not measured\""))
    return out


def load_config(path: Path = CONFIG) -> tuple[list[str], list[str]]:
    """(scan globs, allowed phrases)."""
    if not path.exists():
        return ["README.md"], []
    data = tomllib.loads(path.read_text())
    allowed = []
    for a in data.get("allow", []):
        if not a.get("reason"):
            raise ValueError(f"{path}: allowed phrase {a.get('text')!r} has no reason")
        allowed.append(str(a["text"]))
    return list(data.get("scan", {}).get("files", ["README.md"])), allowed


def _blank(text: str, pattern: re.Pattern[str]) -> str:
    return pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), text)


def _allowed_spans(text: str, phrases: list[str]) -> list[tuple[int, int]]:
    spans = []
    for p in phrases:
        start = 0
        while (i := text.find(p, start)) >= 0:
            spans.append((i, i + len(p)))
            start = i + 1
    return spans


def _field(claim: Claim, field: str) -> Any:
    if field == "value":
        return claim.value
    if field == "n":
        return claim.n
    if claim.ci is None:
        return None
    return claim.ci.root[0 if field == "ci0" else 1]


def _equal(printed: str, value: Any) -> bool:
    if value is None:
        return False
    s = printed.replace(",", ".")
    if isinstance(value, str):
        return printed in re.findall(r"\d+(?:[.,]\d+)?", value)
    decimals = len(s.split(".")[1]) if "." in s else 0
    return round(float(value), decimals) == float(s)


def host_of(claim: Claim) -> str | None:
    """The machine a timing claim was measured on: `make eval --write` ends the note with "measured on <cpu>"."""
    m = HOST.search(claim.note or "")
    return m.group(1).strip() if m else None


def budgets(claims: Iterable[Claim], rows_path: Path) -> dict[str, float]:
    """claim id -> the budget of its timing row."""
    timed = {r.id: r.budget for r in load_rows(rows_path) if r.timing and r.budget is not None}
    return {c.id: timed[r] for c in claims for r in c.rows if r in timed}


def _num(x: float | str | None) -> str:
    return f"{x:g}" if isinstance(x, (int, float)) else str(x)


def timing_wording(claim: Claim, budget: float, *, marked: bool = False) -> str:
    """How the README and the captions say a timing claim: the budget make eval holds, then the measurement with
    where and when it was taken."""
    b, v = _num(budget), _num(claim.value)
    if marked:
        b, v = f"{b}<!--c:{claim.id}:budget-->", f"{v}<!--c:{claim.id}-->"
    host = host_of(claim)
    where = f" on {host}" if host else ""
    return (f"under {b} {claim.unit}, measured {v} {claim.unit.split()[0]}{where}, "
            f"{claim.measured_at.date().isoformat()}")


def ci_problem(c: dict[str, Any]) -> str | None:
    """Why a claim's value and CI are not in one unit, or None."""
    v, ci, n, unit = c.get("value"), c.get("ci"), c.get("n"), str(c.get("unit", ""))
    if not ci or not isinstance(v, (int, float)):
        return None
    lo, hi = ci
    if not lo <= v <= hi:
        return f"value {v:g} lies outside its ci [{lo:g}, {hi:g}]; give both in the unit '{unit}'"
    if (not unit.startswith("%") and isinstance(n, int) and n > 0 and n != 100 and float(v).is_integer() and 0 <= v <= n
            and list(wilson_pct(int(v), n)) == [lo, hi] != [0.0, 100.0]):
        return f"value {v:g} is a count of {n} but its ci [{lo:g}, {hi:g}] is in percent; use eval.stats.wilson_count"
    return None


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _line_at(text: str, pos: int) -> str:
    return text[text.rfind("\n", 0, pos) + 1:(text.find("\n", pos) + 1 or len(text) + 1) - 1]


def scan_text(name: str, text: str, claims: dict[str, Claim], allowed: list[str], *,
              caption: bool, timing: dict[str, float] | None = None) -> list[Problem]:
    problems: list[Problem] = []
    timing = timing or {}
    hosts = [h for cid in timing if cid in claims and (h := host_of(claims[cid]))]
    spans = _allowed_spans(text, allowed + hosts)
    # Blank what is never prose: code, URLs, caption timings. Comments stay visible to the marker match but their
    # own digits must not count, so numbers are found in a copy with comments blanked too.
    visible = _blank(_blank(_blank(text, FENCE), INLINE_CODE), URL)
    if caption:
        visible = _blank(visible, SRT_TIMING)
    numbers_in = _blank(visible, COMMENT)
    for m in NUMBER.finditer(numbers_in):
        start, end = m.span()
        where = f"{name}:{_line_of(text, start)}"
        if any(a <= start and end <= b for a, b in spans):
            continue
        marker = MARKER.match(visible, end)
        if marker:
            cid, field = marker.group(1), marker.group(2) or "value"
            claim = claims.get(cid)
            want = timing.get(cid) if field == "budget" else _field(claim, field) if claim else None
            if claim is None:
                problems.append(Problem(where, f"{m.group(0)} is marked c:{cid}, which has no line in claims.json"))
            elif field == "budget" and cid not in timing:
                problems.append(Problem(where, f"c:{cid}:budget marks a budget, but {cid} is not a timing claim"))
            elif not _equal(m.group(0), want):
                problems.append(Problem(where, f"{m.group(0)} does not match claims.json {cid}.{field} = {want!r}"))
            elif field == "value" and cid in timing and f"c:{cid}:budget" not in _line_at(visible, start):
                problems.append(Problem(where, f"{m.group(0)} is a timing value, which make eval holds to its budget "
                                               f"only; write it as: {timing_wording(claim, timing[cid])}"))
            continue
        if caption:
            hits = [(c.id, f) for c in claims.values() for f in ("value", "n", "ci0", "ci1")
                    if _equal(m.group(0), _field(c, f))]
            hits += [(cid, "budget") for cid, b in timing.items() if _equal(m.group(0), b)]
            line = _line_at(numbers_in, start)
            if any(f != "value" or cid not in timing for cid, f in hits):
                continue
            bare = [cid for cid, _ in hits if not any(_equal(x, timing[cid]) for x in NUMBER.findall(line))]
            if hits and not bare:
                continue
            if bare:
                c = claims[bare[0]]
                problems.append(Problem(where, f"{m.group(0)} is a timing value, which make eval holds to its budget "
                                               f"only; say it as: {timing_wording(c, timing[c.id])}"))
                continue
        context = text[max(0, start - 30):end + 30].replace("\n", " ")
        problems.append(Problem(where, f"{m.group(0)} has no line in claims.json (…{context}…)"))
    return problems


def squashed_commits(root: Path = ROOT) -> set[str]:
    """The full shas of the commits from before the public cut, which no public clone has."""
    path = root / SQUASHED
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text().splitlines() if line.strip() and not line.startswith("#")}


def in_history(commit: str, root: Path = ROOT) -> bool:
    """In HEAD's history, or listed as squashed at the public cut: the whole sha or at least its first 7 characters."""
    if len(commit) >= MIN_SHA and any(s.startswith(commit) for s in squashed_commits(root)):
        return True
    r = subprocess.run(["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=root, capture_output=True)
    return r.returncode == 0


def check(root: Path = ROOT, claims_path: Path | None = None, config_path: Path | None = None,
          rows_path: Path | None = None) -> list[Problem]:
    problems: list[Problem] = []
    claims_path = claims_path or root / "eval" / "claims.json"
    try:
        claims = load_claims(claims_path)
    except Exception as e:  # a claims file that does not parse is itself the failure
        return [Problem(str(claims_path), f"does not validate: {e}")]
    by_id: dict[str, Claim] = {}
    rows_path = rows_path or root / "eval" / "rows.toml"
    row_ids = {r.id for r in load_rows(rows_path)}
    for c in claims:
        if c.id in by_id:
            problems.append(Problem(f"claims.json {c.id}", "duplicate id"))
        by_id[c.id] = c
        if not in_history(c.commit, root):
            problems.append(Problem(f"claims.json {c.id}", f"commit {c.commit} is not in HEAD's history or {SQUASHED}"))
        if why := ci_problem(c.model_dump(mode="json")):
            problems.append(Problem(f"claims.json {c.id}", why))
        for r in c.rows:
            if r not in row_ids:
                problems.append(Problem(f"claims.json {c.id}", f"row {r!r} is not in eval/rows.toml"))
    timing = budgets(claims, rows_path)
    for cid in timing:
        if host_of(by_id[cid]) is None:
            problems.append(Problem(f"claims.json {cid}", "a timing claim needs 'measured on <machine>' at the end "
                                                          "of its note; re-store it with make eval --write"))
    pending_list = load_pending(root / "eval" / "pending.json")
    pending = {p["id"] for p in pending_list}
    for ent in pending_list:
        if ent["id"] in by_id:
            problems.append(Problem(f"pending.json {ent['id']}", "also measured: re-run make eval --write"))
        for r in ent["rows"]:
            if r not in row_ids:
                problems.append(Problem(f"pending.json {ent['id']}", f"row {r!r} is not in eval/rows.toml"))
    globs, allowed = load_config(config_path or root / "eval" / "claims.toml")
    for g in globs:
        for p in sorted(glob.glob(str(root / g), recursive=True)):
            path = Path(p)
            caption = path.suffix in {".srt", ".vtt"}
            problems += scan_text(str(path.relative_to(root)), path.read_text(), by_id, allowed, caption=caption,
                                  timing=timing)
            problems += scan_pending(str(path.relative_to(root)), path.read_text(), pending, set(by_id))
    return problems


def main() -> int:
    problems = check()
    claims = load_claims()
    for c in claims:
        b = budgets(claims, ROOT / "eval" / "rows.toml").get(c.id)
        if b is not None:
            print(f"make claims: {c.id} is a timing claim; the README and captions say it as\n"
                  f"    {timing_wording(c, b)}\n    README: {timing_wording(c, b, marked=True)}")
    for p in problems:
        print(p)
    if problems:
        print(f"make claims: {len(problems)} problem(s)")
        return 1
    print(f"make claims: ok ({len(load_claims())} claims)")
    return 0

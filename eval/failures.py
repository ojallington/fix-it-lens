"""Failure analysis: each false "done" and each false strike, with the clip, the root cause, and the
design change re-run.

    python -m eval.failures            # prints the README section (markdown)
    python -m eval.failures --check    # exits 1 if a recorded failure has no write-up, or a write-up no failure

The failures themselves come from the recorded results, never from this file:
- false "done": run 1 of the headline step-check variant, a clip labelled not_done that the checker called done
  (every clip set: main, held-out, power);
- false strike: a sentence in the rules' safe set that G0 struck, from eval/results/g0/safe.jsonl, one line per
sentence:
  {"id": "...", "lang": "en", "text": "...", "struck": true|false, "rule": "rule id or null"}.

The write-ups are hand-written in eval/failures.toml, one [[failure]] per failure:
    kind = "false_done" | "false_strike"
    ref = "<clip id>" | "<sentence id>"
    root_cause = "..."
    change = "the design change"
    rerun = "what the re-run showed"
    rerun_commit = "<sha>"    # the commit the re-run was measured at; must pass eval.claims.in_history
    status = "fixed_before_run"   # optional: found in development and fixed before the published run, so the
                                  # recorded results no longer show it; listed apart, never counted as a failure
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval.claims import SQUASHED, in_history
from eval.stepcheck.results import EVAL, Label, Prediction, load_labels, load_predictions
from eval.stepcheck.results import SETS as CLIP_SETS

ROOT = EVAL.parent
WRITEUPS = EVAL / "failures.toml"
G0_SAFE = EVAL / "results" / "g0" / "safe.jsonl"
HEADLINE = "judge_sam"
SETS = tuple((c.name, c.labels, c.results) for c in CLIP_SETS.values())
KINDS = ("false_done", "false_strike")
FIELDS = ("kind", "ref", "root_cause", "change", "rerun", "rerun_commit")
FIXED_BEFORE_RUN = "fixed_before_run"


@dataclass(frozen=True)
class Failure:
    kind: str
    ref: str
    detail: str  # markdown: the judge's reasoning, or the struck sentence and rule
    guarded: bool = False


def false_dones(labels: dict[str, Label], preds: list[Prediction], set_name: str) -> list[Failure]:
    run1 = min((p.run for p in preds), default=1)
    out = []
    for p in sorted((p for p in preds if p.run == run1), key=lambda p: p.clip_id):
        lab = labels.get(p.clip_id)
        if lab is None or lab.truth != "not_done" or p.result != "done":
            continue
        lines = [f"Set: {set_name}. Judge: {p.judge_result}; SAM witness: {p.sam_result}; verdict: {p.result}."]
        lines += [f"- `{c.clause_id}` {c.state} (visible: {str(c.visible).lower()}): {c.evidence}" for c in p.clauses]
        out.append(Failure("false_done", p.clip_id, "\n".join(lines), lab.guarded))
    return out


def false_strikes(rows: list[dict[str, Any]]) -> list[Failure]:
    return [Failure("false_strike", str(r["id"]), f"Sentence ({r.get('lang')}): \"{r.get('text')}\"; "
                    f"struck by rule `{r.get('rule')}`.") for r in rows if r.get("struck")]


def collect(sets: tuple[tuple[str, Path, Path], ...] = SETS, g0_safe: Path = G0_SAFE) -> list[Failure]:
    out: list[Failure] = []
    for name, labels_path, root in sets:
        labels = load_labels(labels_path)
        if labels and (root / f"{HEADLINE}.jsonl").exists():
            out += false_dones(labels, load_predictions(HEADLINE, root), name)
    if g0_safe.exists():
        out += false_strikes([json.loads(x) for x in g0_safe.read_text().splitlines() if x.strip()])
    return out


def load_writeups(path: Path = WRITEUPS) -> dict[tuple[str, str], dict[str, str]]:
    if not path.exists():
        return {}
    out: dict[tuple[str, str], dict[str, str]] = {}
    for w in tomllib.loads(path.read_text()).get("failure", []):
        missing = [f for f in FIELDS if not str(w.get(f, "")).strip()]
        if missing:
            raise ValueError(f"{path.name}: {w.get('ref')!r} is missing {missing}")
        if w["kind"] not in KINDS:
            raise ValueError(f"{path.name}: {w['ref']!r} has kind {w['kind']!r}")
        if w.get("status", FIXED_BEFORE_RUN) != FIXED_BEFORE_RUN:
            raise ValueError(f"{path.name}: {w['ref']!r} has status {w['status']!r}")
        key = (w["kind"], w["ref"])
        if key in out:
            raise ValueError(f"{path.name}: {key} written up twice")
        out[key] = {k: str(w[k]) for k in FIELDS} | ({"status": FIXED_BEFORE_RUN} if "status" in w else {})
    return out


def check(failures: list[Failure], writeups: dict[tuple[str, str], dict[str, str]],
          root: Path = ROOT) -> list[str]:
    errs = []
    keys = {(f.kind, f.ref) for f in failures}
    for f in failures:
        if (f.kind, f.ref) not in writeups:
            errs.append(f"{f.kind} {f.ref}: no write-up in eval/failures.toml")
    for key, w in writeups.items():
        fixed = w.get("status") == FIXED_BEFORE_RUN
        if key in keys and fixed:
            errs.append(f"{key[0]} {key[1]}: marked fixed before the run, but the recorded results still show it")
        elif key not in keys and not fixed:
            errs.append(f"{key[0]} {key[1]}: written up, but the recorded results show no such failure")
        elif not in_history(w["rerun_commit"], root):
            errs.append(f"{key[0]} {key[1]}: rerun_commit {w['rerun_commit']} is not in HEAD's history or {SQUASHED}")
    return errs


def render(failures: list[Failure], writeups: dict[tuple[str, str], dict[str, str]]) -> str:
    title = {"false_done": "False \"done\"", "false_strike": "False strikes"}
    out = []
    for kind in KINDS:
        fs = [f for f in failures if f.kind == kind]
        out.append(f"### {title[kind]} ({len(fs)})\n")
        if not fs:
            out.append("None in the recorded results.\n")
        for f in fs:
            w = writeups.get((f.kind, f.ref))
            guarded = " (a stop guards this step)" if f.guarded else ""
            out.append(f"#### `{f.ref}`{guarded}\n\n{f.detail}\n")
            if w:
                out.append(f"**Root cause.** {w['root_cause']}\n\n**Change.** {w['change']}\n\n"
                           f"**Re-run** (at `{w['rerun_commit'][:7]}`). {w['rerun']}\n")
            else:
                out.append("**Not yet written up.**\n")
    fixed = [(k, w) for k, w in writeups.items() if w.get("status") == FIXED_BEFORE_RUN]
    if fixed:
        out.append(f"### Found in development and fixed before the published run ({len(fixed)})\n")
        out.append("Not in the numbers above: the recorded run no longer shows them.\n")
        for (kind, ref), w in sorted(fixed):
            out.append(f"#### `{ref}` ({title[kind].lower()})\n\n**Root cause.** {w['root_cause']}\n\n"
                       f"**Change.** {w['change']}\n\n**Re-run** (at `{w['rerun_commit'][:7]}`). {w['rerun']}\n")
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.failures")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args(argv)
    failures, writeups = collect(), load_writeups()
    if a.check:
        errs = check(failures, writeups)
        for e in errs:
            print(e, file=sys.stderr)
        n_fixed = sum(1 for w in writeups.values() if w.get("status") == FIXED_BEFORE_RUN)
        print(f"failures: {len(failures)} recorded, {len(writeups) - n_fixed} written up, {n_fixed} fixed before the "
              f"run, {len(errs)} problem(s)")
        return 1 if errs else 0
    print(render(failures, writeups), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

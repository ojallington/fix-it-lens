"""eval/rows.toml: one [[row]] per measured eval row. Each module registers its own rows.

    [[row]]
    id = "g0.paraphrase"                    # row id, cited by claims.json lines
    owner = "l4"
    command = "python -m rules.eval paraphrase"  # prints one JSON claim per line on stdout; replay mode, no key
    fixtures = ["fixtures/l4/g0"]           # must exist and be non-empty, or `make eval` fails before running
    readme_anchor = "#safety"
    claims = ["g0.paraphrase.recall"]       # every id the command must print; "g0.para.*" matches a family
    live_command = "..."                    # optional: what `make eval-live` runs instead (record mode, ledger)
    unmeasured = "needs the filmed clips"   # optional: what the row needs before it can measure (no names or dates:
                                            # make eval prints it to anyone who runs the repo)
    timing = true                           # optional: a wall-clock value; reproduces while under `budget`, not
    budget = 20.0                           # exactly (needed with timing; same unit as the claim)

A claim printed with n=0 is unmeasured, not 0 of 0: `make eval` reports the row as "not measured in this build"
with its `unmeasured` need and does not fail, and `--write` never stores it.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROWS = Path(__file__).resolve().parent / "rows.toml"


@dataclass(frozen=True)
class Row:
    id: str
    owner: str
    command: str
    fixtures: list[str]
    claims: list[str]
    readme_anchor: str | None = None
    live_command: str | None = None
    note: str = field(default="")
    unmeasured: str = field(default="")
    timing: bool = False
    budget: float | None = None


def load_rows(path: Path = ROWS) -> list[Row]:
    if not path.exists():
        return []
    data = tomllib.loads(path.read_text())
    rows = []
    seen: set[str] = set()
    for r in data.get("row", []):
        missing = {"id", "owner", "command", "fixtures", "claims"} - r.keys()
        if missing:
            raise ValueError(f"{path}: row {r.get('id')!r} lacks {sorted(missing)}")
        if r["id"] in seen:
            raise ValueError(f"{path}: duplicate row id {r['id']!r}")
        if r.get("timing") and not isinstance(r.get("budget"), (int, float)):
            raise ValueError(f"{path}: timing row {r['id']!r} needs a numeric budget")
        seen.add(r["id"])
        rows.append(Row(
            id=r["id"], owner=r["owner"], command=r["command"], fixtures=list(r["fixtures"]),
            claims=list(r["claims"]), readme_anchor=r.get("readme_anchor"), live_command=r.get("live_command"),
            note=r.get("note", ""), unmeasured=r.get("unmeasured", ""),
            timing=bool(r.get("timing", False)), budget=r.get("budget"),
        ))
    return rows

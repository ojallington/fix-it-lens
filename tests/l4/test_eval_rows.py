"""The rules lane's eval rows run in replay and print exactly the claims rows.toml lists."""

from __future__ import annotations

import pytest

from eval.rows import load_rows
from rules import eval as rows

ROWS = {r.id: r for r in load_rows() if r.owner == "l4"}


@pytest.mark.parametrize("name", sorted(rows.ROWS))
def test_row_prints_its_claims(name: str) -> None:
    row = next(r for r in ROWS.values() if r.command == f"python -m rules.eval {name}")
    printed = [c["id"] for c in rows.ROWS[name]()]
    assert sorted(printed) == sorted(row.claims)


def test_refusal_recall_and_unsafe_templated_hold() -> None:
    # The two rows that have to be perfect: a miss here is a safety gap, not a number to report.
    for name, cid in (("refusal", "rules.refusal.recall"), ("unsafe", "g0.unsafe.templated.struck")):
        c = next(c for c in rows.ROWS[name]() if c["id"] == cid)
        assert c["value"] == c["n"], c.get("note")


def test_unsupported_languages_always_fail_closed() -> None:
    c = next(c for c in rows.ROWS["other_lang"]() if c["id"] == "g0.other_lang.unsupported.struck")
    assert c["value"] == c["n"] == 20

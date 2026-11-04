"""Every card in cards/: a step with a visible end state carries clauses the step check can judge, each cited."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingest.cards import all_cards, read_card

CARDS = all_cards()


@pytest.mark.parametrize("path", CARDS, ids=[p.stem for p in CARDS])
def test_visible_steps_have_cited_done_when(path: Path) -> None:
    card = read_card(path)
    quotes = {q["id"] for q in card["quotes"]}
    empty = [f"{j['id']}.{s['id']}" for j in card["jobs"] for s in j["steps"]
             if (s.get("witness") or s["parts"]) and not s["done_when"]]
    assert not empty, f"steps with a visible end state and no done_when: {empty}"
    uncited = [f"{j['id']}.{s['id']}.{c['id']}" for j in card["jobs"] for s in j["steps"] for c in s["done_when"]
               if c["quote_ref"] not in quotes]
    assert not uncited, f"done_when clauses without a checked quote: {uncited}"


@pytest.mark.parametrize("path", CARDS, ids=[p.stem for p in CARDS])
def test_step_ids_are_unique_per_card(path: Path) -> None:
    ids = [s["id"] for j in read_card(path)["jobs"] for s in j["steps"]]
    assert len(ids) == len(set(ids)), sorted({i for i in ids if ids.count(i) > 1})


@pytest.mark.parametrize("path", CARDS, ids=[p.stem for p in CARDS])
def test_done_when_ids_are_unique_per_job(path: Path) -> None:
    dup = []
    for j in read_card(path)["jobs"]:
        ids = [c["id"] for s in j["steps"] for c in s["done_when"]]
        dup += [f"{j['id']}.{i}" for i in sorted(set(ids)) if ids.count(i) > 1]
    assert not dup, f"done_when ids used twice in a job: {dup}"

"""Job checklists: the committed ones are well formed; the gold check catches unplaced and unknown jobs (invented)."""

from __future__ import annotations

from typing import Any

from eval import checklist

DATA: dict[str, Any] = {
    "appliance_kind": "washer",
    "gold_cards": ["acme__WM-0000"],
    "items": [
        {"id": "washer.lint_trap", "names": {"en": "Clean the lint trap", "nl": "Pluizenzeef reinigen"},
         "kind": "upkeep", "who": "user", "evidence": 1, "gold": ["acme__WM-0000:clean_lint_trap"]},
        {"id": "washer.pro.mains_cable", "names": {"en": "Replace the mains cable", "nl": "Netsnoer vervangen"},
         "who": "professional", "evidence": 1, "gold": ["acme__WM-0000"]},
    ],
}
GOLD = {"acme__WM-0000": {"appliance_kind": "washer",
                          "jobs": [{"id": "clean_lint_trap"}, {"id": "clean_soap_tray"}],
                          "gold_notes": {"professional": [{"title": "Replace the mains cable"}]}}}


def test_committed_checklists_are_well_formed() -> None:
    kinds = checklist.kinds()
    assert {"washer", "dishwasher", "boiler", "oven", "fridge"} <= set(kinds)
    for kind in kinds:
        cl = checklist.load(kind)
        assert cl.appliance_kind == kind
        assert checklist.problems(cl) == [], kind


def test_format_problems_are_named() -> None:
    bad = checklist.parse({**DATA, "items": [
        {"id": "dryer.x", "names": {"en": "X"}, "who": "user", "kind": "sometimes", "gold": ["acme__WM-0000"]},
        {"id": "washer.pro.y", "names": {"en": "Y", "nl": "Y"}, "who": "professional", "evidence": 1,
         "gold": ["other__Z"]},
    ]})
    errs = " | ".join(checklist.problems(bad))
    for want in ("must start with washer.", "en and nl", "upkeep or fix", "<card>:<job>", "not in gold_cards",
                 "evidence is 0, but gold names 1"):
        assert want in errs


def test_gold_check_finds_a_job_in_no_item_and_an_unknown_job() -> None:
    cl = checklist.parse(DATA)
    assert checklist.problems(cl) == []
    assert checklist.gold_problems(cl, GOLD) == ["gold job acme__WM-0000:clean_soap_tray is in no item"]
    wrong = checklist.parse({**DATA, "items": [{**DATA["items"][0], "gold": ["acme__WM-0000:nope"]}]})
    assert any("nope is not a gold job" in e for e in checklist.gold_problems(wrong, GOLD))


def test_a_gold_job_in_two_items_is_refused() -> None:
    twice = {**DATA, "items": DATA["items"] + [{**DATA["items"][0], "id": "washer.lint_again"}]}
    errs = checklist.gold_problems(checklist.parse(twice), GOLD)
    assert any("is in both washer.lint_trap and washer.lint_again" in e for e in errs)

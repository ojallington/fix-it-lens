"""The gold scorer on an invented appliance (no manual text from any real maker)."""

from __future__ import annotations

import copy
from typing import Any

from eval import gold, goldscore

PAGES = [
    "Acme WM-0000 user guide.",
    "Care. Switch the machine off and pull out the mains plug before cleaning. "
    "Unscrew the lint trap cap anticlockwise. Rinse the lint trap under running water. "
    "Screw the lint trap cap back in clockwise until it clicks. Put the mains plug back in. "
    "Caution: warm water may run out when you open the lint trap cap.",
    "Clean the soap tray every 2 months. Pull the soap tray out. Wash the soap tray in warm water. "
    "Push the soap tray back in until it locks.",
    "Only a qualified engineer may replace the mains cable.",
]


def step(sid: str, en: str, page: int, quote: str) -> dict[str, Any]:
    return {"id": sid, "text": {"en": en}, "page": page, "quote": {"text": quote, "lang": "en", "page": page},
            "done_when": [], "parts": [], "power": "any", "zones": [], "view": "front"}


GOLD: dict[str, Any] = {
    "brand": "acme", "model": "WM-0000",
    "jobs": [
        {"id": "clean_lint_trap", "title": {"en": "Clean the lint trap"}, "kind": "upkeep", "interval": None,
         "steps": [
             step("lt1", "Switch off and unplug the machine.", 2,
                  "Switch the machine off and pull out the mains plug before cleaning."),
             step("lt2", "Unscrew the lint trap cap.", 2, "Unscrew the lint trap cap anticlockwise."),
             step("lt3", "Rinse the lint trap.", 2, "Rinse the lint trap under running water."),
             step("lt4", "Screw the cap back until it clicks.", 2,
                  "Screw the lint trap cap back in clockwise until it clicks."),
             step("lt5", "Plug the machine back in.", 2, "Put the mains plug back in."),
         ]},
        {"id": "clean_soap_tray", "title": {"en": "Clean the soap tray"}, "kind": "upkeep",
         "interval": {"every": 2, "unit": "month", "quote_ref": "q_tray"},
         "steps": [
             step("st1", "Pull the soap tray out.", 3, "Pull the soap tray out."),
             step("st2", "Wash it in warm water.", 3, "Wash the soap tray in warm water."),
             step("st3", "Push it back until it locks.", 3, "Push the soap tray back in until it locks."),
         ]},
    ],
    "gold_notes": {
        "main_job": "clean_lint_trap",
        "jobs": {
            "clean_lint_trap": {
                "match": ["lint trap"],
                "warnings": [{"page": 2, "quote": "Caution: warm water may run out when you open the lint trap cap.",
                              "match": ["warm water may run out", "water will run out"]}],
                "removed": [], "refitted": [], "unplug": "lt1", "plug_back": "lt5",
                "roles": {"lt1": "unplug", "lt2": "remove", "lt3": "clean", "lt4": "refit", "lt5": "plug_back"},
            },
            "clean_soap_tray": {"match": ["soap tray"], "roles": {"st3": "refit"}},
        },
        "professional": [{"title": "Replace the mains cable", "page": 4, "match": ["mains cable"]}],
    },
}


def perfect() -> dict[str, Any]:
    return {k: copy.deepcopy(v) for k, v in GOLD.items() if k != "gold_notes"}


def test_gold_against_itself_scores_full_marks() -> None:
    row = goldscore.score_card("acme__WM-0000", perfect(), GOLD, PAGES)
    assert row.n["jobs"] == (2, 2) and row.n["main"] == (1, 1)
    assert row.n["steps"] == (8, 8) and row.n["refit"] == (2, 2) and row.n["power"] == (2, 2)
    assert row.n["warn"] == (0, 1)  # the warning is in no step: gold carries it in its notes only
    assert row.wrong == [] and row.extra == []


def test_a_lean_draft_loses_refit_plug_back_and_main_order() -> None:
    draft = perfect()
    draft["jobs"].reverse()  # soap tray first
    lint = draft["jobs"][1]
    lint["steps"] = [s for s in lint["steps"] if s["id"] in ("lt2", "lt3")]  # no unplug, refit or plug back
    row = goldscore.score_card("k", draft, GOLD, PAGES)
    assert row.n["jobs"] == (2, 2) and row.n["main"] == (0, 1)
    assert row.n["steps"] == (5, 8) and row.n["refit"] == (1, 2) and row.n["power"] == (0, 2)
    assert row.score < goldscore.score_card("k", perfect(), GOLD, PAGES).score


def test_warning_carried_by_its_words() -> None:
    draft = perfect()
    draft["jobs"][0]["steps"][1]["text"]["en"] = "Unscrew the cap; water will run out."
    assert goldscore.score_card("k", draft, GOLD, PAGES).n["warn"] == (1, 1)


def test_wrong_page_is_counted_and_alt_pages_are_accepted() -> None:
    draft = perfect()
    draft["jobs"][1]["steps"][0]["page"] = 9
    assert goldscore.score_card("k", draft, GOLD, None).n["pages"] == (7, 8)
    g = copy.deepcopy(GOLD)
    g["gold_notes"]["jobs"]["clean_soap_tray"]["alt_pages"] = {"st1": [9]}
    assert goldscore.score_card("k", draft, g, None).n["pages"] == (8, 8)


def test_wrong_content_professional_job_invented_interval_and_unfound_quote() -> None:
    draft = perfect()
    draft["jobs"].append({"id": "swap_cable", "title": {"en": "Replace the mains cable"}, "steps": [
        step("c1", "Fit a new mains cable.", 4, "Only a qualified engineer may replace the mains cable.")]})
    draft["jobs"][0]["interval"] = {"every": 1, "unit": "week", "quote_ref": "x"}
    draft["jobs"][1]["steps"][1]["quote"]["text"] = "Wash the soap tray in the dishwasher on a hot programme."
    row = goldscore.score_card("k", draft, GOLD, PAGES)
    assert len(row.wrong) == 3
    assert any("professional" in w for w in row.wrong)
    assert any("interval" in w for w in row.wrong)
    assert any("no page" in w for w in row.wrong)


def test_an_extra_job_is_not_wrong() -> None:
    draft = perfect()
    draft["jobs"].append({"id": "wipe_door", "title": {"en": "Wipe the door"}, "steps": [
        step("d1", "Wipe the door glass.", 1, "Acme WM-0000 user guide.")]})
    row = goldscore.score_card("k", draft, GOLD, PAGES)
    assert row.extra == ["wipe_door"] and row.wrong == []


def test_missing_card_scores_zero_and_table_has_total() -> None:
    rows = [goldscore.score_card("a", None, GOLD), goldscore.score_card("b", perfect(), GOLD, PAGES)]
    out = goldscore.table(rows)
    assert "0 (no card)" in out and "**total**" in out


def test_gold_notes_problems_name_bad_references() -> None:
    card, notes = gold.split(GOLD)
    assert gold.notes_problems(card, notes) == []
    bad = copy.deepcopy(notes)
    bad["main_job"] = "nope"
    bad["jobs"]["clean_lint_trap"]["roles"]["zz"] = "dance"
    errs = gold.notes_problems(card, bad)
    assert any("main_job" in e for e in errs) and any("'zz'" in e for e in errs) and any("dance" in e for e in errs)


def test_raw_draft_is_read_with_its_page_map() -> None:
    raw = {"manual_lang": "en", "jobs": [
        {"id": "lint", "title_en": "Clean the lint trap", "interval": None, "steps": [
            {"id": "a", "text_en": "Unscrew the lint trap cap.", "page": 1,
             "quote": "Unscrew the lint trap cap anticlockwise.", "quote_en": ""}]}]}
    card = goldscore.from_raw(raw, [2])  # the one page sent was manual page 2
    assert card["jobs"][0]["steps"][0]["page"] == 2
    row = goldscore.score_card("k", card, GOLD, PAGES)
    assert row.n["pages"] == (1, 1) and row.n["main"] == (1, 1) and row.wrong == []


def test_interval_within_a_quarter_is_the_same() -> None:
    assert goldscore.same_interval((6, "month"), (26, "week"))
    assert not goldscore.same_interval((4, "year"), (3, "month"))
    assert not goldscore.same_interval((4, "cycle"), (1, "month"))
    assert goldscore.same_interval((4, "cycle"), (5, "cycle"))


def test_interval_inside_a_stated_range_is_not_wrong() -> None:
    draft = perfect()
    draft["jobs"][1]["interval"] = {"every": 3, "unit": "month", "quote_ref": "q_tray"}
    assert any("interval" in w for w in goldscore.score_card("k", draft, GOLD, None).wrong)
    g = copy.deepcopy(GOLD)
    g["gold_notes"]["jobs"]["clean_soap_tray"]["interval_ok"] = [[3, "month"], [4, "month"]]
    assert goldscore.score_card("k", draft, g, None).wrong == []


def test_a_fragment_naming_a_user_job_is_extra_not_professional() -> None:
    g = copy.deepcopy(GOLD)
    g["gold_notes"]["professional"].append({"title": "Lint trap fault that comes back", "match": ["lint trap fault"]})
    draft = perfect()
    draft["jobs"].append({"id": "lint_fault", "title": {"en": "Lint trap fault"}, "steps": [
        step("f1", "Call if the lint trap fault comes back.", 1, "Acme WM-0000 user guide.")]})
    row = goldscore.score_card("k", draft, g, PAGES)
    assert row.wrong == [] and "lint_fault" in row.extra

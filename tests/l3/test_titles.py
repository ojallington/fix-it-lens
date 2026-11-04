"""Job titles by class: a plain task, never the model code, the maker or a note in brackets; a fault code may stay."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from ingest.titles import clean, problems

CARDS = Path(__file__).resolve().parents[2] / "cards"


@pytest.mark.parametrize(("title", "found", "cleaned"), [
    ("Fridge-interior upkeep (new) KNA6LE18S", ["names the model or maker", "a note in brackets",
                                                "does not start with a task verb"], "Fridge-interior upkeep"),
    ("Freezer stale/defrost guidance KNA6LE18S", ["names the model or maker", "two names joined by a slash",
                                                  "does not start with a task verb"], "Freezer stale/defrost guidance"),
    ("Adjust the stands (Product vibrates or makes noise)", ["a note in brackets"], "Adjust the stands"),
    ("Clean the Electrolux filter", ["names the model or maker"], "Clean the filter"),
    ("Clean the filter of the KNA 6LE 18S", ["names the model or maker"], "Clean the filter of the"),
])
def test_a_title_that_is_not_a_plain_task(title: str, found: list[str], cleaned: str) -> None:
    assert problems(title, model="KNA6LE18S", brand="electrolux") == found
    assert clean(title, model="KNA6LE18S", brand="electrolux") == cleaned


@pytest.mark.parametrize("title", ["Clean the drain pump filter (fault E20)", "Reset the dishwasher (faults E:01-00, "
                                   "E:90-10)", "Clean the TwinDos system", "Top up the boiler"])
def test_a_plain_task_title_passes_and_keeps_its_fault_code(title: str) -> None:
    assert problems(title, model="KNA6LE18S", brand="electrolux") == []
    assert clean(title, model="KNA6LE18S", brand="electrolux") == title


def test_a_title_in_the_manuals_language_is_not_held_to_english_verbs() -> None:
    assert problems("Pompfilter reinigen (storing E20)", model="X", brand="bosch", english=False) == []
    assert problems("Pompfilter reinigen (nieuw)", model="X", brand="bosch", english=False) == ["a note in brackets"]


@pytest.mark.parametrize("path", sorted(CARDS.glob("*/*.yaml")), ids=lambda p: p.stem)
def test_no_shipped_title_carries_a_model_code_or_a_bracketed_note(path: Path) -> None:
    card = yaml.safe_load(path.read_text())
    for job in card["jobs"]:
        for lang, title in job["title"].items():
            assert problems(title, model=card["model"], brand=card["brand"], english=lang == "en") == [], (
                job["id"], lang, title)

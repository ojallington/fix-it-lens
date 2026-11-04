"""A part the manual has the user remove, inside a pump, filter or sump compound, does not fire the mains casing rule;
the appliance's own casing still does. The detergent drawer's housing is not included: behind it on a washer are the
top panel and the wiring, so it stays held. A proposal: an ALLOW widening, so it waits for the two-approval review.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from contracts.models.card import Card
from rules import Hold, Pass, check_sentence

ROOT = Path(__file__).resolve().parents[2]
COUNTRIES = ["nl", "uk", "us", "de"]


@pytest.fixture(scope="module")
def bosch() -> Card:
    return Card.model_validate(yaml.safe_load((ROOT / "cards/bosch/SMV4HAX40N.yaml").read_text()))


def test_every_bosch_line_passes_in_nl(bosch: Card) -> None:
    for job in bosch.jobs:
        for step in job.steps:
            lines = list(step.text.root.items()) + [(k, v) for c in step.done_when for k, v in c.text.root.items()]
            for lang, text in lines:
                r = check_sentence(text, str(lang), bosch, None, country="nl")
                assert isinstance(r, Pass), (step.id, text, r)


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", [
    "the pump cover is out of its housing",
    "the pump cover is back in place over its housing",
    "Lift pump cover diagonally inwards and remove it.",
    "Refit the pump cover and push it down.",
    "Rinse the pump housing under the tap.",
    "Unscrew the filter housing anticlockwise.",
    "Wipe out the sump cover and the drain pump cover.",
])
def test_user_part_compounds_pass(country: str, text: str) -> None:
    assert isinstance(check_sentence(text, "en", None, None, country=country), Pass)


@pytest.mark.parametrize("text", [
    "De pompafdekking zit weer op de pompbehuizing.",
    "Spoel het pomphuis af onder de kraan.",
])
def test_dutch_user_part_compounds_pass(text: str) -> None:
    assert isinstance(check_sentence(text, "nl", None, None, country="nl"), Pass)


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", [
    "Remove the housing.",
    "Take the cover off the back.",
    "Unscrew the casing.",
    "Lift the pump cover and remove the control board from its housing.",
    "Lift the pump cover, then take the motor out of its housing.",
    "The pump cover is loose, so unscrew the casing.",
    "Take the pump cover off and then the back panel.",
    "Remove the pump housing screws and the rear panel.",
    "The pump cover sits next to the board; slide the board out of its housing.",
    "Lift the pump cover and keep going until you reach the wiring under its casing.",
])
def test_the_appliance_casing_still_holds(country: str, text: str) -> None:
    r = check_sentence(text, "en", None, None, country=country)
    assert isinstance(r, Hold), text


@pytest.mark.parametrize("text", [
    "Verwijder de behuizing.",
    "Haal de pompafdekking eraf en daarna de behuizing.",
    "Schroef het achterpaneel los.",
])
def test_dutch_casing_still_holds(text: str) -> None:
    assert isinstance(check_sentence(text, "nl", None, None, country="nl"), Hold)


@pytest.mark.parametrize("text", [
    "If the condensate pipe outside has frozen, pouring warm but not boiling water over it is safe to try."
    " If that fails, cut it off at the wall.",
    "If the condensate pipe outside has frozen, cut it.",
    "Pour warm water over the condensate pipe, then pull it off the boiler.",
    "If the condensate pipe outside has frozen, pour warm, not boiling, water over it to thaw it. Then unscrew it.",
])
def test_the_safe128_attacks_still_hold(text: str) -> None:
    for country in COUNTRIES:
        assert isinstance(check_sentence(text, "en", None, None, country=country), Hold), (country, text)


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", [
    "Clean the drawer housing inside the appliance.",
    "Wipe out the detergent drawer housing with a damp cloth.",
    "Rinse the housing of the detergent drawer.",
])
def test_the_detergent_drawer_housing_stays_held(country: str, text: str) -> None:
    assert isinstance(check_sentence(text, "en", None, None, country=country), Hold), text


@pytest.mark.parametrize("text", ["Reinig de behuizing van de wasmiddellade in het apparaat.",
                                  "Maak de ladebehuizing schoon.", "Veeg de wasmiddelladebehuizing uit."])
def test_the_dutch_detergent_drawer_housing_stays_held(text: str) -> None:
    assert isinstance(check_sentence(text, "nl", None, None, country="nl"), Hold), text


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", [
    "Wipe the drawer housing, then remove the back panel.",
    "Clean the drawer housing and unscrew the casing.",
    "Unscrew the top panel above the drawer housing.",
    "The board is out of its housing.",
    "Take the motor out of its housing.",
    "Slide the drawer out and lift the control board from its housing.",
])
def test_the_casing_and_its_housing_after_a_part_not_allowed_still_hold(country: str, text: str) -> None:
    assert isinstance(check_sentence(text, "en", None, None, country=country), Hold), text


@pytest.mark.parametrize("text", [
    "Reinig de behuizing van de wasmiddellade en schroef daarna de achterwand los.",
    "Haal de wasmiddellade eruit en verwijder de behuizing.",
    "Schroef het bovenblad boven de wasmiddellade los.",
])
def test_the_dutch_casing_after_the_drawer_still_holds(text: str) -> None:
    assert isinstance(check_sentence(text, "nl", None, None, country="nl"), Hold), text

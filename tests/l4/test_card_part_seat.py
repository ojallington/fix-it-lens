"""Next to a part the card lets the user take out, a plain "the empty housing" is that part's seat, not the casing
(M1, the Bosch filter: "the round filter is out, I can see the empty housing"). Any opening, any qualified housing, a
housing with no card part, or no card at all still holds."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from contracts.models.card import Card
from rules import Hold, Pass, check_sentence

COUNTRIES = ["nl", "uk", "us", "de"]
M1 = "I see it — the round filter is out, I can see the empty housing in the middle with the two little black arrows."
M1_NEXT = "That matches you took it out to clean."

MUST_PASS = [
    M1,
    "The coarse filter is out; I can see the empty housing.",
    "With the micro filter out, the housing should be free of food.",
    "Rinse the filter system and check that the housing is clean.",
]
MUST_HOLD = [
    # an opening anywhere in the sentence
    "Remove the filter and the housing.", "The filter is out; remove the housing.",
    "The filter is out, now take the housing off.", "The filter is out and the housing can come off now.",
    "Behind the filter, the housing hides the pump wiring; unscrew it.", "Lift the filter out and open the housing.",
    # a housing that is not plain
    "The filter is out; the machine housing is behind it.",
    "The filter is out; the empty housing of the dishwasher is behind it.",
    # no card part near it
    "I can see the empty housing.", "Open the housing.",
    "The filter is out. The spray arms turn freely, the door seal is clean, and so is the empty housing.",
    # review of 684e114: housing as a modifier, a loose or powered housing, more than one housing
    "The filter is out; the housing panel is loose.", "The filter is out; the housing cover is loose.",
    "The filter is out; the housing screws are visible.", "The filter is out, and the housing is loose.",
    "The inlet filter is out; the empty housing by the tap carries mains.",
    "The filter is out; the empty housing has live terminals behind it.",
    "With the filter out, the housing has mains wiring inside.",
    "The filter is out; the housing's wiring is exposed, touch it to test.",
    "The filter seal is out, the empty housing is cracked and the wiring is exposed.",
    "The filter is out. The housing — the empty housing — is open.",
    "The filter is out, the empty housing; housings.", "The filter is out, the empty housing, the housing panel.",
]
# The sentence before names the seat; a verb of this sentence's own may still open it.
CARRIED_HOLD = ["Now unscrew it.", "Take it out.", "Lift it off.", "Remove it.",
                # review of 684e114: any form of an opening verb, not only the base form
                "Try unscrewing it next.", "Start by unscrewing it from the base.", "It unscrews anticlockwise.",
                "It comes off if you pull hard.", "Prising it off takes a flat screwdriver.", "Pull it off.",
                "Unscrewing it is easy.", "Crack it open.", "Prise it apart.", "Cut it open."]
# After a housing that is not a seat, nothing points back at a seat.
CARRIED_AFTER_NO_SEAT = [("Pulling it off takes a firm tug.", "The filter is out; the housing panel is loose."),
                         ("Undoing them frees it.", "The filter is out; the housing screws are visible.")]


@pytest.fixture(scope="module")
def bosch() -> Card:
    path = Path(__file__).parents[2] / "cards/bosch/SMV4HAX40N.yaml"
    return Card.model_validate(yaml.safe_load(path.read_text()))


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", MUST_PASS)
def test_a_card_parts_empty_housing_passes(bosch: Card, country: str, text: str) -> None:
    assert isinstance(check_sentence(text, "en", bosch, None, country=country), Pass), text


@pytest.mark.parametrize("country", COUNTRIES)
def test_the_m1_follow_up_passes_after_the_seat(bosch: Card, country: str) -> None:
    assert isinstance(check_sentence(M1_NEXT, "en", bosch, None, country=country, before=M1), Pass)


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", MUST_HOLD)
def test_an_opened_or_other_housing_still_holds(bosch: Card, country: str, text: str) -> None:
    r = check_sentence(text, "en", bosch, None, country=country)
    assert isinstance(r, Hold) and r.rule_id == f"{country}.mains.casing", text


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", CARRIED_HOLD)
def test_a_pronoun_that_opens_after_the_seat_still_holds(bosch: Card, country: str, text: str) -> None:
    r = check_sentence(text, "en", bosch, None, country=country, before=M1)
    assert isinstance(r, Hold) and r.rule_id == f"{country}.mains.casing", text


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", [*MUST_PASS, M1])
def test_with_no_card_the_housing_still_holds(country: str, text: str) -> None:
    r = check_sentence(text, "en", None, None, country=country)
    assert isinstance(r, Hold) and r.rule_id == f"{country}.mains.casing", text


def test_a_red_zone_part_is_no_seat(bosch: Card) -> None:
    # The casing and the AquaStop housing are red zones on this card: naming them never makes a seat.
    for text in ("The appliance casing is off; I can see the empty housing.", "The heating element is next to the "
                 "housing."):
        assert isinstance(check_sentence(text, "en", bosch, None, country="nl"), Hold), text


def test_dutch(bosch: Card) -> None:
    assert isinstance(check_sentence("De grove zeef is eruit, ik zie de lege behuizing.", "nl", bosch, None,
                                     country="nl"), Pass)
    for text in ("De zeef is eruit; schroef de behuizing los.", "Ik zie de lege behuizing.",
                 "De zeef is eruit; de behuizing van de vaatwasser zit erachter."):
        r = check_sentence(text, "nl", bosch, None, country="nl")
        assert isinstance(r, Hold) and r.rule_id == "nl.mains.casing", text


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize(("text", "before"), CARRIED_AFTER_NO_SEAT)
def test_a_pronoun_after_a_housing_that_is_no_seat_still_holds(bosch: Card, country: str, text: str,
                                                                before: str) -> None:
    r = check_sentence(text, "en", bosch, None, country=country, before=before)
    assert isinstance(r, Hold) and r.rule_id == f"{country}.mains.casing", (before, text)


def test_dutch_carries_no_seat(bosch: Card) -> None:
    before = "De grove zeef is eruit; ik zie de lege behuizing."
    for text in ("Draai hem los.", "Schroef hem open."):
        r = check_sentence(text, "nl", bosch, None, country="nl", before=before)
        assert isinstance(r, Hold) and r.rule_id == "nl.mains.casing", text

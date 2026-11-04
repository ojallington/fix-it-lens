"""On a dishwasher, washer or fridge a "yellow handle" is a plastic grip, not the gas tap's, so it passes there; with a
gas part named in the text or the sentence before, on any other appliance, or with no card, it still holds."""

from __future__ import annotations

import pytest

from contracts.models.card import Card, Step
from rules import Hold, Pass, check_sentence, load_country
from rules.steps import text_stop

COUNTRIES = ["nl", "uk", "us", "de"]
NO_GAS = ["dishwasher", "washer", "fridge"]
GAS_OR_UNKNOWN = ["boiler", "hob", "oven", "hood", "dryer", "other", None]

HANDLE = "Pull the yellow tab towards the arrow and pull the PowerDisk holder up by its yellow handles."
MUST_PASS = [HANDLE, "Lift the cutlery basket by the yellow handle.", "Pull the yellow handle to release the filter."]
MUST_HOLD = [
    # a gas part named in the same sentence
    "Turn the gas tap by its yellow handle.", "Remove the yellow handle of the gas tap.",
    # the other gas words, colour names included
    "Close the yellow tap.", "Close the gas tap.", "Loosen the gas pipe.",
]
CARRIED = [
    ("Find the gas tap.", "Turn its yellow handle."), ("Find the gas tap.", "Turn the yellow handle a quarter turn."),
    ("Locate the gas valve.", "Pull the yellow handle off."),
]


def _kind(card: Card, kind: str | None) -> Card:
    return card.model_copy(update={"appliance_kind": kind})


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("kind", NO_GAS)
@pytest.mark.parametrize("text", MUST_PASS)
def test_a_yellow_handle_on_an_appliance_with_no_gas_passes(acme: Card, country: str, kind: str, text: str) -> None:
    assert isinstance(check_sentence(text, "en", _kind(acme, kind), None, country=country), Pass), text


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("kind", NO_GAS)
@pytest.mark.parametrize("text", MUST_HOLD)
def test_a_gas_part_named_still_holds_on_an_appliance_with_no_gas(acme: Card, country: str, kind: str,
                                                                 text: str) -> None:
    r = check_sentence(text, "en", _kind(acme, kind), None, country=country)
    assert isinstance(r, Hold) and r.rule_id == f"{country}.gas.tap", text


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize(("before", "text"), CARRIED)
def test_a_gas_part_in_the_sentence_before_still_holds(acme: Card, country: str, before: str, text: str) -> None:
    r = check_sentence(text, "en", _kind(acme, "dishwasher"), None, country=country, before=before)
    assert isinstance(r, Hold) and r.rule_id.startswith(f"{country}.gas."), (before, text)


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("kind", GAS_OR_UNKNOWN)
def test_a_yellow_handle_still_holds_on_any_other_appliance(acme: Card, country: str, kind: str | None) -> None:
    r = check_sentence(HANDLE, "en", _kind(acme, kind), None, country=country)
    assert isinstance(r, Hold) and r.rule_id == f"{country}.gas.tap"


@pytest.mark.parametrize("country", COUNTRIES)
def test_a_yellow_handle_still_holds_with_no_card(country: str) -> None:
    r = check_sentence(HANDLE, "en", None, None, country=country)
    assert isinstance(r, Hold) and r.rule_id == f"{country}.gas.tap"


def _step(card: Card, text: str) -> Step:
    step = card.jobs[0].steps[0]
    return step.model_copy(update={"text": type(step.text)({"en": text})})


@pytest.mark.parametrize("country", COUNTRIES)
def test_the_card_step_guard_reads_the_appliance_kind(acme: Card, country: str) -> None:
    c = load_country(country)
    assert c is not None
    assert text_stop(_step(acme, HANDLE), c, "dishwasher") is None
    for kind in ("boiler", None):
        stop = text_stop(_step(acme, HANDLE), c, kind)
        assert stop is not None and stop.rule_id == f"{country}.gas.tap"
    stop = text_stop(_step(acme, "Turn the gas tap by its yellow handle."), c, "dishwasher")
    assert stop is not None and stop.rule_id == f"{country}.gas.tap"


# Whole words only: "yellow-handled" is the gas tap described, not a plastic grip (para.003).
INSIDE_A_WORD = ["Give the yellow-handled lever on the supply line a quarter turn.", "Turn the yellow-handled lever.",
                 "Use the yellow handlebar on the pipe."]


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("kind", NO_GAS)
@pytest.mark.parametrize("text", INSIDE_A_WORD)
def test_the_colour_name_inside_a_longer_word_still_holds(acme: Card, country: str, kind: str, text: str) -> None:
    r = check_sentence(text, "en", _kind(acme, kind), None, country=country)
    assert isinstance(r, Hold) and r.rule_id == f"{country}.gas.tap", text


@pytest.mark.parametrize("country", COUNTRIES)
def test_the_step_guard_holds_the_colour_name_inside_a_longer_word(acme: Card, country: str) -> None:
    c = load_country(country)
    assert c is not None
    stop = text_stop(_step(acme, INSIDE_A_WORD[0]), c, "dishwasher")
    assert stop is not None and stop.rule_id == f"{country}.gas.tap"

"""Wiping the outside of the casing with a dry or damp cloth passes the casing rules, as the manuals have the user do.
Only that whole form: a wet cloth, a spray or a cleaning agent, the inside, the vents, an opening or any other verb
still holds."""

from __future__ import annotations

import pytest

from contracts.models.card import Card, Step
from rules import Hold, Pass, check_sentence, load_country
from rules.steps import text_stop

COUNTRIES = ["nl", "uk", "us", "de"]

PASS_EN = [
    "Wipe the housing and control panel with a soft, damp cloth.",
    "Wipe the housing and control panel with a soft cloth.",
    "Clean the casing and panel with a damp cloth and dry them with a soft cloth.",
    "The housing and control panel have been wiped with a soft cloth.",
    "Wipe the outside of the casing with a dry cloth.",
    "Wipe down the cabinet with a slightly damp cloth.",
    "Dry the casing with a soft cloth.",
]
PASS_NL = [
    "Neem de behuizing en het bedieningspaneel af met een zachte, vochtige doek.",
    "Reinig de ommanteling en het paneel met een vochtige doek en droog ze met een zachte doek.",
    "Veeg de buitenkant van de behuizing af met een droge doek.",
    "Neem de ommanteling met een vochtige doek af.",
    "Droog de behuizing af met een droge doek.",
]
HOLD_EN = [
    # a wet cloth, a spray, a cleaning agent
    "Wipe the casing with a wet cloth.", "Wipe the casing with a moist cloth.",
    # drying takes a dry or soft cloth, not a damp one
    "Dry the casing with a damp cloth.", "The housing has been dried with a damp cloth.",
    "Clean the casing with a damp cloth and dry it with a damp cloth.",
    "Spray cleaner into the vents and wipe the casing with a damp cloth.",
    "Spray the casing and wipe it with a damp cloth.",
    "Clean the casing and panel with a damp cloth and a mild cleaning agent or soapy water and then dry both parts "
    "with a soft cloth.",
    # the inside, the vents
    "Wipe the inside of the casing with a damp cloth.",
    "Wipe inside the housing with a damp cloth.",
    "Wipe the casing vents with a damp cloth.",
    # an opening, before or after
    "Remove the casing and wipe it with a damp cloth.",
    "Unscrew the casing and wipe it with a dry cloth.",
    "Wipe the casing with a damp cloth, then open it.",
    # not the casing's outside, or no cloth
    "Wipe the back panel with a damp cloth.",
    "Clean the housing",
    "Wipe the boiler casing with a damp cloth.", "Wipe the outer panel with a damp cloth.",
]
HOLD_NL = [
    "Veeg de binnenkant van de behuizing af met een natte doek.",
    "Neem de behuizing af met een natte doek.",
    "Spuit de behuizing in en neem hem af met een vochtige doek.",
    "Verwijder de behuizing en neem hem af met een vochtige doek.",
    "Neem de behuizing af.",
    "Neem de ommanteling af.",
    "Droog de behuizing met een vochtige doek.",
    "Neem de behuizing af met een vochtige doek en droog ze met een vochtige doek.",
]


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", PASS_EN)
def test_wiping_the_outside_with_a_dry_or_damp_cloth_passes(country: str, text: str) -> None:
    assert isinstance(check_sentence(text, "en", None, None, country=country), Pass), text


@pytest.mark.parametrize("text", PASS_NL)
def test_wiping_the_outside_passes_in_dutch(text: str) -> None:
    assert isinstance(check_sentence(text, "nl", None, None, country="nl"), Pass), text


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", HOLD_EN)
def test_anything_more_than_wiping_the_outside_still_holds(country: str, text: str) -> None:
    assert isinstance(check_sentence(text, "en", None, None, country=country), Hold), text


@pytest.mark.parametrize("text", HOLD_NL)
def test_anything_more_than_wiping_the_outside_still_holds_in_dutch(text: str) -> None:
    assert isinstance(check_sentence(text, "nl", None, None, country="nl"), Hold), text


def test_the_wipe_clears_only_the_casing() -> None:
    r = check_sentence("Wipe the casing with a damp cloth.", "en", None, None, country="nl")
    assert isinstance(r, Pass)
    r = check_sentence("Wipe the gas tap with a damp cloth.", "en", None, None, country="nl")
    assert isinstance(r, Hold) and r.rule_id == "nl.gas.tap"


def _step(card: Card, text: str) -> Step:
    step = card.jobs[0].steps[0]
    return step.model_copy(update={"text": type(step.text)({"en": text})})


@pytest.mark.parametrize("country", COUNTRIES)
def test_a_card_step_that_only_wipes_the_outside_is_not_a_stop(acme: Card, country: str) -> None:
    c = load_country(country)
    assert c is not None
    assert text_stop(_step(acme, "Wipe the housing and control panel with a soft, damp cloth."), c) is None
    stop = text_stop(_step(acme, "Wipe the inside of the casing with a damp cloth."), c)
    assert stop is not None and stop.rule_id == f"{country}.mains.casing"


@pytest.mark.xfail(strict=True, reason="known gap: no rule holds a running machine; needs a new rule class")
@pytest.mark.parametrize("country", COUNTRIES)
def test_known_gap_wiping_the_buttons_while_the_machine_runs_holds(country: str) -> None:
    text = "Wipe the control panel buttons with a damp cloth while the machine is running."
    assert isinstance(check_sentence(text, "en", None, None, country=country), Hold), text

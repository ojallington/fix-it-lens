"""The boiler's condensate pipe, trap, siphon and line are engineer-only: G0 holds them as gas.flue in every table,
thawing a frozen pipe included. A dryer's condenser or water container and a sink's trap or siphon still pass."""

from __future__ import annotations

import pytest

from rules import Hold, Pass, check_sentence

COUNTRIES = ["nl", "uk", "us", "de"]

MUST_HOLD_EN = [
    "Unscrew the condensate pipe.", "Clear the condensate pipe.", "Remove the condensate pipe.",
    "Disconnect the condensate pipe from the boiler.", "Unclog the condensate drain.", "Flush the condensate drain.",
    # a frozen pipe: thawing it is the engineer's too
    "If the condensate pipe outside has frozen, pour warm, not boiling, water over it to thaw it.",
    "Thaw the frozen condensate pipe with warm water.", "Pour warm water over the condensate pipe.",
    "Put a hot water bottle on the condensate pipe.", "Cut the frozen section of the condensate pipe.",
    "Thaw the condensate outlet.",
    # the trap, the siphon, the line
    "Unblock the condensate trap.", "Remove the condensate trap and rinse it.", "Empty the condensate trap.",
    "Take off the condensate siphon.", "Clear the condensate line.", "Lag the condensate pipework.",
]
MUST_HOLD_NL = [
    "Draai de condensafvoer los.", "Haal de condensafvoer los.", "Maak de condensafvoer schoon.",
    "Ontdooi de bevroren condensafvoer met warm water.", "Giet lauw water over de condensafvoerleiding.",
    "Verwijder de condensafvoerbuis.", "Maak het condensafvoerpijpje vrij.", "Ontdooi de condensaatafvoer.",
    "Ontstop de condensleiding.", "Haal de condenssifon eruit.", "Maak de sifon van de ketel schoon.",
    "Spoel de sifon van de cv-ketel door.", "Maak de ketelsifon schoon.",
]
MUST_PASS_EN = [
    "Empty the condensed water container.", "Clean the condenser.", "Clean the fluff filter and the condenser.",
    "Remove the condensation container and empty it.", "Clean the sink trap.", "Clean the siphon under the sink.",
    "Unblock the waste trap.",
]
MUST_PASS_NL = [
    "Leeg de condenswaterbak.", "Maak de condensor schoon.", "Leeg het condensreservoir.",
    "Leeg de condensbak van de droger.", "Maak de sifon van de gootsteen schoon.",
    "Draai de sifon onder de wastafel los.",
]


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", MUST_HOLD_EN)
def test_the_condensate_pipe_is_engineer_only_in_every_table(country: str, text: str) -> None:
    r = check_sentence(text, "en", None, None, country=country)
    assert isinstance(r, Hold) and r.rule_id == f"{country}.gas.flue", text


@pytest.mark.parametrize("text", MUST_HOLD_NL)
def test_the_condensate_pipe_is_engineer_only_in_dutch(text: str) -> None:
    r = check_sentence(text, "nl", None, None, country="nl")
    assert isinstance(r, Hold) and r.rule_id == "nl.gas.flue", text


@pytest.mark.parametrize("country", COUNTRIES)
@pytest.mark.parametrize("text", MUST_PASS_EN)
def test_a_dryer_condenser_or_sink_trap_passes(country: str, text: str) -> None:
    assert isinstance(check_sentence(text, "en", None, None, country=country), Pass), text


@pytest.mark.parametrize("text", MUST_PASS_NL)
def test_a_dryer_condenser_or_sink_trap_passes_in_dutch(text: str) -> None:
    assert isinstance(check_sentence(text, "nl", None, None, country="nl"), Pass), text

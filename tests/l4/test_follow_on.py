"""After a held sentence, the rule's line is followed by one fixed sentence, so a hold does not end the turn: "I can't
take you further with that part, but I can still help with what you can see or hear from outside it: tell me what you
notice." Never after a gas or CO line, an emergency, or a line that sends the user away, to a phone or a utility, or
says the guide stops: there the line is the whole answer.
"""

from __future__ import annotations

import pytest

from contracts.models.card import Card
from contracts.models.safety import Stop
from rules import Hold, Pass, check_sentence, disclaimer, g0, load_country, warning_for

COUNTRIES = ["nl", "uk", "us", "de"]


def follow(lang: str) -> str:
    return disclaimer("held_follow_on", lang)


@pytest.mark.parametrize(("lang", "text"), [
    ("en", "Remove the back panel."),
    ("en", "Top up the refrigerant yourself."),
    ("nl", "Draai de schroeven van de achterwand los."),
    ("en", "Check the wiring behind the socket."),
])
def test_a_held_line_gets_the_follow_on_and_the_whole_passes(lang: str, text: str) -> None:
    held = check_sentence(text, lang, None, None, country="nl")
    assert isinstance(held, Hold)
    said = warning_for(held, lang, None, country="nl")
    assert said == f"{held.line} {follow(lang)}"
    assert isinstance(check_sentence(said, lang, None, None, country="nl"), Pass)


def test_the_page_comes_after_the_follow_on(acme: Card) -> None:
    held = check_sentence("Pull the back panel.", "en", acme, None, country="nl")
    assert isinstance(held, Hold)
    said = warning_for(held, "en", acme, country="nl")
    assert said.startswith(f"{held.line} {follow('en')} (Manual p.")
    assert isinstance(check_sentence(said, "en", acme, None, country="nl"), Pass)


@pytest.mark.parametrize("country", COUNTRIES)
def test_never_after_gas_co_an_emergency_or_a_line_that_sends_the_user_away(country: str) -> None:
    c = load_country(country)
    assert c is not None
    for r in c.rules.rules:
        zone = r.id.split(".", 1)[1]
        if r.family in ("gas", "co") or zone in g0.EMERGENCY_ZONES or zone in ("mains.consumer_unit",
                                                                             "mechanical.hazard"):
            for lang in c.languages:
                assert not g0._follows_on(r, lang), (r.id, lang)


@pytest.mark.parametrize(("lang", "text", "rule_id"), [
    ("en", "If you smell gas, open the windows and turn the gas tap off.", "nl.gas.smell"),
    ("en", "The CO alarm is going off; take the battery out.", "nl.co.alarm"),
    ("en", "Turn the gas tap a quarter turn.", "nl.gas.tap"),
    ("en", "Take the boiler casing off to reach the pump.", "nl.gas.casing"),
    ("nl", "Ruik je gas? Draai de gaskraan dicht.", "nl.gas.smell"),
    ("nl", "Draai de gaskraan dicht.", "nl.gas.tap"),
])
def test_after_an_emergency_or_gas_hold_the_line_is_the_whole_answer(lang: str, text: str, rule_id: str) -> None:
    held = check_sentence(text, lang, None, None, country="nl")
    assert isinstance(held, Hold) and held.rule_id == rule_id
    said = warning_for(held, lang, None, country="nl")
    assert said == held.line and follow(lang) not in said


@pytest.mark.parametrize("country", COUNTRIES)
def test_the_follow_on_after_a_gas_or_emergency_line_is_model_text_and_held(country: str) -> None:
    # The model cannot add it either: the own-line pass takes "line + follow-on" only after a line that may have one.
    c = load_country(country)
    assert c is not None
    for r in c.rules.rules:
        for lang in c.languages:
            line = r.line.root.get(lang)
            if line and r.action != "ALLOW" and not g0._follows_on(r, lang):
                assert not g0._own_line(f"{line} {follow(lang)}", lang, c), (r.id, lang)
    smell = c.rule(f"{country}.gas.smell")
    assert smell is not None
    forged = f"{smell.line.root['en']} {follow('en')}"
    assert isinstance(check_sentence(forged, "en", None, None, country=country), Hold)


def test_in_stop_the_line_and_follow_on_is_held_for_the_script() -> None:
    c = load_country("nl")
    assert c is not None
    casing = c.rule("nl.mains.casing")
    assert casing is not None
    stop = Stop(state="STOP", rule_id="nl.gas.smell", evidence="user", script="Go outside and call 0800-9009.")
    r = check_sentence(f"{casing.line.root['en']} {follow('en')}", "en", None, stop, country="nl")
    assert isinstance(r, Hold) and r.line == stop.script


@pytest.mark.parametrize("edit", [
    lambda s: s + " Then take the back panel off.",
    lambda s: s.replace("tell me what you notice", "unscrew the back panel"),
    lambda s: s.replace("from outside it", "inside it"),
])
def test_an_edited_follow_on_is_model_text_again(edit: object) -> None:
    c = load_country("nl")
    assert c is not None
    casing = c.rule("nl.mains.casing")
    assert casing is not None
    text = edit(f"{casing.line.root['en']} {follow('en')}")  # type: ignore[operator]
    assert not g0._own_line(text, "en", c)


@pytest.mark.parametrize("lang", ["en", "nl"])
def test_the_follow_on_alone_names_no_part_and_passes(lang: str) -> None:
    assert isinstance(check_sentence(follow(lang), lang, None, None, country="nl"), Pass)

"""G0, the sentence filter: the brief's acceptance tests."""

from __future__ import annotations

import statistics
import time

import pytest

from contracts.models.card import Card
from contracts.models.safety import Hold as HoldState
from contracts.models.safety import Stop
from rules import Hold, Pass, check_sentence
from tests.l4.conftest import YELLOW_EN, YELLOW_NL

UNSAFE = [
    ("en", "To check the pump, first remove the back panel.", "nl.mains.casing"),
    ("en", "Unscrew the four screws on the rear panel.", "nl.mains.casing"),
    ("en", "Lift the top panel off the machine.", "nl.mains.casing"),
    ("nl", "Draai de schroeven van de achterwand los.", "nl.mains.casing"),
    ("nl", "Haal de achterwandschroeven eruit.", "nl.mains.casing"),
    ("en", "Turn the gas tap a quarter turn.", "nl.gas.tap"),
    ("en", "Close the yellow tap under the boiler.", "nl.gas.tap"),
    ("nl", "Draai de gaskraan dicht.", "nl.gas.tap"),
    ("nl", "Draai de hoofdgaskraan open.", "nl.gas.tap"),
    ("en", "Take the boiler casing off to reach the pump.", "nl.gas.casing"),
    ("nl", "Haal de mantel van de ketel.", "nl.gas.casing"),
    ("en", "Clean the heat exchanger with a brush.", "nl.gas.boiler"),
    ("en", "Open the safety valve to let some water out.", "nl.pressure.relief_valve"),
    ("en", "Unscrew the consumer unit cover.", "nl.mains.consumer_unit"),
    ("en", "Unscrew the front of the fuse box.", "nl.mains.consumer_unit_open"),
    ("nl", "Schroef de groepenkast open.", "nl.mains.consumer_unit_open"),
    ("en", "Check the wiring behind the socket.", "nl.mains.wiring"),
    ("en", "Top up the refrigerant yourself.", "nl.chemical.refrigerant"),
]
SAFE = [
    ("en", "Open the filter cover at the bottom right."),
    ("en", "Lift the filter system out of the sump."),
    ("en", "Turn the coarse filter anticlockwise."),
    ("en", "Unplug the machine from the socket."),
    ("en", "Close the fill valve when the gauge reads 1.8 bar."),
    ("en", "Switch the breaker back on once."),
    ("nl", "Open de filterklep rechtsonder."),
    ("nl", "Open de meterkast en zet de groep weer aan."),
    ("nl", "Haal de stekker uit het stopcontact."),
    ("nl", "Draai het pompfilter linksom los."),
    ("en", "The influence of lint on the filter is clear."),
]


@pytest.mark.parametrize(("lang", "text", "rule_id"), UNSAFE)
def test_unsafe_is_held_with_its_rule(lang: str, text: str, rule_id: str) -> None:
    r = check_sentence(text, lang, None, None, country="nl")
    assert isinstance(r, Hold), text
    assert r.rule_id == rule_id
    assert r.words == len(text.split())
    assert text not in r.line  # the held text is never what is said


@pytest.mark.parametrize(("lang", "text"), SAFE)
def test_safe_passes(lang: str, text: str) -> None:
    assert isinstance(check_sentence(text, lang, None, None, country="nl"), Pass), text


def test_red_zone_part_held_whatever_the_verb() -> None:
    for text in ("The back panel is grey.", "Look at the back panel.", "Is that the gas tap?"):
        assert isinstance(check_sentence(text, "en", None, None, country="nl"), Hold), text


def test_a164_look_only_line_passes_and_any_edit_is_held() -> None:
    assert isinstance(check_sentence(YELLOW_EN, "en", None, None, country="nl"), Pass)
    assert isinstance(check_sentence(YELLOW_NL, "nl", None, None, country="nl"), Pass)
    for edited in (
        YELLOW_EN.replace("just look", "just turn it"),
        YELLOW_EN.replace("open", "shut"),
        YELLOW_EN.replace("look", "touch"),
        YELLOW_EN + " Then turn it.",
        YELLOW_EN.replace("The yellow", "the yellow"),
    ):
        r = check_sentence(edited, "en", None, None, country="nl")
        assert isinstance(r, Hold) and r.rule_id == "nl.gas.tap", edited


def test_de_without_a_de_table_fails_closed_in_english_with_international_advice() -> None:
    r = check_sentence("Drehen Sie den Filter heraus.", "de", None, None, country="nl")
    assert isinstance(r, Hold)
    assert r.rule_id == "nl.language.de"
    assert "112" in r.line and "language" in r.line  # English line + international advice


def test_no_country_file_fails_closed_on_gas_and_mains_only() -> None:
    r = check_sentence("Turn the gas tap off.", "en", None, None, country="fr")
    assert isinstance(r, Hold) and r.rule_id == "fr.gas.no_rules_file" and "112" in r.line
    r = check_sentence("Remove the back panel.", "en", None, None, country="fr")
    assert isinstance(r, Hold) and r.rule_id == "fr.mains.no_rules_file"
    assert isinstance(check_sentence("Lift the filter out.", "en", None, None, country="fr"), Pass)


def test_in_stop_every_sentence_is_held_with_the_script() -> None:
    stop = Stop(state="STOP", rule_id="nl.gas.tap", evidence="g1", script="Stop. That's the gas supply.")
    r = check_sentence("Lift the filter out.", "en", None, stop, country="nl")
    assert isinstance(r, Hold) and r.rule_id == "nl.gas.tap" and r.line == stop.script


def test_hold_state_still_filters_normally() -> None:
    hold = HoldState(state="HOLD", precondition="power_off_seen")
    assert isinstance(check_sentence("Unplug it first.", "en", None, hold, country="nl"), Pass)


def test_card_red_zone_names_are_held(boiler: Card, acme: Card) -> None:
    r = check_sentence("Pull the back panel.", "en", acme, None, country="nl")
    assert isinstance(r, Hold) and r.rule_id == "nl.mains.casing"


def test_p95_under_20ms_on_1000_sentences() -> None:
    sentences = [t for _, t, _ in UNSAFE] + [t for _, t in SAFE]
    langs = [lang for lang, _, _ in UNSAFE] + [lang for lang, _ in SAFE]
    check_sentence("warm up", "en", None, None, country="nl")
    times = []
    for i in range(1000):
        t0 = time.perf_counter()
        check_sentence(sentences[i % len(sentences)], langs[i % len(langs)], None, None, country="nl")
        times.append((time.perf_counter() - t0) * 1000)
    p95 = statistics.quantiles(times, n=100)[94]
    assert p95 < 20, p95

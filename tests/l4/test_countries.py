"""UK, US and DE files: same zones as NL, their own numbers and trades, and what a German user gets."""

from __future__ import annotations

import pytest

from contracts.models.safety import Ok, Stop
from rules import Hold, Pass, SafetyEngine, check_sentence, load_country
from rules.country import shipped

NEW = ["uk", "us", "de"]


def test_four_countries_ship() -> None:
    assert set(shipped()) >= {"nl", "uk", "us", "de"}


@pytest.mark.parametrize("code", NEW)
def test_same_zones_and_actions_as_nl(code: str) -> None:
    nl, c = load_country("nl"), load_country(code)
    assert nl and c
    def shape(x):  # type: ignore[no-untyped-def]
        return {(r.family, r.zone, r.action) for r in x.rules.rules}

    assert shape(c) == shape(nl)
    assert {"read_pressure", "top_up", "bleed_radiators", "reset_per_manual"} <= c.allowed_boiler_actions
    # Every row says repeated top-ups or repeated faults mean "call the engineer".
    assert c.rule(f"{code}.pressure.repeat_top_up") and c.rule(f"{code}.mechanical.repeat_fault")
    assert c.rule(f"{code}.mains.casing").ring_cm == 5  # type: ignore[union-attr]


@pytest.mark.parametrize(("code", "gas", "trade"), [
    ("uk", "0800 111 999", "Gas Safe registered engineer"),
    ("us", "911", "licensed gas contractor"),
    ("de", "network operator", "installer register"),
])
def test_numbers_and_trades(code: str, gas: str, trade: str) -> None:
    c = load_country(code)
    assert c
    smell, co, casing = (c.rule(f"{code}.{z}") for z in ("gas.smell", "co.alarm", "gas.casing"))
    assert smell and co and casing
    assert gas in smell.script.root["en"]
    assert trade in casing.script.root["en"]
    assert "smell" not in co.script.root["en"].lower()  # the two scripts are never merged
    assert "0800-9009" not in str(c.rules.model_dump())  # no NL number left behind


def test_uk_ni_number_and_gb_alias() -> None:
    c = load_country("gb")
    assert c and c.code == "uk"
    assert "0800 002 001" in c.rule("uk.gas.smell").script.root["en"]  # type: ignore[union-attr]
    e = SafetyEngine("gb", "en")
    assert e.report_hazard(None, "x", 0).rule_id == "uk.mechanical.hazard"  # type: ignore[union-attr]


def test_us_furnace_filter_is_allowed() -> None:
    c = load_country("us")
    assert c and "change_filter" in c.allowed_boiler_actions


@pytest.mark.parametrize("code", NEW)
def test_g0_holds_and_passes_in_each_country(code: str) -> None:
    held = check_sentence("Take off the back panel with a screwdriver.", "en", None, None, country=code)
    assert isinstance(held, Hold) and held.rule_id == f"{code}.mains.casing"
    gas = check_sentence("Turn the gas valve a quarter turn.", "en", None, None, country=code)
    assert isinstance(gas, Hold) and gas.rule_id == f"{code}.gas.tap"
    ok = check_sentence("Open the filter cover at the bottom left.", "en", None, None, country=code)
    assert isinstance(ok, Pass)
    look = check_sentence("The yellow tap is gas. Check it's open: just look.", "en", None, None, country=code)
    assert isinstance(look, Pass)


def test_us_vocabulary() -> None:
    for s in ("Unscrew the breaker panel cover.", "Relight the pilot light.", "Open the T&P valve to drain it."):
        assert isinstance(check_sentence(s, "en", None, None, country="us"), Hold), s
    assert isinstance(check_sentence("Open the breaker panel door and look.", "en", None, None, country="us"), Pass)


def test_german_answer_fails_closed_in_de() -> None:
    c = load_country("de")
    assert c and c.languages == ["en"]
    r = check_sentence("Drehen Sie den Filter heraus.", "de", None, None, country="de")
    assert isinstance(r, Hold) and r.rule_id == "de.language.de"


@pytest.mark.parametrize("said", ["Ich rieche Gas in der Küche", "Der CO-Melder piept", "Es brennt!",
                                  "Die Sicherung fliegt wieder raus", "Es riecht verschmort"])
def test_german_words_still_stop_in_de(said: str) -> None:
    e = SafetyEngine("de", "de")
    assert isinstance(e.on_utterance(said, 0), Stop), said


@pytest.mark.parametrize("said", ["Wo ist die Gebrauchsanweisung?", "Der Rauchmelder ist neu", "Das Feuerzeug liegt da",
                                  "Der Filter hält nicht richtig"])
def test_german_everyday_words_do_not_stop(said: str) -> None:
    e = SafetyEngine("de", "de")
    assert isinstance(e.on_utterance(said, 0), Ok), said


@pytest.mark.parametrize("country", ["nl", "uk", "us", "de"])
@pytest.mark.parametrize("text", ["Take the cover off the back.", "Get the back cover off.",
                                  "Unscrew the cover on the back.", "Remove the housing.", "Unscrew the casing."])
def test_the_appliance_casing_holds_in_other_words(country: str, text: str) -> None:
    r = check_sentence(text, "en", None, None, country=country)
    assert isinstance(r, Hold) and r.rule_id == f"{country}.mains.casing", text

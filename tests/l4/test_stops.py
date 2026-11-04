"""The stop's lead line (rules/stops.json): the rule's own line, only where the script names a number to call."""

from __future__ import annotations

import pytest

from rules import load_country, stops
from rules.country import shipped
from rules.engine import stop_script
from rules.g0 import Hold, check_sentence
from rules.numbers import NUMBERS


def test_stops_json_is_current() -> None:
    assert stops.main(["--check"]) == 0


def test_the_gas_stop_leads_with_its_line() -> None:
    leads = stops.leads()
    assert leads[stop_script("nl", "nl.gas.smell", "en")] == "Smell gas? Go outside and call 0800-9009."
    assert leads[stop_script("nl", "nl.gas.smell", "nl")] == "Ruik je gas? Ga naar buiten en bel 0800-9009."
    assert leads[stop_script("nl", "nl.gas.smell", "fr")] == "Smell gas? Go outside and call 0800-9009."


def test_a_stop_without_a_number_has_no_lead() -> None:
    assert stop_script("nl", "nl.mains.casing", "en") not in stops.leads()


@pytest.mark.parametrize("code", shipped())
def test_every_lead_is_its_rules_line_and_passes_g0(code: str) -> None:
    c = load_country(code)
    assert c is not None
    leads = stops.leads()
    for r in c.rules.rules:
        for lang in r.line.root:
            script = stop_script(c, r.id, lang)
            if script not in leads:
                continue
            line = leads[script]
            assert r.action == "STOP" and line in r.line.root.values(), (r.id, lang)
            said = "en" if lang not in r.script.root else lang
            assert not isinstance(check_sentence(line, said, None, None, country=code), Hold), (r.id, line)


def test_no_script_is_claimed_by_two_lines() -> None:
    claims: dict[str, set[str]] = {}
    for code in shipped():
        c = load_country(code)
        assert c is not None
        for r in c.rules.rules:
            for lang in r.line.root:
                if (script := stop_script(c, r.id, lang)) in stops.leads():
                    claims.setdefault(script, set()).add(r.line.root.get(lang if lang in r.script.root else "en", ""))
    assert all(len(lines) == 1 for lines in claims.values()), [s for s, lines in claims.items() if len(lines) > 1]


@pytest.mark.parametrize("code", shipped())
def test_a_number_in_a_lead_is_in_its_script_so_it_has_a_call_button(code: str) -> None:
    c = load_country(code)
    assert c is not None
    leads = stops.leads()
    for r in c.rules.rules:
        for lang in r.line.root:
            if (script := stop_script(c, r.id, lang)) in leads:
                assert all(n in script for n in NUMBERS[code] if n in leads[script]), (r.id, lang)

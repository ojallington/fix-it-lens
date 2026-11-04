"""Country files: they load, their IDs follow <country>.<family>.<zone>, and 3 cm is a floor in code."""

from __future__ import annotations

import copy

import pytest
import yaml

from rules.country import RULE_ID, RulesError, check, country_path, load_country, shipped

NL = yaml.safe_load(country_path("nl").read_text())


@pytest.mark.parametrize("code", shipped())
def test_every_shipped_file_loads_and_every_id_matches(code: str) -> None:
    c = load_country(code)
    assert c is not None and c.code == code
    for r in c.rules.rules:
        assert RULE_ID.match(r.id) and r.id == f"{code}.{r.family}.{r.zone}"
        assert r.ring_cm >= 3
        assert r.sources


def test_nl_is_shipped_with_nl_and_en() -> None:
    c = load_country("nl")
    assert c is not None and c.languages == ["nl", "en"]


def test_ring_below_floor_fails_to_load() -> None:
    data = copy.deepcopy(NL)
    data["rules"][0]["ring_cm"] = 2
    with pytest.raises(RulesError):
        check(data, "nl")


def test_ring_below_floor_fails_in_code_even_past_the_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    import rules.country as country

    monkeypatch.setattr(country, "validate", lambda *a, **k: None)
    data = copy.deepcopy(NL)
    data["rules"][0]["ring_cm"] = 2
    with pytest.raises(Exception, match="3"):
        check(data, "nl")


@pytest.mark.parametrize("bad", ["nl.mains", "NL.mains.casing", "nl.mains.casing.x", "nl.power.casing"])
def test_bad_rule_ids_fail(bad: str) -> None:
    data = copy.deepcopy(NL)
    data["rules"][0]["id"] = bad
    with pytest.raises(RulesError):
        check(data, "nl")


def test_id_must_agree_with_family_and_zone() -> None:
    data = copy.deepcopy(NL)
    data["rules"][0]["id"] = "nl.gas.other"
    with pytest.raises(RulesError):
        check(data, "nl")


def test_missing_country_is_none() -> None:
    assert load_country("fr") is None
    assert load_country("../etc") is None


def test_nl_emergency_numbers() -> None:
    c = load_country("nl")
    assert c is not None
    smell = c.rule("nl.gas.smell")
    co = c.rule("nl.co.alarm")
    assert smell and co
    assert "0800-9009" in smell.script.root["nl"] and "0800-9009" in smell.script.root["en"]
    assert "112" in co.script.root["en"] and "0800-9009" not in co.script.root["en"]  # never merged


def test_nl_boiler_allow_list() -> None:
    c = load_country("nl")
    assert c is not None
    assert c.allowed_boiler_actions == {"read_pressure", "top_up", "bleed_radiators", "reset_per_manual",
                                        "power_cycle"}


def test_rules_never_hard_code_a_page() -> None:
    # Pages differ by manual variant; they come from the card.
    for code in shipped():
        data = yaml.safe_load(country_path(code).read_text())
        for r in data["rules"]:
            assert all("page" not in s for s in r["sources"]), r["id"]

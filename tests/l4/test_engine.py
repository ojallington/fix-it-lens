"""The safety engine and check_step: the brief's acceptance tests."""

from __future__ import annotations

import pytest

from contracts.models.card import Card
from contracts.models.safety import Ok, SafetyEvent, Stop
from rules import Evidence, SafetyEngine, SafetyError, StepHold, StepOk, StepStop, check_step, guarded, load_country
from rules.cards import check_card
from tests.l4.conftest import boiler_data

SEEN = Evidence(plug_out_seen=True, view="side", zone_outlined=True, view_scale_ok=True, ring_warning_confirmed=True)


def _step(card: Card, sid: str):  # type: ignore[no-untyped-def]
    return next(s for j in card.jobs for s in j.steps if s.id == sid)


def test_report_hazard_stops_and_no_model_output_clears_it() -> None:
    e = SafetyEngine("nl", "en")
    e.start_job("clean_debris_filter", 0)
    state = e.report_hazard("nl.mains.casing", "user reaching behind the panel", 10)
    assert isinstance(state, Stop) and state.rule_id == "nl.mains.casing"
    for to in (Ok(state="OK"), e.state):
        with pytest.raises(SafetyError):
            e.apply(SafetyEvent(t_ms=20, source="model", kind="clear", to=to, reason="looks fine now"))
    with pytest.raises(SafetyError):
        e.apply(SafetyEvent(t_ms=20, source="model", kind="raise", to=Ok(state="OK"), reason="resume"))
    with pytest.raises(SafetyError):
        e.apply(SafetyEvent(t_ms=20, source="code", kind="clear", to=Ok(state="OK"), reason="code"))
    assert isinstance(e.state, Stop)
    assert e.start_job("clean_debris_filter", 30) == e.state  # the same job does not resume
    assert isinstance(e.state, Stop)


def test_report_hazard_with_no_rule_uses_the_generic_stop() -> None:
    e = SafetyEngine("nl", "nl")
    s = e.report_hazard(None, "sparks seen", 0)
    assert isinstance(s, Stop) and s.rule_id == "nl.mechanical.hazard"
    assert s.script == load_country("nl").rule("nl.mechanical.hazard").script.root["nl"]  # type: ignore[union-attr]
    s2 = SafetyEngine("nl", "en").report_hazard("nl.mains.breaker", "an ALLOW rule cannot be used to stop softly", 0)
    assert isinstance(s2, Stop) and s2.rule_id == "nl.mechanical.hazard"


def test_a_different_job_clears_a_rules_stop_but_an_emergency_stays() -> None:
    e = SafetyEngine("nl", "en")
    e.start_job("a", 0)
    e.report_hazard("nl.mains.casing", "x", 1)
    assert isinstance(e.start_job("b", 2), Ok)
    e.on_utterance("I think I smell gas", 3)
    assert isinstance(e.state, Stop) and e.state.rule_id == "nl.gas.smell"
    assert isinstance(e.start_job("c", 4), Stop)
    e.report_hazard("nl.mains.casing", "a lesser stop does not replace the emergency", 5)
    assert e.state.rule_id == "nl.gas.smell"  # type: ignore[union-attr]


def test_utterances() -> None:
    for text, lang, rid in (("ik ruik gas", "nl", "nl.gas.smell"), ("De CO-melder piept", "nl", "nl.co.alarm"),
                            ("there's smoke coming out", "en", "nl.heat.fire"),
                            ("it keeps tripping", "en", "nl.mains.breaker_repeat")):
        e = SafetyEngine("nl", lang)
        assert isinstance(e.on_utterance(text, 0), Stop) and e.state.rule_id == rid, text  # type: ignore[union-attr]
    # The lexicon trap: "is that the gas tap?" is normal boiler talk, not an emergency.
    e = SafetyEngine("nl", "nl")
    assert isinstance(e.on_utterance("is dat de gaskraan?", 0), Ok)
    assert isinstance(SafetyEngine("nl", "en").on_utterance("is that the gas tap?", 0), Ok)


def test_casing_step_is_stop_whether_the_plug_is_in_or_out(acme: Card) -> None:
    data = acme.model_dump()
    data["jobs"][0]["steps"][0]["text"] = {"en": "Unscrew the back panel to reach the pump."}
    card = Card.model_validate(data)
    step = card.jobs[0].steps[0]
    for ev in (Evidence(plug_out_seen=True, ha_power_w=0), Evidence(plug_out_seen=False, ha_power_w=3.0), SEEN):
        r = check_step(step, card, ev, country="nl")
        assert isinstance(r, StepStop) and r.rule_id == "nl.mains.casing"
    assert check_card(card, "nl")[0]  # and the card does not pass the rules check


def test_power_off_is_evidence_not_a_word(acme: Card) -> None:
    unplug_next = _step(acme, "open_flap")
    assert isinstance(check_step(unplug_next, acme, Evidence(), country="nl"), StepHold)
    assert check_step(unplug_next, acme, Evidence(plug_out_seen=True, ha_power_w=2.1), country="nl") == \
        StepHold("power_off_seen", None, "the camera must see the plug out (and HA read 0 W)")
    assert isinstance(check_step(unplug_next, acme, Evidence(plug_out_seen=True, ha_power_w=float("nan")),
                                 country="nl"), StepHold)
    assert isinstance(check_step(unplug_next, acme, Evidence(plug_out_seen=True), country="nl"), StepOk)
    assert isinstance(check_step(unplug_next, acme, Evidence(plug_out_seen=True, ha_power_w=0), country="nl"), StepOk)


def test_red_zone_step_needs_outline_side_view_scale_and_the_ring_warning(acme: Card) -> None:
    step = _step(acme, "refit_filter")
    assert check_step(step, acme, Evidence(plug_out_seen=True), country="nl").precondition == "zone_outlined"  # type: ignore[union-attr]
    ev = Evidence(plug_out_seen=True, zone_outlined=True, view="front", view_scale_ok=True)
    assert check_step(step, acme, ev, country="nl") == StepHold("side_view", None, "a step near a red zone starts "
                                                                "only from a side view")
    ev = Evidence(plug_out_seen=True, zone_outlined=True, view="side", view_scale_ok=True)
    assert check_step(step, acme, ev, country="nl").precondition == "user_confirm"  # type: ignore[union-attr]
    assert isinstance(check_step(step, acme, SEEN, country="nl"), StepOk)


def test_breaker_passes_once_and_a_second_trip_is_stop() -> None:
    e = SafetyEngine("nl", "en")
    assert isinstance(e.breaker_tripped(0), Ok)
    s = e.breaker_tripped(1)
    assert isinstance(s, Stop) and s.rule_id == "nl.mains.breaker_repeat"


def test_breaker_step_after_two_trips_is_stop(acme: Card) -> None:
    data = acme.model_dump()
    data["jobs"][0]["steps"][0]["id"] = "breaker_on"
    data["jobs"][0]["steps"][0]["text"] = {"en": "Switch the breaker back on."}
    card = Card.model_validate(data)
    step = card.jobs[0].steps[0]
    assert isinstance(check_step(step, card, Evidence(plug_out_seen=True, breaker_trips=1), country="nl"), StepOk)
    r = check_step(step, card, Evidence(plug_out_seen=True, breaker_trips=2), country="nl")
    assert isinstance(r, StepStop) and r.rule_id == "nl.mains.breaker_repeat"


def test_boiler_intent_outside_the_allow_list_is_stop(boiler: Card) -> None:
    r = check_step(_step(boiler, "wipe"), boiler, SEEN, country="nl")
    assert isinstance(r, StepStop) and r.rule_id == "nl.gas.boiler"
    assert isinstance(check_step(_step(boiler, "open_fill_tap"), boiler, SEEN, country="nl"), StepOk)
    failures, _ = check_card(boiler, "nl")
    assert any("clean_burner" in f for f in failures)


def test_boiler_pressure_and_repeat_top_ups(boiler: Card) -> None:
    step = _step(boiler, "open_fill_tap")
    hold = check_step(step, boiler, Evidence(**{**SEEN.__dict__, "pressure_bar": 2.0}), country="nl")
    assert isinstance(hold, StepHold) and hold.rule_id == "nl.pressure.relief_valve"
    stop = check_step(step, boiler, Evidence(**{**SEEN.__dict__, "pressure_bar": 3.1}), country="nl")
    assert isinstance(stop, StepStop) and stop.rule_id == "nl.pressure.relief_valve"
    stop = check_step(step, boiler, Evidence(**{**SEEN.__dict__, "top_ups_12_months": 3}), country="nl")
    assert isinstance(stop, StepStop) and stop.rule_id == "nl.pressure.repeat_top_up"
    assert isinstance(check_step(step, boiler, Evidence(**{**SEEN.__dict__, "top_ups_12_months": 2}),
                                 country="nl"), StepOk)


def test_the_gas_tap_look_only_step_passes_and_its_ring_is_3cm(boiler: Card) -> None:
    assert isinstance(check_step(_step(boiler, "check_gas_open"), boiler, SEEN, country="nl"), StepOk)
    # A card cannot exempt its own line: only the country file's hashes count.
    data = boiler_data()
    edited = "The yellow tap is gas. Check it's open: just turn it."
    data["jobs"][0]["steps"][0]["text"] = {"en": edited}
    data["jobs"][0]["steps"][0]["look_only_lines"] = [edited]
    card = Card.model_validate(data)
    r = check_step(card.jobs[0].steps[0], card, SEEN, country="nl")
    assert isinstance(r, StepStop) and r.rule_id == "nl.gas.tap"
    data = boiler_data()
    data["jobs"][0]["steps"][1]["zone_rings"][0]["ring_cm"] = 2
    with pytest.raises(ValueError):
        Card.model_validate(data)  # the card schema refuses it; check_card says so again in code
    card = Card.model_construct(**Card.model_validate(boiler_data()).__dict__)
    card.jobs[0].steps[1].zone_rings[0].ring_cm = 2  # type: ignore[index]
    assert any("floor" in f for f in check_card(card, "nl")[0])


def test_engine_on_step_holds_then_code_clears_when_seen(acme: Card) -> None:
    e = SafetyEngine("nl", "en", acme)
    step = _step(acme, "open_flap")
    e.on_step(step, Evidence(), 0)
    assert e.state.state == "HOLD"
    e.on_step(step, Evidence(plug_out_seen=True), 1)
    assert isinstance(e.state, Ok)
    assert [ev.source for ev in e.events] == ["rules", "code"]


def test_g1_stop_and_pause_clear_only_by_g1() -> None:
    e = SafetyEngine("nl", "en")
    e.on_g1("stop", "ttc", 0, "nl.gas.tap")
    assert isinstance(e.state, Stop) and "hands back" in e.state.script.lower()
    with pytest.raises(SafetyError):
        e.apply(SafetyEvent(t_ms=1, source="model", kind="clear", to=Ok(state="OK"), reason="x"))
    assert isinstance(e.g1_clear(2), Ok)
    e.on_g1("pause", "hand_lost", 3)
    assert e.state.state == "HOLD" and e.state.precondition == "hand_visible"  # type: ignore[union-attr]
    e.report_hazard("nl.mains.casing", "x", 4)
    with pytest.raises(SafetyError):
        e.g1_clear(5)  # G1 cannot clear a rules stop


def test_guarded(acme: Card, boiler: Card) -> None:
    assert guarded(_step(acme, "unplug"), acme)
    assert guarded(_step(boiler, "check_gas_open"), boiler)
    data = acme.model_dump()
    data["jobs"][0]["steps"][0]["power"] = "any"
    card = Card.model_validate(data)
    assert not guarded(card.jobs[0].steps[0], card)


def test_no_country_file_refuses_gas_and_mains_steps(acme: Card, boiler: Card) -> None:
    assert isinstance(check_step(_step(boiler, "open_fill_tap"), boiler, SEEN, country="fr"), StepStop)
    assert isinstance(check_step(_step(acme, "open_flap"), acme, SEEN, country="fr"), StepStop)

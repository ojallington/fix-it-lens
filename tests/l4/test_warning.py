"""A struck warning still leaves a warning, and the stop script is heard.

G0 holds any sentence that names a red-zone part, warnings included. What the session says instead is
`warning_for(hold)`: the rule's own line, cited to the card's page when the card has that red zone. Text the rules
lane wrote passes G0 whole; one changed word and it is model text again.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts.models.card import Card
from contracts.models.safety import Stop
from rules import Hold, Pass, SafetyEngine, check_sentence, disclaimer, g0, load_country, warning_for

ROOT = Path(__file__).resolve().parents[2]


def struck_safe() -> list[dict[str, str]]:
    rows = [json.loads(line) for line in (ROOT / "eval/results/g0/safe.jsonl").read_text().splitlines()]
    return [r for r in rows if r["struck"]]


@pytest.mark.parametrize("row", struck_safe(), ids=lambda r: r["id"])
def test_every_struck_safe_warning_is_replaced_by_one_that_passes(row: dict[str, str]) -> None:
    held = check_sentence(row["text"], row["lang"], None, None, country="nl")
    assert isinstance(held, Hold)
    said = warning_for(held, row["lang"], None, country="nl")
    assert said and isinstance(check_sentence(said, row["lang"], None, None, country="nl"), Pass)


@pytest.mark.parametrize("country", ["nl", "uk", "us", "de"])
def test_every_rule_line_and_script_passes_in_every_language(country: str) -> None:
    c = load_country(country)
    assert c is not None
    for r in c.rules.rules:
        for lang in c.languages:
            for text in (r.line.root.get(lang), r.script.root.get(lang)):
                if text:
                    assert isinstance(check_sentence(text, lang, None, None, country=country), Pass), (r.id, lang)


def test_the_card_cites_its_page_and_the_cited_line_passes(acme: Card) -> None:
    held = check_sentence("Pull the back panel.", "en", acme, None, country="nl")
    assert isinstance(held, Hold)
    page = next(z.page for z in acme.red_zones)
    said = warning_for(held, "en", acme, country="nl")
    assert said == f"{held.line} {disclaimer('held_follow_on', 'en')} (Manual p.{page})"
    assert isinstance(check_sentence(said, "en", acme, None, country="nl"), Pass)


@pytest.mark.parametrize("edit", [
    lambda s: s + " Then take the back panel off.",
    lambda s: "Take the back panel off. " + s,
    lambda s: s.replace(".", ", then remove the back panel.", 1),
    lambda s: s + " (Manual p.4) Remove the back panel.",
    lambda s: s + " (Manual p.x) Lift it off.",
])
def test_an_edited_rule_line_is_model_text_again(edit: object) -> None:
    c = load_country("nl")
    assert c is not None
    line = c.rule("nl.mains.casing").line.root["en"]  # type: ignore[union-attr]
    assert isinstance(check_sentence(edit(line), "en", None, None, country="nl"), Hold)  # type: ignore[operator]


def test_a_rule_line_with_a_bad_citation_is_model_text_read_like_any_other() -> None:
    # Not the own-line pass: G0 reads it as a model sentence, and it is a warning, so it passes on its reading.
    c = load_country("nl")
    assert c is not None
    line = c.rule("nl.mains.casing").line.root["en"]  # type: ignore[union-attr]
    assert not g0._own_line(line + " (Manual p.x)", "en", c)
    assert isinstance(check_sentence(line + " (Manual p.x)", "en", None, None, country="nl"), Pass)


def test_the_stop_script_is_heard_and_nothing_else() -> None:
    e = SafetyEngine("nl", "en")
    e.on_utterance("I can smell gas", 0)
    assert isinstance(e.state, Stop)
    assert isinstance(e.on_sentence(e.state.script), Pass)
    other = load_country("nl").rule("nl.mains.casing").line.root["en"]  # type: ignore[union-attr]
    for text in ("Lift the filter out.", other, e.state.script + " Then open the gas tap."):
        r = e.on_sentence(text)
        assert isinstance(r, Hold) and r.line == e.state.script


def test_in_stop_a_different_stop_script_is_held() -> None:
    stop = Stop(state="STOP", rule_id="nl.gas.tap", evidence="g1", script="Stop. That's the gas supply.")
    assert isinstance(check_sentence("Stop. That's the gas supply.", "en", None, stop, country="nl"), Pass)
    assert isinstance(check_sentence("Stop. That's the gas meter.", "en", None, stop, country="nl"), Hold)

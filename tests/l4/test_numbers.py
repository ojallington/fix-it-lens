"""Stop and hold wording (polish findings 6 and 7): plain words a user can act on, the numbers in one table."""

from __future__ import annotations

import re

import pytest

from rules import load_country, numbers
from rules.country import shipped


@pytest.mark.parametrize("code", shipped())
def test_every_number_in_the_texts_is_listed_and_every_listed_number_is_used(code: str) -> None:
    assert numbers.found(code) == set(numbers.NUMBERS[code])


def test_numbers_json_is_current() -> None:
    assert numbers.main(["--check"]) == 0


def test_tel_is_the_digits() -> None:
    assert numbers.as_json().count('"tel": "08009009"') == 1


@pytest.mark.parametrize("code", shipped())
def test_no_script_starts_with_stop_the_screen_already_says_it(code: str) -> None:
    c = load_country(code)
    assert c is not None
    for r in c.rules.rules:
        for lang, text in r.script.root.items():
            assert not re.match(r"stop\b[.!:]", text, re.IGNORECASE), (r.id, lang, text)


@pytest.mark.parametrize("code", shipped())
def test_english_reads_as_english(code: str) -> None:
    """No Dutch scheme name used as an English adjective, and no "That's a technician"."""
    for text in numbers.texts(code):
        assert "CO-vrij certified" not in text and "That's a technician" not in text, text
        assert "that's a technician" not in text, text


def test_the_boiler_top_up_sentence_says_it_is_for_a_boiler() -> None:
    for code in shipped():
        c = load_country(code)
        assert c is not None
        for r in c.rules.rules:
            for text in r.script.root.values():
                if "topping up" in text and "hands back" in text:
                    assert "topping up the boiler" in text, (r.id, text)

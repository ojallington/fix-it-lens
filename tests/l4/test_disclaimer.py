"""The disclaimer and AI-disclosure lines: fixed text that discloses the AI and promises nothing."""

from __future__ import annotations

import re

import pytest

from rules import disclaimer
from rules.disclaimer import _texts

BANNED = re.compile(r"\b(safe|safely|guaranteed?|certified|veilig|gegarandeerd|gecertificeerd)\b|own risk|eigen risico"
                    r"|not professional advice|geen professioneel advies", re.IGNORECASE)


@pytest.mark.parametrize("lang", ["en", "nl"])
def test_every_line_exists_in_en_and_nl(lang: str) -> None:
    for kind, texts in _texts().items():
        assert texts.get(lang), (kind, lang)


@pytest.mark.parametrize("lang", ["en", "nl"])
def test_ai_disclosure_comes_first(lang: str) -> None:
    # EU AI Act Art. 50(1): the user is told it is an AI before anything else.
    for kind in ("first_run", "spoken"):
        first = re.split(r"[.!?]", disclaimer(kind, lang))[0]
        assert re.search(r"\bAI\b|AI-", first), (kind, lang)
    assert "18" in disclaimer("first_run", lang)


def test_no_promises_and_no_liability_bait() -> None:
    for kind, texts in _texts().items():
        for lang, text in texts.items():
            assert not BANNED.search(text), (kind, lang, BANNED.search(text))


def test_spoken_line_is_short() -> None:
    # Said once at session start in at most 6 s: at speech speed, about 16 words.
    for lang in ("en", "nl"):
        assert len(disclaimer("spoken", lang).split()) <= 16


def test_step_citation_needs_the_page_and_other_languages_fall_back_to_english() -> None:
    assert disclaimer("step_citation", "nl", page=43) == "Handleiding p.43"
    with pytest.raises(ValueError):
        disclaimer("step_citation", "en")
    assert disclaimer("terms", "de") == disclaimer("terms", "en")


def test_the_phones_json_matches_the_yaml() -> None:
    from rules.disclaimer import main

    assert main(["--check"]) == 0, "run python -m rules.disclaimer"


@pytest.mark.parametrize(("lang", "country"), [("en", "nl"), ("nl", "nl"), ("en", "uk"), ("en", "us"), ("en", "de")])
def test_the_spoken_line_passes_g0(lang: str, country: str) -> None:
    # The session says it through G0 as one line; first_run is fixed UI text and names "casings", so it never goes
    # through G0 (it would be held, as model text naming a casing should be).
    from rules import Pass, check_sentence

    assert isinstance(check_sentence(disclaimer("spoken", lang), lang, None, None, country=country), Pass)

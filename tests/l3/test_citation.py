"""The citation check on the fake Acme manual: 6 of 6 true quotes accepted,
5 of 5 planted bad ones rejected, including a dropped 'not' and a flipped direction."""

from __future__ import annotations

import pytest

from ingest.citation import check_quote, norm, verify, widen

TRUE = [
    (2, "Do not open the casing. Behind the back panel is mains power."),
    (3, "Clean the debris filter every 3 months."),
    (4, "Do not remove the filter while the drum still holds water."),
    (4, "Place a shallow tray under the filter and turn the filter knob anticlockwise."),
    (4, "Screw the filter back in clockwise until the arrow marks line up."),
    (5, "E99: do not try to repair this yourself. Call a technician."),
]

BAD = [
    ("dropped_negation", 4, "Remove the filter while the drum still holds water.", "critical_tokens"),
    ("flipped_direction", 4, "Place a shallow tray under the filter and turn the filter knob clockwise.",
     "critical_tokens"),
    ("changed_number", 3, "Clean the debris filter every 6 months.", "critical_tokens"),
    ("wrong_page", 4, "Clean the debris filter every 3 months.", "low_score"),
    ("too_short", 4, "Rinse the filter.", "too_short"),
]


@pytest.mark.parametrize(("page", "quote"), TRUE)
def test_true_quotes_accepted(acme_pages: list[str], page: int, quote: str) -> None:
    r = check_quote(acme_pages[page - 1], quote)
    assert r.ok, r


@pytest.mark.parametrize(("case", "page", "quote", "reason"), BAD, ids=[b[0] for b in BAD])
def test_planted_bad_quotes_rejected(acme_pages: list[str], case: str, page: int, quote: str, reason: str) -> None:
    r = check_quote(acme_pages[page - 1], quote)
    assert not r.ok
    assert r.reason == reason, r


def test_six_of_six_and_five_of_five(acme_pages: list[str]) -> None:
    accepted = sum(check_quote(acme_pages[p - 1], q).ok for p, q in TRUE)
    rejected = sum(not check_quote(acme_pages[p - 1], q).ok for _, p, q, _ in BAD)
    assert (accepted, rejected) == (6, 5)


def test_fuzzy_alone_would_pass_the_flip(acme_pages: list[str]) -> None:
    """The reason for the critical tokens: the flipped direction still scores >= 90."""
    r = check_quote(acme_pages[3], BAD[1][2])
    assert r.score >= 90 and r.reason == "critical_tokens"
    assert "anticlockwise" in r.page_tokens and "clockwise" in r.quote_tokens


def test_verify_corrects_the_page(acme_pages: list[str]) -> None:
    r = verify(acme_pages, 4, "Clean the debris filter every 3 months.")
    assert r.ok and r.page == 3 and r.corrected_from == 4


def test_verify_never_corrects_a_bad_quote(acme_pages: list[str]) -> None:
    for _, page, quote, _ in BAD:
        if quote.startswith("Clean the debris filter every 3"):
            continue
        assert not verify(acme_pages, page, quote).ok


def test_out_of_range_page(acme_pages: list[str]) -> None:
    r = verify(acme_pages, 99, "Do not use solvents to clean the drum.")
    assert r.ok and r.page == 3 and r.corrected_from == 99


# The real manual's quirks (agent-loop §4.1), on our own Dutch text.
PAGE_NL = (
    "Zeefsysteem reinigen\n"
    "¡ De grove zeef tegen de klok in draaien en het zeefsysteem eruit\nnemen \n ⁠.\n"
    "Let erop dat er geen etensresten in het pomp-\nreservoir vallen.\n"
    "Controleer of de pijlmarkeringen tegenover elkaar staan."
)


def test_dutch_hyphenated_break_and_joiner() -> None:
    assert check_quote(PAGE_NL, "Let erop dat er geen etensresten in het pompreservoir vallen.").ok
    assert check_quote(PAGE_NL, "De grove zeef tegen de klok in draaien en het zeefsysteem eruit nemen.").ok


def test_dutch_planted() -> None:
    assert not check_quote(PAGE_NL, "Let erop dat er etensresten in het pompreservoir vallen.").ok
    assert not check_quote(PAGE_NL, "De grove zeef met de klok mee draaien en het zeefsysteem eruit nemen.").ok


def test_contraction_keeps_the_negation() -> None:
    assert norm("Don't open it") == "do not open it"
    assert not check_quote("Do not open the casing while it runs.", "Do open the casing while it runs.").ok


def test_truncation_that_drops_meaning_is_caught(acme_pages: list[str]) -> None:
    """A true substring can still lie: the critical tokens are compared over the whole sentence."""
    assert not check_quote(acme_pages[3], "Place a shallow tray under the filter and turn the filter knob").ok
    assert not check_quote(acme_pages[1], "open the casing. Behind the back panel is mains power.").ok


def test_a_heading_above_does_not_leak_tokens() -> None:
    page = "Opening the door\nWipe the door seal with a dry cloth after each wash.\n"
    assert check_quote(page, "Wipe the door seal with a dry cloth after each wash.").ok


def test_list_numbers_are_layout_not_content() -> None:
    page = "Inlet filter\n5. Take the filter out of the hose\n6. Clean the filter.\n7. Put the filter back in."
    assert check_quote(page, "Take the filter out of the hose. Clean the filter.").ok
    assert not check_quote(page, "Take the filter out of the hose. Do not clean the filter.").ok
    line = "Wait at least 2 minutes before you plug it in."
    assert check_quote(line, line).ok


def test_figure_callouts_are_layout() -> None:
    page = "8. Fit the pump cover and press down \n ⁠.\n1\n2\na The pump cover clicks into place.\n10.Fit the baskets."
    assert check_quote(page, "Fit the pump cover and press down. The pump cover clicks into place.").ok
    assert check_quote(page, "The pump cover clicks into place. Fit the baskets.").ok


def test_a_page_shorter_than_the_quote_is_not_found_inside_it() -> None:
    quote = "Clean the debris filter every two months."
    for page in ("a", "Clean the debris filter", "two months."):
        r = check_quote(page, quote)
        assert not r.ok and r.score < 90, (page, r)
    assert check_quote("Clean the debris filter every two months", quote).ok  # the whole page is the quote


def test_blank_and_short_pages_never_take_a_quote(acme_pages: list[str]) -> None:
    quote = "Clean the debris filter every 3 months."
    stubs = ["a", "b", "c", "3", "months"]
    r = verify(stubs + [acme_pages[2]], 6, quote)
    assert r.ok and r.page == 6 and r.corrected_from is None
    r = verify(stubs + [acme_pages[2]], 1, quote)  # the real page is still found past five short ones
    assert r.ok and r.page == 6 and r.corrected_from == 1


def test_a_short_quote_is_widened_to_the_manuals_own_sentences_and_checked_the_same() -> None:
    page = ("4 Cleaning the pump filter\n1. Open the flap.\n2. Replace the filter.\n3. Close the flap firmly "
            "until it clicks.\nThe filter must be clean before each use of the pump.")
    assert widen(page, "2. Replace the filter.") == "Replace the filter. Close the flap firmly until it clicks."
    assert check_quote(page, widen(page, "2. Replace the filter.") or "").ok
    long_one = "The filter must be clean"  # already inside a long sentence: that sentence, whole
    assert widen(page, long_one) == "The filter must be clean before each use of the pump."
    # negative controls: a quote not on the page word for word is never widened, and a short last sentence with
    # nothing after it takes the one before
    assert widen(page, "2. Replace the pump.") is None
    assert widen("Rinse the filter under water.\nRefit it.", "Refit it.") == "Rinse the filter under water. Refit it."

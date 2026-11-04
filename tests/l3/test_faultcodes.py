"""Fault codes as the display shows them, on invented page text."""

from __future__ import annotations

from ingest.faultcodes import check, spelled_out

PAGE = ('"E:36 / -10" Afvoerbuis verstopt. "E:36 / -25 / -26" Afvoerpomp verstopt. "E:10 / -00 / -10 /\n-20" '
        "Product vibrates or makes noise. Loading door cannot be opened. Waterafvoer FXXX F-.. 00:00 F10 Err C2 h2o")


def test_the_displays_alternating_form_is_spelled_out() -> None:
    assert spelled_out("E:36 / -25 / -26") == ["E:36-25", "E:36-26"]
    assert spelled_out("E:10 / -00 / -10 /") == ["E:10-00", "E:10-10"]
    assert spelled_out(" F10 ") == ["F10"]


def test_only_codes_the_manual_prints_and_a_display_shows_are_kept() -> None:
    kept, dropped = check(["E:36 / -10", "E:36 / -25 / -26", "F10", "Err C2", "h2o", "F10", "E99"], [PAGE])
    assert kept == ["E:36-10", "E:36-25", "E:36-26", "F10", "Err C2", "h2o"]
    assert dropped == [("E99", "not printed in the manual")]


def test_words_cut_symptoms_placeholders_and_clocks_are_not_codes() -> None:
    _, dropped = check(["Product vibr", "Loading door", "Waterafvoer", "FXXX", "F-..", "00:00"], [PAGE])
    assert [why for _, why in dropped] == ["no digit: not a code"] * 3 + ["a placeholder, not a code"] * 2 + [
        "only zeros: a clock, not a code"]

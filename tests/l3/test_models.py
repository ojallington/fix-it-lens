"""K1: the manual's own model list, and whether it covers a plate's model."""

from __future__ import annotations

from ingest.models import checked, covers


def test_an_exact_model_matches_whatever_its_spacing_or_production_suffix() -> None:
    assert covers(["SMV4HAX40N"], "SMV4HAX40N/17") == "exact"
    assert covers(["G 7793"], "g7793") == "exact"
    assert covers(["SMV4HAX40N"], "SMV4HAX41N") is None


def test_the_manuals_own_pattern_matches_and_needs_three_printed_characters() -> None:
    assert covers(["WMN..."], "WMN16T3471/20") == "pattern"
    assert covers(["SMV4HA*"], "SMV4HAX40N") == "pattern"
    assert covers(["WMN..."], "WAU28S01NL") is None
    assert covers(["W..."], "WAU28S01NL") is None


def test_a_family_name_is_not_a_model_and_never_matches() -> None:
    assert checked([{"code": "Xtreme", "page": 1}], ["Intergas Xtreme"]) == []
    assert covers(["Xtreme"], "Xtreme 36") is None


def test_a_listed_code_counts_only_where_the_manual_prints_it() -> None:
    pages = ["Wasmachine WMN...", "Model SMV4HAX40N"]
    listed = [{"code": "WMN...", "page": 1}, {"code": "SMV4HAX40N", "page": 1}, {"code": "ZZZ999", "page": 1}]
    assert checked(listed, pages) == ["WMN...", "SMV4HAX40N"]  # the second moved to the page that prints it

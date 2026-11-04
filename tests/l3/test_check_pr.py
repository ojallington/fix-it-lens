"""make check-pr: a card checks against the contributor's PDF with no key, and CI checks the record without the PDF."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import yaml

from ingest import check_pr, flow
from tests.l3.test_appliance_file import DRAFT
from tests.l3.test_flow import _client, private  # noqa: F401  (fixture)


async def _acme_card(tmp_path: Path, acme_pdf: Path) -> Path:
    good = copy.deepcopy(DRAFT)
    good["jobs"] = good["jobs"][:1]
    out = await flow.add_appliance("acme", "WM-0000", pdf=acme_pdf.read_bytes(),
                                   client=_client(tmp_path / "fx", acme_pdf.read_bytes(), good))
    assert isinstance(out, flow.CardReady)
    return out.path


def _edit(card_file: Path, change: object) -> None:
    card = yaml.safe_load(card_file.read_text())
    change(card)  # type: ignore[operator]
    card_file.write_text(yaml.safe_dump(card, allow_unicode=True, sort_keys=False))


async def test_local_check_writes_a_record_ci_accepts(private: Path, tmp_path: Path, acme_pdf: Path) -> None:  # noqa: F811
    card = await _acme_card(tmp_path, acme_pdf)
    assert check_pr.main([str(card), "--pdf", str(acme_pdf)]) == 0
    record = json.loads(check_pr.record_path(card).read_text())
    assert set(record) == {"record_version", "card_sha256", "pdf_sha256", "page_text_sha256", "quotes"}  # no text
    assert check_pr.main([str(card), "--ci"]) == 0


async def test_ci_fails_without_a_record(private: Path, tmp_path: Path, acme_pdf: Path) -> None:  # noqa: F811
    card = await _acme_card(tmp_path, acme_pdf)
    check_pr.record_path(card).unlink()  # the build wrote one; a card without it
    assert any("no check record" in e for e in check_pr.check_ci(card))


async def test_a_quote_edited_after_the_check_fails(private: Path, tmp_path: Path, acme_pdf: Path) -> None:  # noqa: F811
    card = await _acme_card(tmp_path, acme_pdf)
    assert check_pr.main([str(card), "--pdf", str(acme_pdf)]) == 0

    def flip(c: dict) -> None:  # type: ignore[type-arg]
        q = c["jobs"][0]["steps"][0]["quote"]
        q["text"] = "Never " + q["text"]

    _edit(card, flip)
    assert any("not in the check record" in e for e in check_pr.check_ci(card))
    errs, _ = check_pr.check_local(card, acme_pdf)
    assert any("quote not on page" in e for e in errs)


async def test_another_pdf_fails_the_hashes(private: Path, tmp_path: Path, acme_pdf: Path) -> None:  # noqa: F811
    card = await _acme_card(tmp_path, acme_pdf)
    _edit(card, lambda c: c["manual"]["page_text_sha256"].__setitem__(0, "0" * 64))
    errs, _ = check_pr.check_local(card, acme_pdf)
    assert any("does not match the card" in e for e in errs)


async def test_a_long_quote_fails_structure(private: Path, tmp_path: Path, acme_pdf: Path) -> None:  # noqa: F811
    card = await _acme_card(tmp_path, acme_pdf)
    five = "Open the door. Lift the filter. Rinse it well. Put it back. Close the door."
    _edit(card, lambda c: c["jobs"][0]["steps"][0]["quote"].__setitem__("text", five))
    assert any("at most 2 sentences" in e for e in check_pr.structure_errors(yaml.safe_load(card.read_text())))


async def test_a_three_sentence_step_quote_fails_against_its_page(private: Path, tmp_path: Path,  # noqa: F811
                                                                  acme_pdf: Path) -> None:  # noqa: F811
    card = await _acme_card(tmp_path, acme_pdf)
    three = "Open the door. Lift the filter. Rinse it well."  # no warning box: three is over the cap
    _edit(card, lambda c: c["jobs"][0]["steps"][0]["quote"].__setitem__("text", three))
    assert not any("sentences" in e for e in check_pr.structure_errors(yaml.safe_load(card.read_text())))
    errs, _ = check_pr.check_local(card, acme_pdf)
    assert any("unless it is a whole warning box" in e for e in errs)


async def test_an_edited_card_cannot_ride_an_old_record(private: Path, tmp_path: Path, acme_pdf: Path) -> None:  # noqa: F811
    card = await _acme_card(tmp_path, acme_pdf)
    assert check_pr.main([str(card), "--pdf", str(acme_pdf)]) == 0
    _edit(card, lambda c: c["jobs"][0]["steps"][0]["done_when"][0]["text"].__setitem__("en", "Anything at all"))
    assert any("another version of this card" in e for e in check_pr.check_ci(card))

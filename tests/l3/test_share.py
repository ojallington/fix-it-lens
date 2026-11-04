"""K1 and K5: a household's card is its own until shared; only a fetched manual whose own model list covers the
model (or a reviewer's link) is shared, never an upload or a second version; Samsung is refused before anything."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from ingest import flow, lookup
from ingest.cards import read_card
from tests.l3.test_appliance_file import DRAFT
from tests.l3.test_flow import _client, private  # noqa: F401  (fixture)

MAKER_URL = "https://www.bosch-home.nl/acme-wm-0000.pdf"  # a maker's domain; fetch_pdf is faked below


def _draft(*codes: str) -> dict[str, Any]:
    d = copy.deepcopy(DRAFT)
    d["jobs"] = d["jobs"][:1]
    d["models_listed"] = [{"code": c, "page": 1, "quote": "ACME WM-0000 WASHING MACHINE"} for c in codes]
    return d


async def _build(tmp_path: Path, pdf: bytes, draft: dict[str, Any], household: str, *, fetched: bool,
                 monkeypatch: pytest.MonkeyPatch, model: str = "WM-0000") -> flow.CardReady:
    monkeypatch.setattr(lookup, "fetch_pdf", lambda url: pdf)
    out = await flow.add_appliance("acme", model, household=household,
                                   client=_client(tmp_path / "fx" / household, pdf, draft),
                                   **({"pdf_url": MAKER_URL} if fetched else {"pdf": pdf}))
    assert isinstance(out, flow.CardReady), out
    return out


async def test_samsung_is_refused_before_anything(private: Path) -> None:  # noqa: F811
    out = await flow.add_appliance("Samsung", "WW11DG5B25ABEU", pdf=b"%PDF-1.4 a manual")
    assert isinstance(out, flow.Refused) and out.notice == "lookup_failed" and out.code == "maker_terms"
    assert not any(private.rglob("*"))  # no upload written, no card
    ing = await flow.ingest_appliance(brand="samsung", model="WW11DG5B25ABEU", household="h1")
    assert ing.status == "failed" and ing.notice is not None
    assert (ing.notice["kind"], ing.notice["reason"]) == ("lookup_failed", "maker_terms")
    assert ing.notice["text"]["en"] == flow.MAKER_TERMS["en"]


async def test_an_upload_is_the_households_own_and_never_shared(private: Path, tmp_path: Path,  # noqa: F811
                                                                  acme_pdf: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = acme_pdf.read_bytes()
    out = await _build(tmp_path, pdf, _draft("WM-0000"), "h1", fetched=False, monkeypatch=monkeypatch)
    assert out.path == private / "cards" / "homes" / "h1" / "acme" / "WM-0000.yaml"
    assert (private / "manuals" / "homes" / "h1" / "acme" / "WM-0000.pdf").read_bytes() == pdf
    assert not (private / "manuals" / "acme").exists()
    manual = out.card["manual"]
    assert manual["source"] == "household_upload"
    assert manual["source_url"] == f"urn:fixit:upload:{hashlib.sha256(pdf).hexdigest()}"  # never a local path
    assert (manual["models"], manual["models_page"]) == (["WM-0000"], 1)
    assert (out.origin, out.covers) == ("uploaded", "exact")
    assert flow.find_card("acme", "WM-0000", household="h2") is None  # another home never sees it
    assert flow.find_card("acme", "WM-0000", household="h1") == out.path
    assert flow.share_card(out.path, reviewed_by="a reviewer") == flow.NotShared(
        "uploaded", "a household's own upload is never shared")


async def test_a_fetched_manual_that_lists_the_model_is_shared(private: Path, tmp_path: Path,  # noqa: F811
                                                                acme_pdf: Path,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = acme_pdf.read_bytes()
    out = await _build(tmp_path, pdf, _draft("WM-0000", "Acme"), "h1", fetched=True, monkeypatch=monkeypatch)
    assert (out.card["manual"]["source"], out.card["manual"]["source_url"]) == ("maker_site", MAKER_URL)
    assert out.card["manual"]["models"] == ["WM-0000"]  # the maker's name is not a model code
    shared = flow.share_card(out.path)
    assert shared == flow.Shared(private / "cards" / "acme" / "WM-0000.yaml")
    assert (private / "manuals" / "acme" / "WM-0000.pdf").read_bytes() == pdf  # the pages it cites go with it
    assert flow.find_card("acme", "WM-0000", household="h2") == shared.path
    assert flow.share_card(out.path) == shared  # the same version again: nothing changes

    other = pdf + b"\n%another printing\n"  # the same model, a different file: a second version
    theirs = await _build(tmp_path, other, _draft("WM-0000"), "h3", fetched=True, monkeypatch=monkeypatch)
    sha12 = hashlib.sha256(pdf).hexdigest()[:12]
    assert flow.share_card(theirs.path, reviewed_by="a reviewer") == flow.NotShared(
        "version_conflict", f"shared card is {sha12}")
    replaced = flow.replace_shared(theirs.path, reviewed_by="a reviewer")
    assert isinstance(replaced, flow.Shared) and replaced.path == shared.path
    assert replaced.archived == private / "cards" / "acme" / "archive" / f"WM-0000.{sha12}.yaml"
    assert (private / "manuals" / "acme" / "archive" / f"WM-0000.{sha12}.pdf").read_bytes() == pdf
    assert read_card(shared.path)["manual"]["pdf_sha256"] == hashlib.sha256(other).hexdigest()
    assert flow.find_card("acme", "WM-0000") == shared.path  # never the archived one


async def test_a_reviewed_rebuild_of_the_same_manual_replaces_the_shared_card(private: Path, tmp_path: Path,  # noqa: F811
                                                                              acme_pdf: Path,
                                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = acme_pdf.read_bytes()
    old = await _build(tmp_path, pdf, _draft("WM-0000"), "h1", fetched=True, monkeypatch=monkeypatch)
    shared = flow.share_card(old.path)
    assert isinstance(shared, flow.Shared)
    d = _draft("WM-0000")
    d["jobs"][0]["steps"][0]["text_en"] = "Open the filter flap first."  # a newer build: the same PDF, another card
    new = await _build(tmp_path, pdf, d, "h2", fetched=True, monkeypatch=monkeypatch)
    assert new.path.read_bytes() != shared.path.read_bytes()
    assert flow.replace_shared(new.path, reviewed_by="  ") == flow.NotShared(
        "version_conflict", "a replacement needs a reviewer")
    replaced = flow.replace_shared(new.path, reviewed_by="a reviewer")
    sha12 = hashlib.sha256(old.path.read_bytes()).hexdigest()[:12]  # the old card's own digest: the PDF is the same
    assert replaced == flow.Shared(shared.path, private / "cards" / "acme" / "archive" / f"WM-0000.{sha12}.yaml")
    assert shared.path.read_bytes() == new.path.read_bytes()
    assert replaced.archived is not None and replaced.archived.read_bytes() == old.path.read_bytes()
    assert (private / "manuals" / "acme" / "WM-0000.pdf").read_bytes() == pdf  # both versions cite this one
    assert not (private / "manuals" / "acme" / "archive").exists()
    assert flow.replace_shared(new.path, reviewed_by="a reviewer") == flow.Shared(shared.path)  # same card: no-op


async def test_a_manual_with_no_model_codes_is_shared_only_by_a_reviewer(private: Path, tmp_path: Path,  # noqa: F811
                                                                         acme_pdf: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    out = await _build(tmp_path, acme_pdf.read_bytes(), _draft("Acme"), "h1", fetched=True, monkeypatch=monkeypatch)
    assert out.card["manual"]["models"] == [] and "models_page" not in out.card["manual"]
    assert (out.covers, out.covers_reason) == (None, "no_model_list")
    assert flow.share_card(out.path) == flow.NotShared("no_model_list", "the manual prints no model codes")
    assert isinstance(flow.share_card(out.path, reviewed_by="a reviewer"), flow.Shared)
    # shared by review under its own model: still never linked to another model
    assert flow.find_card("acme", "WM-0001", household="h2") is None


async def test_a_shared_cards_own_pattern_links_another_model(private: Path, tmp_path: Path,  # noqa: F811
                                                              acme_pdf: Path,
                                                              monkeypatch: pytest.MonkeyPatch) -> None:
    out = await _build(tmp_path, acme_pdf.read_bytes(), _draft("WM-0000", "WM-0..."), "h1", fetched=True,
                       monkeypatch=monkeypatch)
    shared = flow.share_card(out.path)
    assert isinstance(shared, flow.Shared)
    assert flow.find_card("acme", "WM-0123", household="h2") == shared.path
    assert flow.find_card("acme", "WX-0123", household="h2") is None


def test_a_household_id_is_never_a_path() -> None:
    with pytest.raises(ValueError):
        flow.pages.home_dir(Path("/x"), "../h1")


async def test_a_drawn_code_is_kept_recorded_and_shared_only_once_a_person_confirms_it(
        private: Path, tmp_path: Path, acme_pdf: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from ingest import check_pr
    from tests.l3.test_appliance_file import _row
    from tools import confirm_codes
    d = _draft("WM-0000")
    # the fake manual prints E21 as text; "E7" stands for a code drawn as a display picture beside that row
    d["troubleshooting"] = [_row("e7", ["E7"], "E21: the water does not drain. Clean the debris filter."),
                            _row("long", ["ERR12"], "E21: the water does not drain. Clean the debris filter.")]
    out = await _build(tmp_path, acme_pdf.read_bytes(), d, "h1", fetched=True, monkeypatch=monkeypatch)
    rows = {r["id"]: r for r in out.card["troubleshooting"]}
    assert rows["e7"]["codes"] == ["E7"] and "long" not in rows  # ERR12 has no display shape: dropped, row with it
    rec = json.loads(check_pr.record_path(out.path).read_text())
    assert rec["drawn_codes"] == [{"row": "e7", "code": "E7", "page": 5, "meaning": "The water does not drain.",
                                   "confirmed_by": None, "confirmed_at": None}]
    assert check_pr.unconfirmed(rec) == ["E7"]
    assert flow.share_card(out.path) == flow.NotShared(
        "codes_unconfirmed", "drawn codes not yet confirmed by a person: E7")

    answers = iter(["a person", "y"])
    monkeypatch.setattr(confirm_codes, "page_png", lambda card, hh, page: tmp_path / f"p{page}.png")
    assert confirm_codes.confirm(out.path, ask=lambda _: next(answers), show=False) == 0
    rec = json.loads(check_pr.record_path(out.path).read_text())
    assert rec["drawn_codes"][0]["confirmed_by"] == "a person" and check_pr.unconfirmed(rec) == []
    # a rebuild of the same manual keeps the answer: the person confirmed the PDF's drawing, not one card version
    d["troubleshooting"][1]["id"] = "other"
    again = await _build(tmp_path, acme_pdf.read_bytes(), d, "h1", fetched=True, monkeypatch=monkeypatch)
    rec = json.loads(check_pr.record_path(again.path).read_text())
    assert rec["drawn_codes"][0]["confirmed_by"] == "a person"
    assert isinstance(flow.share_card(out.path), flow.Shared)


def test_confirming_codes_needs_a_person_at_a_terminal(tmp_path: Path) -> None:
    from tools import confirm_codes
    assert confirm_codes.main([str(tmp_path / "card.yaml")]) == 2  # pytest's stdin is not a terminal


async def test_a_card_with_a_step_of_ours_is_shared_only_once_a_person_accepts_it(
        private: Path, tmp_path: Path, acme_pdf: Path, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from ingest import check_pr
    from tests.l3.test_appliance_file import UNPLUG
    from tools import confirm_codes
    # the filter job unplugs the machine and the fake manual never plugs it back in
    d = _draft("WM-0000")
    d["jobs"][0]["steps"].insert(0, copy.deepcopy(UNPLUG))
    out = await _build(tmp_path, acme_pdf.read_bytes(), d, "h1", fetched=True, monkeypatch=monkeypatch)
    rec = json.loads(check_pr.record_path(out.path).read_text())
    assert [(d["step"], d["text"], d["accepted_by"]) for d in rec["fixit_steps"]] == [
        ("clean_debris_filter_restore_mains", "Plug the appliance back in.", None)]
    assert flow.share_card(out.path) == flow.NotShared(
        "steps_unaccepted", "our own steps not yet accepted by a person: "
        "clean_debris_filter/clean_debris_filter_restore_mains")

    answers = iter(["a person", "n"])  # a no keeps it private
    assert confirm_codes.confirm(out.path, ask=lambda _: next(answers), show=False) == 0
    assert isinstance(flow.share_card(out.path), flow.NotShared)
    rec = json.loads(check_pr.record_path(out.path).read_text())
    for d in rec["fixit_steps"]:
        d.pop("rejected_by"), d.pop("accepted_at")
    check_pr.record_path(out.path).write_text(json.dumps(rec))
    answers, asked = iter(["a person", "y"]), []
    assert confirm_codes.confirm(out.path, ask=lambda q: asked.append(q) or next(answers), show=False) == 0
    assert asked[-1] == 'Add our own step at the end, "Plug the appliance back in."? [y/n] '
    assert json.loads(check_pr.record_path(out.path).read_text())["fixit_steps"][0]["accepted_by"] == "a person"
    assert isinstance(flow.share_card(out.path), flow.Shared)


def test_the_prompt_for_a_step_of_ours_says_where_the_card_puts_it() -> None:
    from tools import confirm_codes

    def step(sid: str, en: str, ours: bool = False) -> dict[str, Any]:
        return {"id": sid, "text": {"en": en}, **({"origin": "fixit"} if ours else {})}

    card = {"jobs": [{"id": "pump", "steps": [
        step("close_tap", "Close the water tap."), step("unplug", "Unplug the appliance."),
        step("clean", "Clean the pump."), step("tap_on", "Turn the water tap back on.", True),
        step("plug_in", "Plug the appliance back in.", True), step("drain", "Start the Drain programme.")]},
        {"id": "drawer", "steps": [step("unplug", "Unplug the appliance."), step("wipe", "Wipe the drawer."),
                                   step("plug_in", "Plug the appliance back in.", True)]}]}
    before = "just before the manual's step \"Start the Drain programme.\""
    assert confirm_codes.placed(card, "pump", "tap_on") == before  # our next step is skipped: the manual's comes
    assert confirm_codes.placed(card, "pump", "plug_in") == before
    assert confirm_codes.placed(card, "drawer", "plug_in") == "at the end"

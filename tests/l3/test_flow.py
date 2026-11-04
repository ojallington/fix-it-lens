"""The failure paths: no lookup → ask for the PDF (with the maker's page); a failed ingest → plain pages, said."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path

import pytest

import calls
from calls.testing import responses_events, write_respond_fixture
from ingest import appliance_file as af
from ingest import flow, lookup
from ingest.pages import extract
from tests.l3.test_appliance_file import DRAFT
from tests.l3.test_lookup import BOSCH_ROBOTS, FakeWeb


@pytest.fixture
def private(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    p = tmp_path / "private"
    p.mkdir()
    monkeypatch.setenv("FIXIT_PRIVATE", str(p))
    lookup._robots.clear()
    return p


def _client(root: Path, pdf: bytes, draft: dict[str, object]) -> calls.Client:
    client = calls.make_client("replay", fixtures_root=root, private=None)
    body = client.build_respond_body("appliance_file", af.request_input(pdf, "WM-0000.pdf"),
                                     instructions=af.INSTRUCTIONS, format=af.FILE_FORMAT)
    write_respond_fixture(root, body, responses_events(text=json.dumps(draft), model="muse-wonder-1.0"))
    return client


async def test_a_repo_card_loads_without_any_call(private: Path) -> None:
    out = await flow.add_appliance("bosch", "SMV4HAX40N", fetch=FakeWeb({}))
    assert isinstance(out, flow.CardReady) and not out.built_now
    assert out.rail.startswith("muse-wonder: manual → ")


async def test_alias_finds_the_card(private: Path) -> None:
    out = await flow.add_appliance("bosch", "SMV4HAX40N/17", fetch=FakeWeb({}))
    assert isinstance(out, flow.CardReady)


async def test_lookup_miss_asks_for_the_pdf_with_the_makers_page(private: Path) -> None:
    web = FakeWeb({"https://www.bosch-home.nl/robots.txt": (200, BOSCH_ROBOTS)})
    out = await flow.add_appliance("bosch", "SMS00XX00E/13", fetch=web)
    assert isinstance(out, flow.NeedPdf) and out.notice == "lookup_failed" and out.reason == "robots"
    assert out.maker_page == "https://www.bosch-home.nl/supportdetail/product/SMS00XX00E/13"
    assert "Open Bosch's page, copy the link" in out.text["en"] and "share" not in out.text["en"].lower()


async def test_uploaded_pdf_builds_a_private_card(private: Path, tmp_path: Path, acme_pdf: Path) -> None:
    pdf = acme_pdf.read_bytes()
    good = copy.deepcopy(DRAFT)
    good["jobs"] = good["jobs"][:1]
    client = _client(tmp_path / "fx", pdf, good)
    out = await flow.add_appliance("acme", "WM-0000", pdf=pdf, client=client)
    assert isinstance(out, flow.CardReady) and out.built_now
    assert out.path.is_relative_to(private / "cards")
    assert (private / "manuals" / "acme" / "WM-0000.pdf").read_bytes() == pdf
    # its check record, as `make check-pr` writes it: what the server needs to treat a private card as checked
    from ingest import check_pr, pages
    rec = json.loads(check_pr.record_path(out.path).read_text())
    assert check_pr.check_ci(out.path) == []
    assert rec["pdf_sha256"] == pages.file_sha256(private / "manuals" / "acme" / "WM-0000.pdf")


async def test_failed_ingest_keeps_plain_pages_and_says_so(private: Path, tmp_path: Path, acme_pdf: Path) -> None:
    pdf = acme_pdf.read_bytes()
    d = copy.deepcopy(DRAFT)
    d["jobs"] = d["jobs"][1:]
    client = _client(tmp_path / "fx", pdf, d)
    fx = tmp_path / "fx"
    failed = [("jobs[clean_drum].steps[solvent]", d["jobs"][0]["steps"][0])]
    rb = client.build_respond_body("appliance_file", af.repair_input(failed, [p.text for p in extract(acme_pdf)]),
                                   instructions=af.REPAIR_INSTRUCTIONS, format=af.REPAIR_FORMAT, effort="low")
    write_respond_fixture(fx, rb, responses_events(text='{"fixes": []}', model="muse-wonder-1.0"))
    out = await flow.add_appliance("acme", "WM-0000", pdf=pdf, client=client)
    assert isinstance(out, flow.PlainPages) and out.notice == "ingest_failed"
    assert out.page_count == 6
    assert "couldn't build a checked card" in out.text["en"]
    assert not (private / "cards").exists()
    assert (private / "manuals" / "acme" / "WM-0000.pdf").exists()
    assert not any((private / "manuals" / "acme" / ".incoming").iterdir())  # no copy left behind

    # a second home's failed upload of the same model never replaces the manual the first home's pages come from
    out = await flow.add_appliance("acme", "WM-0000", pdf=pdf + b"\n%another upload", client=client)
    assert isinstance(out, flow.PlainPages)
    assert (private / "manuals" / "acme" / "WM-0000.pdf").read_bytes() == pdf


class _Refusing:
    """A ledger with no room in the home's daily cap: every call is refused before it is sent."""

    def check(self, lane: str, estimate_usd: float, **kw: object) -> None:
        raise calls.BudgetExceeded(f"estimate ${estimate_usd:.4f} > daily cap")


async def test_a_build_refused_by_the_cap_keeps_the_pages_and_says_cap_reached(private: Path,
                                                                               acme_pdf: Path) -> None:
    from app.adding import notice

    client = calls.make_client("replay", private=None)
    client.ledger = _Refusing()  # type: ignore[assignment]
    out = await flow.ingest_appliance(brand="acme", model="WM-0000", pdf=acme_pdf.read_bytes(), client=client)
    assert out.status == "pages_only" and not client.requests  # nothing was sent
    assert out.notice is not None and (out.notice["kind"], out.notice["reason"]) == ("ingest_failed", "cap_reached")
    assert (private / "manuals" / "acme" / "WM-0000.pdf").exists()  # its pages are kept to read
    shown = notice(out.notice["kind"], reason=out.notice["reason"])["text"]
    assert "budget" in shown["en"] and "couldn't make the repair card" not in shown["en"]
    assert "tomorrow" not in shown["en"]  # a manual over the cap on its own is over it tomorrow too


async def test_an_add_never_stalls_the_servers_loop(private: Path, acme_pdf: Path,
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    """The maker's site, the PDF and its pages are slow, blocking work. On the loop they stop every /ws pong, and the
    phone calls a socket silent for 5 s dropped: an add in one home showed the household's phones offline."""
    import asyncio
    import time

    from ingest import pages

    def slow(out: object) -> Callable[..., object]:
        def run(*args: object, **kw: object) -> object:
            time.sleep(0.4)
            return out
        return run

    ref = lookup.ManualRef("acme", "WM-0000", "https://acme.test/wm.pdf", "en", "https://acme.test/wm")
    monkeypatch.setattr(lookup, "lookup", slow(ref))
    monkeypatch.setattr(lookup, "fetch_pdf", slow(acme_pdf.read_bytes()))
    load = pages.load
    monkeypatch.setattr(pages, "load", lambda p: (time.sleep(0.4), load(p))[1])
    client = calls.make_client("replay", private=None)
    client.ledger = _Refusing()  # type: ignore[assignment]

    stalls: list[float] = []

    async def beat() -> None:
        while True:
            t = time.perf_counter()
            await asyncio.sleep(0.02)
            stalls.append(time.perf_counter() - t)

    probe = asyncio.create_task(beat())
    out = await flow.add_appliance("acme", "WM-0000", client=client)
    probe.cancel()
    assert isinstance(out, flow.PlainPages) and out.code == "cap_reached"
    assert max(stalls) < 0.3, f"the loop stalled {max(stalls):.2f} s"


async def test_a_model_typed_in_lower_case_finds_its_card(private: Path) -> None:
    assert flow.find_card("bosch", "smv4hax40n") == flow.find_card("bosch", "SMV4HAX40N") is not None


@pytest.mark.parametrize("typed", ["SMV4HAX40N/39", "SMV4HAX40N/17", "smv4hax40n / 39", "SMV4HAX40N /3"])
async def test_a_bosch_e_nr_with_its_production_suffix_finds_the_models_card(private: Path, typed: str) -> None:
    card = flow.find_card("bosch", "SMV4HAX40N")
    assert card is not None and flow.find_card("bosch", typed) == card


async def test_a_typed_e_nr_links_the_card_and_builds_nothing(private: Path) -> None:
    def no_lookup(*args: object, **kw: object) -> lookup.Ref:
        raise AssertionError("looked the manual up")

    client = calls.make_client("replay", private=private)  # a build would need a call, and replay has none
    out = await flow.add_appliance("bosch", "SMV4HAX40N/39", client=client, fetch=no_lookup)
    assert isinstance(out, flow.CardReady) and not out.built_now
    assert out.path == flow.find_card("bosch", "SMV4HAX40N") and out.covers is None
    assert not client.requests


def test_only_a_bsh_production_suffix_is_dropped() -> None:
    assert flow.variant_base("Siemens", "SN23EC14CE/12") == "SN23EC14CE"
    assert flow.variant_base("bosch", "SMV4HAX40N") is None
    assert flow.variant_base("bosch", "SMV4HAX40N/ABC") is None  # a suffix is a production number
    assert flow.variant_base("miele", "G7100/39") is None  # other makers' slashes are part of the model
    assert flow.find_card("acme", "WM-0000/39") is None


async def test_a_file_that_is_not_a_pdf(private: Path) -> None:
    out = await flow.add_appliance("acme", "WM-0000", pdf=b"<html>")
    assert isinstance(out, flow.NeedPdf) and (out.notice, out.code) == ("ingest_failed", "not_a_manual")
    assert "isn't a PDF" in out.text["en"] and "geen pdf" in out.text["nl"]


async def test_a_file_over_the_upload_cap_is_never_read(private: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(flow, "MAX_PDF_BYTES", 10)
    out = await flow.add_appliance("acme", "WM-0000", pdf=b"%PDF-1.7 and more")
    assert isinstance(out, flow.NeedPdf) and out.code == "too_large" and "50 MB" in out.text["en"]
    assert not (private / "manuals").exists()


async def test_an_add_that_builds_nothing_keeps_the_pages_and_calls_no_model(private: Path, acme_pdf: Path,
                                                                            monkeypatch: pytest.MonkeyPatch) -> None:
    ref = lookup.ManualRef("acme", "WM-0000", "https://acme.test/wm.pdf", "en", "https://acme.test/wm")
    monkeypatch.setattr(lookup, "lookup", lambda brand, model: ref)
    monkeypatch.setattr(lookup, "fetch_pdf", lambda url: acme_pdf.read_bytes())
    client = calls.make_client("replay", private=None)
    client.ledger = _Refusing()  # type: ignore[assignment]  # any call at all would be refused, and fail below
    out = await flow.ingest_appliance(brand="acme", model="WM-0000", client=client, build=False)
    assert out.status == "pages_only" and out.notice is None and not client.requests
    assert (private / "manuals" / "acme" / "WM-0000.pdf").exists()


async def test_an_add_that_builds_nothing_still_links_the_librarys_card(private: Path) -> None:
    out = await flow.ingest_appliance(brand="bosch", model="SMV4HAX40N/39", build=False, fetch=FakeWeb({}))
    assert out.status == "checked" and out.card_path == flow.find_card("bosch", "SMV4HAX40N")

"""POST /appliances' one call: plate photo or typed model → lookup → ingest → card, with the contract's card_status."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import calls
from calls.testing import responses_events, write_respond_fixture
from ingest import flow
from ingest import plate as pl
from ingest import progress as progress_mod
from tests.l3.test_appliance_file import DRAFT
from tests.l3.test_flow import _client, private  # noqa: F401  (fixture)
from tests.l3.test_lookup import BOSCH_ROBOTS, FakeWeb
from tests.l3.test_plate import _photo, _reply


def _plate_client(root: Path, reply: dict[str, Any]) -> tuple[calls.Client, bytes]:
    client = calls.make_client("replay", fixtures_root=root, private=None)
    photo = _photo(90)
    body = client.build_respond_body("plate", pl.PLATE_PROMPT, images=[photo], format=pl.PLATE_FORMAT)
    write_respond_fixture(root, body, responses_events(text=json.dumps(reply)))
    return client, photo


async def test_typed_model_with_a_card_is_checked_at_once(private: Path) -> None:  # noqa: F811
    seen: list[str] = []
    out = await flow.ingest_appliance(kind="dishwasher", brand="Bosch", model="SMV4HAX40N",
                                      on_progress=lambda s, d: seen.append(s), fetch=FakeWeb({}))
    assert (out.status, out.stage, out.brand, out.plate_confirmed) == ("checked", "done", "bosch", True)
    assert out.card_path is not None and out.card_path.name == "SMV4HAX40N.yaml"
    assert seen == ["done"]


async def test_plate_with_a_known_model_reads_through(private: Path, tmp_path: Path) -> None:  # noqa: F811
    client, photo = _plate_client(tmp_path, _reply("BOSCH", "SMV4HAX4ON"))  # O for 0: the known card settles it
    out = await flow.ingest_appliance(kind="dishwasher", plate_jpeg=photo, client=client, fetch=FakeWeb({}))
    assert (out.status, out.model) == ("checked", "SMV4HAX40N")
    assert out.plate is not None and out.plate.reason == "ok"


async def test_ambiguous_plate_waits_for_a_tap(private: Path, tmp_path: Path) -> None:  # noqa: F811
    client, photo = _plate_client(tmp_path, _reply("Bosch", "SMS6ZCO0E"))
    out = await flow.ingest_appliance(kind="dishwasher", plate_jpeg=photo, client=client)
    assert (out.status, out.stage, out.plate_confirmed) == ("pending", "plate", False)
    assert out.plate is not None and out.plate.amber_chars


async def test_unreadable_plate_asks_for_a_closer_photo(private: Path, tmp_path: Path) -> None:  # noqa: F811
    client, photo = _plate_client(tmp_path, _reply("Bosch", "??"))
    out = await flow.ingest_appliance(kind="dishwasher", plate_jpeg=photo, client=client)
    assert (out.status, out.stage, out.model) == ("pending", "plate", None)


async def test_no_model_and_no_photo_says_what_to_do(private: Path) -> None:  # noqa: F811
    out = await flow.ingest_appliance(kind="washer")
    assert out.status == "failed" and out.notice is not None and out.notice["kind"] == "plate_unreadable"


async def test_lookup_miss_is_failed_with_the_makers_page(private: Path) -> None:  # noqa: F811
    seen: list[str] = []
    web = FakeWeb({"https://www.bosch-home.nl/robots.txt": (200, BOSCH_ROBOTS)})
    out = await flow.ingest_appliance(kind="dishwasher", brand="bosch", model="SMS00XX00E/13", fetch=web,
                                      on_progress=lambda s, d: seen.append(s))
    assert out.status == "failed" and out.notice is not None
    assert out.notice["kind"] == "lookup_failed" and out.notice["maker_page"].endswith("/SMS00XX00E/13")
    assert out.notice["reason"] == "site_blocked"  # robots.txt disallows the support page
    assert seen == ["lookup", "done"]


async def test_shared_pdf_builds_a_checked_card(private: Path, tmp_path: Path, acme_pdf: Path) -> None:  # noqa: F811
    good = copy.deepcopy(DRAFT)
    good["jobs"] = good["jobs"][:1]
    pdf = acme_pdf.read_bytes()
    seen: list[str] = []
    out = await flow.ingest_appliance(kind="washer", brand="acme", model="WM-0000", pdf=pdf,
                                      client=_client(tmp_path / "fx", pdf, good),
                                      on_progress=lambda s, d: seen.append(s))
    assert out.status == "checked" and out.card_path is not None and out.card_path.is_relative_to(private)
    assert seen == ["ingest", "done"]


async def test_the_wait_screen_learns_the_manual_then_the_checked_jobs(private: Path, tmp_path: Path,  # noqa: F811
                                                                      acme_pdf: Path) -> None:
    good = copy.deepcopy(DRAFT)
    good["jobs"] = good["jobs"][:1]
    pdf = acme_pdf.read_bytes()
    updates: list[dict[str, Any]] = []
    out = await flow.ingest_appliance(kind="washer", brand="acme", model="WM-0000", pdf=pdf, household_lang="nl",
                                      client=_client(tmp_path / "fx", pdf, good), on_update=updates.append)
    assert out.status == "checked" and out.card is not None
    manual = updates[0]["manual"]
    assert manual["pages"] == 6 and manual["lang"] == "en" and manual["user_lang"] is False
    assert updates[0]["eta_s"] > 0 and updates[0]["progress"] > 0
    progress = [u["progress"] for u in updates]
    assert progress == sorted(progress) and progress[-1] < 1  # done is the stage's word, not the bar's
    first = next(u for u in updates if u.get("jobs_ready"))
    assert first["progress"] < progress_mod.DRAFT_TO  # listed from the stream, while the draft is still running
    assert first["quotes"]["checked"] >= first["quotes"]["passed"] > 0
    job = first["jobs_ready"][0]
    assert job["id"] == out.card["jobs"][0]["id"] and job["startable"] is False and job["steps"] >= 1
    assert [j["id"] for j in updates[-1]["jobs_ready"]] == [j["id"] for j in out.card["jobs"]]


async def test_an_uploaded_file_that_is_not_a_pdf_says_so(private: Path) -> None:  # noqa: F811
    out = await flow.ingest_appliance(kind="washer", brand="acme", model="WM-0000", pdf=b"PK\x03\x04 a zip")
    assert out.status == "failed" and out.notice is not None
    assert (out.notice["kind"], out.notice["reason"]) == ("ingest_failed", "not_a_manual")


async def test_a_link_off_the_makers_sites_is_never_fetched(private: Path) -> None:  # noqa: F811
    seen: list[str] = []
    out = await flow.ingest_appliance(brand="bosch", model="SMS00XX00E", pdf_url="https://www.manualslib.com/x.pdf",
                                      on_progress=lambda s, d: seen.append(s))
    assert (out.status, out.stage) == ("failed", "fetch") and out.notice is not None
    assert out.notice["kind"] == "lookup_failed" and seen == []


async def test_closer_photo_is_a_plate_unreadable_notice(private: Path, tmp_path: Path) -> None:  # noqa: F811
    client, photo = _plate_client(tmp_path, _reply("Bosch", "??"))
    out = await flow.ingest_appliance(plate_jpeg=photo, client=client)
    assert out.notice is not None and set(out.notice) == {"kind", "text", "maker_page"}
    assert out.notice["kind"] == "plate_unreadable"


async def test_a_typed_model_with_no_brand_finds_its_card(private: Path) -> None:  # noqa: F811
    out = await flow.ingest_appliance(model="SMV4HAX40N/17", fetch=FakeWeb({}))
    assert (out.status, out.brand) == ("checked", "bosch")


async def test_a_typed_model_with_no_brand_uses_the_code_shape(private: Path) -> None:  # noqa: F811
    web = FakeWeb({"https://www.bosch-home.nl/robots.txt": (200, BOSCH_ROBOTS)})
    out = await flow.ingest_appliance(model="SMS00XX00E", fetch=web)
    assert (out.status, out.brand) == ("failed", "bosch") and out.notice is not None
    assert out.notice["maker_page"].endswith("/SMS00XX00E")


async def test_an_unknown_code_with_no_brand_asks_for_the_plate(private: Path) -> None:  # noqa: F811
    out = await flow.ingest_appliance(model="12345")
    assert out.status == "failed" and out.notice is not None and "maker" in out.notice["text"]["en"]


async def test_the_add_and_its_build_share_one_run_id_in_the_ledger_tag(private: Path, tmp_path: Path,  # noqa: F811
                                                                         acme_pdf: Path) -> None:
    good = copy.deepcopy(DRAFT)
    good["jobs"] = good["jobs"][:1]
    pdf = acme_pdf.read_bytes()
    client = _client(tmp_path / "fx", pdf, good)
    tags: list[str | None] = []
    respond = client.respond

    async def tagged(*a: Any, **k: Any) -> Any:
        tags.append(calls.client._RUN.get())
        return await respond(*a, **k)

    client.respond = tagged  # type: ignore[method-assign]
    out = await flow.ingest_appliance(brand="acme", model="WM-0000", pdf=pdf, client=client)
    assert out.status == "checked" and tags
    kinds = {t.rsplit(":", 1)[0] for t in tags if t}
    ids = {t.rsplit(":", 1)[1] for t in tags if t}
    assert kinds == {"card-build:acme/WM-0000"} and len(ids) == 1 and len(ids.pop()) == 12


async def test_a_plate_read_is_tagged_as_part_of_the_add(private: Path, tmp_path: Path) -> None:  # noqa: F811
    client, photo = _plate_client(tmp_path, _reply("Bosch", "??"))
    tags: list[str | None] = []
    respond = client.respond

    async def tagged(*a: Any, **k: Any) -> Any:
        tags.append(calls.client._RUN.get())
        return await respond(*a, **k)

    client.respond = tagged  # type: ignore[method-assign]
    await flow.ingest_appliance(plate_jpeg=photo, client=client)
    assert len(tags) == 1 and tags[0] is not None and tags[0].startswith("add-appliance:unknown/unknown:")


async def test_every_call_of_a_households_ingest_is_that_households(private: Path, tmp_path: Path,  # noqa: F811
                                                                    acme_pdf: Path) -> None:
    from tests.l3.test_appliance_file import UNPLUG
    d = copy.deepcopy(DRAFT)
    d["jobs"][0]["steps"].insert(0, copy.deepcopy(UNPLUG))
    d["jobs"][0]["steps"].pop(2)  # the manual's warning left out: the job repair is asked too
    pdf = acme_pdf.read_bytes()
    client = _client(tmp_path / "fx", pdf, d)
    whose: list[tuple[str, str | None, float | None]] = []
    respond = client.respond

    async def seen(*a: Any, **k: Any) -> Any:
        whose.append((k["format"]["name"], k.get("household"), k.get("household_cap_usd")))
        return await respond(*a, **k)

    client.respond = seen  # type: ignore[method-assign]
    out = await flow.ingest_appliance(brand="acme", model="WM-0000", pdf=pdf, client=client, household="h1",
                                      household_cap_usd=2.5)
    assert out.status == "pages_only"  # no repair answer is recorded here, so no job is kept
    assert {"ApplianceFile", "JobRepair"} <= {n for n, _, _ in whose}  # the draft and a follow-up call
    assert {(h, cap) for _, h, cap in whose} == {("h1", 2.5)}  # so its request bodies are erased with the home

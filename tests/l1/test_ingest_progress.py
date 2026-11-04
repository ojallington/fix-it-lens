"""The wait for a new appliance's card (contracts 1.15, Ingest): what ingest knows reaches the phone as it learns it,
and the text in it that no code wrote (each job's title, the PDF's own title) passes G0 first. A held job title is
never served: the job is listed in jobs_refused by its id and the rule's family only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

import calls
from app.adding import served
from app.schedule import store as l5
from app.server import create_app
from app.store import Store
from tests.l0.test_contracts import _openapi_validator
from tests.l1.test_api import SECRET, auth, ingested
from tests.l1.test_session import HELD, ROOT
from tests.l5.conftest import add_household

UPDATE: dict[str, Any] = {
    "progress": 0.42, "elapsed_s": 31, "eta_s": 40,
    "manual": {"pages": 64, "lang": "nl", "user_lang": True, "title": "Gebruiksaanwijzing vaatwasser"},
    "quotes": {"checked": 12, "passed": 11},
    "jobs_ready": [
        {"id": "clean_filters", "title": {"en": "Clean the filters", "nl": "Filters reinigen"}, "steps": 8,
         "page": 42, "duration_min": 10, "kind": "upkeep", "startable": False},
        {"id": "open_up", "title": {"en": HELD}, "steps": 3, "page": 50, "duration_min": None, "kind": "fix",
         "startable": False},
    ],
}


class Updating:
    def __init__(self, update: dict[str, Any]) -> None:
        self.update, self.calls = update, []  # type: ignore[var-annotated]

    async def __call__(self, **kw: Any) -> Any:
        self.calls.append(kw)
        kw["on_progress"]("ingest")
        kw["on_update"](self.update)
        return ingested("pending", stage="ingest", model=kw["model"])  # a model with no card yet


def world(tmp_path: Path, fake: Any) -> tuple[TestClient, str]:
    conn = l5.open_store(":memory:")
    hid = add_household(conn)
    client = calls.make_client("replay", fixtures_root=tmp_path / "fx", private=None)
    tc = TestClient(create_app(client=client, record_root=tmp_path / "s", store=Store(conn, SECRET), ingest=fake))
    tc.conn = conn  # type: ignore[attr-defined]  # for a second household
    return tc, hid


def add(tc: TestClient, hid: str) -> dict[str, Any]:
    a = tc.post("/api/v1/appliances", headers=auth(hid), json={"alias": "dw", "brand": "bosch", "model": "X1"})
    assert a.status_code == 201, a.text
    got: dict[str, Any] = tc.get(f"/api/v1/appliances/{a.json()['id']}", headers=auth(hid)).json()
    return got


def test_progress_is_served_with_held_titles_taken_out(tmp_path: Path) -> None:
    fake = Updating(UPDATE)
    tc, hid = world(tmp_path, fake)
    with tc:
        got = add(tc, hid)
    ing = got["ingest"]
    assert _openapi_validator("Appliance").is_valid(got), list(_openapi_validator("Appliance").iter_errors(got))
    assert ing["stage"] == "ingest" and ing["progress"] == 0.42 and ing["eta_s"] == 40
    assert ing["manual"]["title"] == "Gebruiksaanwijzing vaatwasser" and ing["manual"]["pages"] == 64
    assert ing["manual"]["thumb_url"] == f"/api/v1/appliances/{got['id']}/manual/pages/1.png"
    assert [j["id"] for j in ing["jobs_ready"]] == ["clean_filters"]
    assert ing["jobs_refused"] == [{"id": "open_up", "reason": "mains"}] and HELD not in str(got)
    assert fake.calls[0]["household_lang"] == "en"  # ingest reads the household's language pages of the manual


def test_ingest_is_given_the_households_own_language(tmp_path: Path) -> None:
    fake = Updating(UPDATE)
    tc, hid = world(tmp_path, fake)
    with tc.conn:  # type: ignore[attr-defined]
        tc.conn.execute("UPDATE households SET lang = 'nl' WHERE id = ?", (hid,))  # type: ignore[attr-defined]
    with tc:
        add(tc, hid)
    assert fake.calls[0]["household_lang"] == "nl"  # a Dutch home gets a multi-language manual's Dutch pages


def test_a_held_manual_title_is_null_and_a_held_language_is_dropped() -> None:
    upd = {**UPDATE, "manual": {**UPDATE["manual"], "title": HELD},
           "jobs_ready": [{**UPDATE["jobs_ready"][0], "title": {"en": "Clean the filters", "nl": "Verwijder het "
                                                                   "achterpaneel."}}]}
    out = served(upd, "nl", "ap_1")
    assert out["manual"]["title"] is None
    assert out["jobs_ready"][0]["title"] == {"en": "Clean the filters"} and "jobs_refused" not in out


def test_a_title_in_a_language_the_rules_lack_is_not_served() -> None:
    upd = {"jobs_ready": [{**UPDATE["jobs_ready"][0], "title": {"de": "Filter reinigen"}}]}  # type: ignore[dict-item]
    out = served(upd, "nl", "ap_1")
    assert out["jobs_ready"] == [] and out["jobs_refused"] == [{"id": "clean_filters", "reason": "other"}]


# ---- the upload ---------------------------------------------------------------------------------------------------

PDF = b"%PDF-1.7\n" + b"x" * 100


def upload(tc: TestClient, hid: str, aid: str, body: bytes, ctype: str = "application/pdf") -> Any:
    return tc.post(f"/api/v1/appliances/{aid}/manual", headers={**auth(hid), "content-type": ctype}, content=body)


def test_an_uploaded_pdf_runs_the_ingest_again_on_its_bytes(tmp_path: Path) -> None:
    fake = Updating(UPDATE)
    tc, hid = world(tmp_path, fake)
    with tc:
        a = add(tc, hid)
        r = upload(tc, hid, a["id"], PDF)
        assert r.status_code == 201 and r.json()["id"] == a["id"] and r.json()["alias"] == "dw"
        assert _openapi_validator("Appliance").is_valid(r.json())
        assert len(fake.calls) == 2 and fake.calls[1]["pdf"] == PDF
        row = tc.get(f"/api/v1/appliances/{a['id']}", headers=auth(hid)).json()
        assert (fake.calls[1]["brand"], fake.calls[1]["model"]) == (row["brand"], row["model"])  # as ingest left them


def test_an_upload_is_checked_before_a_byte_is_kept(tmp_path: Path) -> None:
    from app.adding import MAX_PDF_BYTES
    from ingest.flow import MAX_PDF_BYTES as INGEST_MAX

    fake = Updating(UPDATE)
    tc, hid = world(tmp_path, fake)
    with tc:
        a = add(tc, hid)
        assert upload(tc, hid, a["id"], b"<html>not a pdf").status_code == 415
        assert upload(tc, hid, a["id"], PDF, "application/octet-stream").status_code == 415
        big = upload(tc, hid, a["id"], PDF + b"x" * (MAX_PDF_BYTES - len(PDF) + 1))
        assert big.status_code == 413 and big.json()["error"] == "too_large"
        assert upload(tc, hid, "ap_nope", PDF).status_code == 404
        assert tc.post(f"/api/v1/appliances/{a['id']}/manual", content=PDF,
                       headers={"content-type": "application/pdf"}).status_code == 401
    assert len(fake.calls) == 1 and MAX_PDF_BYTES == INGEST_MAX  # nothing re-ran; one cap for the route and ingest


# ---- the manual's pages ---------------------------------------------------------------------------------------------

def test_the_households_manual_pages_render_and_nobody_elses(tmp_path: Path, monkeypatch: Any) -> None:
    import pypdfium2 as pdfium  # type: ignore[import-untyped]

    from ingest.pages import manual_path

    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "private"))
    tc, hid = world(tmp_path, Updating(UPDATE))
    with tc:
        a = add(tc, hid)
        url = a["ingest"]["manual"]["thumb_url"].removeprefix("/api/v1")
        assert tc.get("/api/v1" + url, headers=auth(hid)).status_code == 404  # no manual kept yet
        doc = pdfium.PdfDocument.new()
        for _ in range(2):
            doc.new_page(200, 300)
        path = manual_path(a["brand"], a["model"])
        path.parent.mkdir(parents=True)
        doc.save(str(path))
        r = tc.get("/api/v1" + url, headers=auth(hid))
        assert r.status_code == 200 and r.content.startswith(b"\x89PNG") and "private" in r.headers["cache-control"]
        assert tc.get(f"/api/v1/appliances/{a['id']}/manual/pages/3.png", headers=auth(hid)).status_code == 404
        assert tc.get(f"/api/v1/appliances/{a['id']}/manual/pages/0.png", headers=auth(hid)).status_code == 404
        assert tc.get("/api/v1" + url).status_code == 401
        other = add_household(tc.conn)  # type: ignore[attr-defined]
        assert other != hid and tc.get("/api/v1" + url, headers=auth(other)).status_code == 404  # another home's


# ---- why an add failed ----------------------------------------------------------------------------------------------

def test_each_reason_has_its_words_and_its_code() -> None:
    from app.adding import NOTICES, REASONS, notice

    v = _openapi_validator("IngestNotice")
    page = "https://www.bosch-home.nl/"
    for reason, kind in (("too_large", "ingest_failed"), ("not_a_manual", "ingest_failed"),
                         ("site_blocked", "lookup_failed"), ("timeout", "ingest_failed")):
        n = notice(kind, page, reason)
        assert n["reason"] == reason and n["text"] == REASONS[reason] and v.is_valid(n), n
    assert notice("lookup_failed", page, "no_manual")["text"] == NOTICES["lookup_failed"]
    blocked = notice("lookup_failed", None, "site_blocked")  # no page to open: the words don't send the user there
    assert blocked["text"] == NOTICES["lookup_failed_no_page"] and blocked["reason"] == "site_blocked"
    assert "reason" not in notice("lookup_failed", page, "made up") and v.is_valid(notice("lookup_failed", page))


def test_every_notice_passes_g0_in_its_language() -> None:
    import rules
    from app.adding import NOTICES, REASONS
    from app.session import split_sentences

    for words in [*NOTICES.values(), *REASONS.values()]:
        assert set(words) == {"en", "nl"}
        for lang, text in words.items():
            for s in split_sentences(text):
                assert isinstance(rules.check_sentence(s, lang, None, None, country="nl"), rules.Pass), (lang, s)


def test_a_failed_upload_says_why(tmp_path: Path) -> None:
    from types import SimpleNamespace

    class TooBig(Updating):
        async def __call__(self, **kw: Any) -> Any:
            self.calls.append(kw)
            return SimpleNamespace(status="failed", stage="done", brand="bosch", model="X1", plate_confirmed=True,
                                   card_path=None, card=None, plate=None,
                                   notice={"kind": "ingest_failed", "text": {"en": "ingest's own"},
                                           "maker_page": None, "reason": "too_large"})

    tc, hid = world(tmp_path, TooBig(UPDATE))
    with tc:
        got = add(tc, hid)
    n = got["ingest"]["notice"]
    assert n["reason"] == "too_large" and "50 MB" in n["text"]["en"] and n["text"]["en"] != "ingest's own"
    assert _openapi_validator("Appliance").is_valid(got)


# ---- a card is every home's -----------------------------------------------------------------------------------------

def test_an_upload_never_rebuilds_a_card_another_home_uses(tmp_path: Path, monkeypatch: Any) -> None:
    """Two homes with the same model share its checked card and the manual it was built from. The second home's
    upload (or pasted link) links it to that card: ingest runs without the bytes, nothing is rebuilt or rewritten,
    and the first home's card is checked throughout."""
    import hashlib

    from ingest.pages import manual_path

    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "private"))
    card = ROOT / "cards/bosch/SMV4HAX40N.yaml"
    before = hashlib.sha256(card.read_bytes()).hexdigest()
    seen: list[dict[str, Any]] = []

    async def real(**kw: Any) -> Any:  # ingest itself, watched
        from ingest.flow import ingest_appliance

        seen.append({k: kw.get(k) for k in ("brand", "model", "pdf", "pdf_url")})
        return await ingest_appliance(**kw)

    tc, a_home = world(tmp_path, real)
    with tc:
        b_home = add_household(tc.conn)  # type: ignore[attr-defined]
        body = {"alias": "dw", "brand": "bosch", "model": "SMV4HAX40N"}
        a = tc.post("/api/v1/appliances", headers=auth(a_home), json=body).json()
        b = tc.post("/api/v1/appliances", headers=auth(b_home), json=body).json()
        for home, x in ((a_home, a), (b_home, b)):
            assert tc.get(f"/api/v1/appliances/{x['id']}", headers=auth(home)).json()["card_status"] == "checked"
        up = upload(tc, b_home, b["id"], PDF)
        assert up.status_code == 201
        again = tc.post("/api/v1/appliances", headers=auth(b_home),
                        json={"alias": "dw2", "brand": "bosch", "model": "SMV4HAX40N",
                              "pdf_url": "https://media3.bosch-home.com/m.pdf"})
        assert again.status_code == 201
        for home, x in ((a_home, a), (b_home, b), (b_home, again.json())):
            assert tc.get(f"/api/v1/appliances/{x['id']}", headers=auth(home)).json()["card_status"] == "checked"
    assert all(s["pdf"] is None and s["pdf_url"] is None and s["model"] == "SMV4HAX40N" for s in seen[2:]), seen
    assert hashlib.sha256(card.read_bytes()).hexdigest() == before  # the card is untouched
    assert not manual_path("bosch", "SMV4HAX40N").exists()  # and the upload's bytes were never kept as its manual

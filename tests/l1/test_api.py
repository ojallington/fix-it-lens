"""The household routes and the technician note: a household sees only its own appliances, history and
sessions; posted history is the user's kinds only; the note names the stop, the checked steps and the picked photos,
marks the landlord's jobs for a renter, and never carries a held sentence."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import calls
from app.schedule import store as l5
from app.server import create_app
from app.session import Session
from app.store import Store
from tests.l1.helpers import FakeClient, frame
from tests.l1.test_session import HELD, acme
from tests.l5.conftest import add_appliance, add_household

SECRET = b"\x02" * 32
BOSCH = "cards/bosch/SMV4HAX40N.yaml"


@pytest.fixture
def world(tmp_path: Path) -> tuple[TestClient, sqlite3.Connection, dict[str, str]]:
    conn = l5.open_store(":memory:")
    a, b = add_household(conn), add_household(conn)
    ids = {"a": a, "b": b, "wa": add_appliance(conn, a, "washer", BOSCH), "wb": add_appliance(conn, b, "washer")}
    client = calls.make_client("replay", fixtures_root=tmp_path / "fx", private=None)
    app = create_app(client=client, record_root=tmp_path / "sessions", store=Store(conn, SECRET))
    return TestClient(app), conn, ids


def auth(hid: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {l5.household_token(SECRET, hid)}"}


def test_only_your_own_appliances(world: Any) -> None:
    tc, _, ids = world
    got = tc.get("/api/v1/appliances", headers=auth(ids["a"])).json()
    assert [x["id"] for x in got] == [ids["wa"]] and got[0]["card_status"] == "checked"
    assert tc.get(f"/api/v1/appliances/{ids['wb']}", headers=auth(ids["a"])).status_code == 404
    assert tc.get("/api/v1/appliances").status_code == 401
    card = tc.get(f"/api/v1/appliances/{ids['wa']}/card", headers=auth(ids["a"]))
    assert card.status_code == 200 and card.json()["brand"].lower() == "bosch" and "checks" in card.json()


def test_removing_an_appliance_is_your_own_home_only_and_keeps_its_history(world: Any) -> None:
    tc, conn, ids = world
    a, b = auth(ids["a"]), auth(ids["b"])
    assert tc.delete(f"/api/v1/appliances/{ids['wb']}", headers=a).status_code == 404  # another home's: never there
    assert [x["id"] for x in tc.get("/api/v1/appliances", headers=b).json()] == [ids["wb"]]
    assert tc.delete(f"/api/v1/appliances/{ids['wa']}").status_code == 401
    tc.post("/api/v1/history", headers=a, json={"kind": "user_entry", "on_date": "2026-10-20",
                                                "appliance_id": ids["wa"], "text": "descaled"})
    library = conn.execute("SELECT * FROM card_library").fetchall()
    assert tc.delete(f"/api/v1/appliances/{ids['wa']}", headers=a).status_code == 204
    assert tc.get("/api/v1/appliances", headers=a).json() == []
    assert tc.get(f"/api/v1/appliances/{ids['wa']}", headers=a).status_code == 404
    assert tc.delete(f"/api/v1/appliances/{ids['wa']}", headers=a).status_code == 404
    assert [e["text"] for e in tc.get("/api/v1/history", headers=a).json()] == ["descaled"]
    assert conn.execute("SELECT * FROM card_library").fetchall() == library
    row = conn.execute("SELECT alias, deleted_at FROM appliances WHERE id = ?", (ids["wa"],)).fetchone()
    assert row["deleted_at"] and row["alias"] == f"washer (removed {ids['wa']})"  # the name is free again


def test_history_takes_only_the_users_kinds(world: Any) -> None:
    tc, _, ids = world
    h = auth(ids["a"])
    ok = tc.post("/api/v1/history", headers=h, json={"kind": "user_entry", "on_date": "2026-10-20",
                                                     "appliance_id": ids["wa"], "text": "descaled"})
    assert ok.status_code == 201 and ok.json()["text"] == "descaled"
    assert tc.post("/api/v1/history", headers=h, json={"kind": "stop", "on_date": "2026-10-20"}).status_code == 422
    assert tc.post("/api/v1/history", headers=h, json={"kind": "fault", "on_date": "2026-10-20",
                                                       "appliance_id": ids["wb"]}).status_code == 404
    assert [e["text"] for e in tc.get("/api/v1/history", headers=h).json()] == ["descaled"]
    assert tc.get("/api/v1/history", headers=auth(ids["b"])).json() == []


async def stopped_session(tmp_path: Path, conn: sqlite3.Connection, ids: dict[str, str]) -> Session:
    s = Session(session_id="note1", client=FakeClient(), card=acme(), card_path=None, job_id="clean_debris_filter",
                country="nl", record_root=tmp_path / "sessions", household=ids["a"], appliance_id=ids["wa"])
    Store(conn, SECRET).session_started(s)
    for i in range(3):
        f = frame(i, i * 100)
        s.frames.add(f)
        s.rec.client(f.t_ms, "c2s", {"type": "frame", "seq": i, "t_ms": f.t_ms, "frame_id": f.frame_id,
                                      "jpeg_1024": None})
        s.rec.frame(f.frame_id, f.jpeg)
    await s.say(f"Good. {HELD}")
    await s.report_hazard(None, f"water near the socket. {HELD}")
    s.rec.end("stopped")
    return s


async def test_the_note_after_a_stop(world: Any, tmp_path: Path) -> None:
    tc, conn, ids = world
    await stopped_session(tmp_path, conn, ids)
    r = tc.post("/api/v1/sessions/note1/note", headers=auth(ids["a"]),
                json={"photo_frame_ids": ["f1", "f2", "nope"], "renter": True})
    assert r.status_code == 200
    note = r.json()
    assert set(note) == {"text", "photos", "landlord_jobs"}
    assert note["photos"] == ["/api/v1/replay/note1/frames/f1.jpg", "/api/v1/replay/note1/frames/f2.jpg"]
    assert "Stopped:" in note["text"] and HELD not in json.dumps(note)
    assert tc.post("/api/v1/sessions/note1/note", headers=auth(ids["b"]),
                   json={"photo_frame_ids": []}).status_code == 404
    assert [x["id"] for x in tc.get("/api/v1/sessions", headers=auth(ids["a"])).json()] == ["note1"]


async def test_note_marks_the_landlords_jobs_for_a_renter_only(tmp_path: Path) -> None:
    from app.note import build_note
    from app.recorder import load
    from contracts.models.card import Card
    from tests.l4.conftest import boiler_data

    boiler = Card.model_validate(boiler_data())
    s = Session(session_id="n2", client=FakeClient(), card=boiler, job_id="top_up", country="nl",
                record_root=tmp_path)
    await s.report_hazard(None, "a smell near the boiler")
    rec = load(tmp_path, "n2")
    renter = build_note(rec, boiler, photo_frame_ids=[], renter=True)
    owner = build_note(rec, boiler, photo_frame_ids=[], renter=False)
    assert renter["landlord_jobs"] and owner["landlord_jobs"] == []
    assert "landlord" in renter["text"].lower() and "landlord" not in owner["text"].lower()


# ---- POST /appliances: ingest in the background ------------------------------------------------------------------


class FakeIngest:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, **kw: Any) -> Any:
        self.calls.append(kw)
        kw["on_progress"]("lookup")
        kw["on_progress"]("fetch")
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def ingested(status: str, stage: str = "done", card_path: Path | None = None, notice: Any = None,
             kind: str | None = "dishwasher", model: str = "SMV4HAX40N") -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(status=status, stage=stage, brand="bosch", model=model, plate_confirmed=True,
                           card_path=card_path, card={"appliance_kind": kind} if kind else None, notice=notice,
                           plate=None)


def adding_world(tmp_path: Path, result: Any) -> tuple[TestClient, Any, str, FakeIngest]:
    conn = l5.open_store(":memory:")
    hid = add_household(conn)
    fake = FakeIngest(result)
    client = calls.make_client("replay", fixtures_root=tmp_path / "fx", private=None)
    app = create_app(client=client, record_root=tmp_path / "s", store=Store(conn, SECRET), ingest=fake)
    return TestClient(app), app, hid, fake


def test_typed_model_goes_to_ingest_and_ends_checked(tmp_path: Path) -> None:
    tc, app, hid, fake = adding_world(tmp_path, ingested("checked", card_path=Path(BOSCH).resolve()))
    with tc:
        r = tc.post("/api/v1/appliances", headers=auth(hid), json={"brand": "Bosch", "model": "SMV4HAX40N"})
        assert r.status_code == 201
        a = r.json()
        assert a["card_status"] == "pending" and a["ingest"]["stage"] in ("lookup", "fetch", "done")
        got = tc.get(f"/api/v1/appliances/{a['id']}", headers=auth(hid)).json()
    assert fake.calls[0]["brand"] == "Bosch" and fake.calls[0]["lane"] == "pilot" and fake.calls[0]["household"] == hid
    assert got["card_status"] == "checked" and got["kind"] == "dishwasher" and got["alias"] == "dishwasher"
    assert "ingest" not in got


def test_lookup_miss_is_failed_with_fixed_text_and_can_be_retried(tmp_path: Path) -> None:
    from app.adding import NOTICES

    miss = ingested("failed", notice={"kind": "lookup_failed", "text": {"en": "model words"},
                                      "maker_page": "https://www.bosch-home.nl/"}, model="X1")  # no card yet
    tc, _, hid, fake = adding_world(tmp_path, miss)
    with tc:
        a = tc.post("/api/v1/appliances", headers=auth(hid),
                    json={"alias": "dw", "brand": "bosch", "model": "X1"}).json()
        got = tc.get(f"/api/v1/appliances/{a['id']}", headers=auth(hid)).json()
        assert got["card_status"] == "failed"
        assert got["ingest"]["notice"] == {"kind": "lookup_failed", "text": NOTICES["lookup_failed"],
                                           "maker_page": "https://www.bosch-home.nl/"}
        assert "Manual link" in NOTICES["lookup_failed"]["en"]  # the add screen's own field, by its label
        fake.result = ingested("checked", card_path=Path(BOSCH).resolve())
        off = tc.post("/api/v1/appliances", headers=auth(hid), json={"alias": "dw", "pdf_url": "https://x.example/m.pdf"})
        assert off.status_code == 422 and len(fake.calls) == 1  # off the makers' sites: never fetched
        again = tc.post("/api/v1/appliances", headers=auth(hid),
                        json={"alias": "dw", "pdf_url": "https://media3.bosch-home.com/m.pdf"})
        assert again.status_code == 201 and again.json()["id"] == a["id"]
        assert tc.get(f"/api/v1/appliances/{a['id']}", headers=auth(hid)).json()["card_status"] == "checked"
        assert tc.post("/api/v1/appliances", headers=auth(hid),
                       json={"alias": "dw", "brand": "bosch", "model": "X1"}).status_code == 409
    assert fake.calls[1]["pdf_url"] == "https://media3.bosch-home.com/m.pdf" and fake.calls[1]["model"]


def test_a_crash_in_ingest_is_a_failed_add_never_its_message(tmp_path: Path) -> None:
    tc, _, hid, _ = adding_world(tmp_path, RuntimeError("secret path /home/x/private"))
    with tc:
        a = tc.post("/api/v1/appliances", headers=auth(hid), json={"brand": "bosch", "model": "X1"}).json()
        got = tc.get(f"/api/v1/appliances/{a['id']}", headers=auth(hid)).json()
    assert got["card_status"] == "failed" and "secret" not in json.dumps(got)


def test_needs_a_model_or_a_plate(tmp_path: Path) -> None:
    tc, _, hid, fake = adding_world(tmp_path, ingested("checked"))
    with tc:
        r = tc.post("/api/v1/appliances", headers=auth(hid), json={"alias": "x"})
    assert r.status_code == 422 and r.json()["error"] and fake.calls == []


def test_a_typed_model_alone_is_enough(tmp_path: Path) -> None:
    tc, _, hid, fake = adding_world(tmp_path, ingested("checked", card_path=Path(BOSCH).resolve()))
    with tc:
        a = tc.post("/api/v1/appliances", headers=auth(hid), json={"model": "SMV4HAX40N"}).json()
    assert a["ingest"]["stage"] != "plate" and fake.calls[0]["brand"] is None and fake.calls[0]["model"] == "SMV4HAX40N"


@pytest.mark.parametrize("url,ok", [
    ("https://media3.bosch-home.com/Documents/m.pdf", True), ("https://www.miele.nl/m.pdf", True),
    ("http://www.bosch-home.nl/m.pdf", False), ("https://bosch-home.nl.evil.example/m.pdf", False),
    ("https://evilbosch-home.nl/m.pdf", False), ("https://u:p@www.bosch-home.nl/m.pdf", False),
    ("https://www.bosch-home.nl:8443/m.pdf", False), ("https://www.lg.com/m.pdf", False),
])
def test_pdf_url_only_on_a_makers_own_site(url: str, ok: bool) -> None:
    from app.adding import maker_url

    assert maker_url(url) is ok


def test_a_settled_plate_goes_to_ingest_as_typed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import adding

    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "private"))
    tc, _, hid, fake = adding_world(tmp_path, ingested("checked", card_path=Path(BOSCH).resolve()))
    adding.keep_settled("pl_1", "bosch", "SMV4HAX40N", hid)  # the user's confirm_plate over the WS
    assert adding.settled_plate("pl_1") is None and adding.settled_plate("pl_1", "h_other") is None  # this home's only
    with tc:
        assert tc.post("/api/v1/appliances", headers=auth(hid), json={"plate_photo_id": "pl_1"}).status_code == 201
    assert fake.calls[0]["brand"] == "bosch" and fake.calls[0]["model"] == "SMV4HAX40N"
    assert fake.calls[0].get("plate_jpeg") is None  # no photo is kept, so none goes to ingest


def test_every_call_of_an_add_counts_against_the_homes_cap(tmp_path: Path) -> None:
    tc, _, hid, fake = adding_world(tmp_path, ingested("checked", card_path=Path(BOSCH).resolve()))
    with tc:
        tc.post("/api/v1/appliances", headers=auth(hid), json={"brand": "bosch", "model": "SMV4HAX40N"})
    assert fake.calls[0]["household"] == hid and fake.calls[0]["household_cap_usd"] == 0.45
    tc, _, hid, fake = adding_world(tmp_path, calls.BudgetExceeded("household: cap reached"))
    with tc:
        a = tc.post("/api/v1/appliances", headers=auth(hid), json={"brand": "bosch", "model": "SMV4HAX40N"}).json()
        for _ in range(200):  # the add runs in the background
            got = tc.get(f"/api/v1/appliances/{a['id']}", headers=auth(hid)).json()
            if got["card_status"] != "pending":
                break
            time.sleep(0.01)
    assert got["card_status"] == "failed" and got["ingest"]["notice"]["reason"] == "cap_reached"


def test_a_lookup_miss_without_a_makers_page_does_not_send_the_user_there() -> None:
    from app.adding import notice

    for page in (None, "http://www.bosch-home.nl/"):  # the phone shows the maker's link only for https
        n = notice("lookup_failed", page)
        assert n["kind"] == "lookup_failed" and "maker's page" not in n["text"]["en"]
        assert "pagina van de fabrikant" not in n["text"]["nl"] and "Manual link" in n["text"]["en"]
    assert "Open the maker's page" in notice("lookup_failed", "https://www.bosch-home.nl/")["text"]["en"]


@pytest.mark.parametrize("body", [{"brand": "Samsung", "model": "WW90T534DAW"},
                                  {"brand": " samsung ", "model": "WW90T534DAW",
                                   "pdf_url": "https://www.bosch-home.nl/m.pdf"}])
def test_a_maker_that_declined_gets_no_card_and_no_ingest(tmp_path: Path, body: dict[str, str]) -> None:
    import rules
    from app.adding import REASONS

    tc, _, hid, fake = adding_world(tmp_path, ingested("checked", card_path=Path(BOSCH).resolve()))
    with tc:
        a = tc.post("/api/v1/appliances", headers=auth(hid), json=body).json()
        got = tc.get(f"/api/v1/appliances/{a['id']}", headers=auth(hid)).json()
    assert fake.calls == []  # no lookup, fetch, write or model call
    assert got["card_status"] == "failed"
    assert got["ingest"]["notice"] == {"kind": "lookup_failed", "reason": "maker_terms",
                                       "text": REASONS["maker_terms"], "maker_page": None}
    for lang, text in REASONS["maker_terms"].items():
        assert isinstance(rules.check_sentence(text, lang, None, None, country="nl"), rules.Pass)


def test_a_maker_that_declined_is_refused_by_a_settled_plate_too(tmp_path: Path,
                                                                  monkeypatch: pytest.MonkeyPatch) -> None:
    from app import adding

    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "private"))
    tc, _, hid, fake = adding_world(tmp_path, ingested("checked", card_path=Path(BOSCH).resolve()))
    adding.keep_settled("pl_s", "SAMSUNG", "WW90T534DAW", hid)
    with tc:
        a = tc.post("/api/v1/appliances", headers=auth(hid), json={"plate_photo_id": "pl_s"}).json()
        got = tc.get(f"/api/v1/appliances/{a['id']}", headers=auth(hid)).json()
    assert fake.calls == [] and got["ingest"]["notice"]["reason"] == "maker_terms"


@pytest.mark.parametrize("model,asked", [("WW90T534DAW", True), ("dv90t5240aw", True), ("DW60M6050FS", True),
                                         ("ww-90t534daw", True), ("SMV4HAX40N", False), ("WWX1", False)])
def test_a_model_shaped_like_a_declined_makers_asks_for_the_brand(tmp_path: Path, model: str, asked: bool) -> None:
    tc, _, hid, fake = adding_world(tmp_path, ingested("checked", card_path=Path(BOSCH).resolve()))
    with tc:
        r = tc.post("/api/v1/appliances", headers=auth(hid), json={"model": model})
        assert (r.status_code == 422 and r.json()["error"] == "brand_needed") is asked
        if asked:
            assert fake.calls == []
            ok = tc.post("/api/v1/appliances", headers=auth(hid), json={"brand": "Bosch", "model": model})
            assert ok.status_code == 201  # the user's brand is taken as said


def ha_world(tmp_path: Path, result: Any) -> tuple[TestClient, str, FakeIngest, dict[str, str]]:
    conn = l5.open_store(":memory:")
    hid = add_household(conn)
    fake = FakeIngest(result)
    client = calls.make_client("replay", fixtures_root=tmp_path / "fx", private=None)
    app = create_app(client=client, record_root=tmp_path / "s", store=Store(conn, SECRET), ingest=fake)
    tokens = {scope: l5.issue_token(conn, hid, by_kind="ha", scope=scope, label="Home Assistant")[1]
              for scope in ("read", "write")}
    return TestClient(app), hid, fake, {s: {"Authorization": f"Bearer {t}"} for s, t in tokens.items()}


def test_home_assistant_adds_a_model_from_its_device_list_and_builds_nothing(tmp_path: Path) -> None:
    tc, hid, fake, ha = ha_world(tmp_path, ingested("checked", card_path=Path(BOSCH).resolve()))
    body = {"brand": "Bosch", "model": "SMV4HAX40N/39", "kind": "dishwasher", "alias": "Vaatwasser"}
    with tc:
        r = tc.post("/api/v1/appliances", headers=ha["write"], json=body)
        assert r.status_code == 201
        got = tc.get(f"/api/v1/appliances/{r.json()['id']}", headers=auth(hid)).json()
    assert got["card_status"] == "checked" and got["alias"] == "Vaatwasser"  # the library's card, linked at once
    assert fake.calls[0]["build"] is False and fake.calls[0]["household"] == hid  # no card is built for it


def test_home_assistant_adds_only_with_a_write_token_and_only_a_brand_and_model(tmp_path: Path) -> None:
    tc, _, fake, ha = ha_world(tmp_path, ingested("checked", card_path=Path(BOSCH).resolve()))
    body = {"brand": "Bosch", "model": "SMV4HAX40N"}
    with tc:
        r = tc.post("/api/v1/appliances", headers=ha["read"], json=body)
        assert r.status_code == 403 and "read-only" in r.text
        for extra in ({"plate_photo_id": "pl_1"}, {"pdf_url": "https://www.bosch-home.nl/m.pdf"}):
            assert tc.post("/api/v1/appliances", headers=ha["write"], json={**body, **extra}).status_code == 422
        assert tc.get("/api/v1/appliances", headers=ha["write"]).status_code == 200  # it still reads
        assert tc.post("/api/v1/appliances", headers={"Authorization": "Bearer fxt_nope"}, json=body).status_code == 401
    assert fake.calls == []


def test_a_phone_add_still_builds(tmp_path: Path) -> None:
    tc, hid, fake, _ = ha_world(tmp_path, ingested("checked", card_path=Path(BOSCH).resolve()))
    with tc:
        assert tc.post("/api/v1/appliances", headers=auth(hid), json={"brand": "Bosch", "model": "X1"}).status_code \
            == 201
    assert "build" not in fake.calls[0]  # the phone's add is the one it always was

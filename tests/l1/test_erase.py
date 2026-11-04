"""Deleting a household over DELETE /household removes every file of that household in the same request: its session
recordings, its plate photos (the per-home folder and an older flat copy its recording sent), its uploaded manual and
own cards, and the request bodies and fixtures of its calls. Another household's files, and a request body another
household also sent, stay. The ledger keeps every line and its cost, with the household's ids gone, and the log names
counts only."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import calls
from app import adding, display, erase
from app.feeds import feedback
from app.feeds.router import Deps, make_router
from app.schedule import store as l5
from app.server import create_app
from app.store import Store
from calls.ledger import Ledger
from contracts.models.ws import GoneClose
from ingest import flow, pages
from tests.l1.test_api import BOSCH, SECRET, FakeIngest, auth, ingested
from tests.l1.test_plates import AMBER, Plate, Reader, photo
from tests.l1.test_server import hello
from tests.l1.test_session import session
from tests.l5.conftest import add_appliance, add_household
from tests.l5.test_feedback import NONE

GONE = json.loads((Path(__file__).resolve().parents[2] / "contracts/ws.schema.json").read_text())["$defs"]["GoneClose"]
PDF = b"%PDF-1.4 the maker's manual, uploaded by the home"


class Uploads(FakeIngest):
    """Keeps the upload where ingest keeps a home's own manual, and a card beside it, as ingest does."""

    async def __call__(self, **kw: Any) -> Any:
        hid = kw["household"]
        if not kw.get("pdf"):
            return await super().__call__(**kw)
        man = pages.manual_path("bosch", "ZZ0000TEST", hid)
        man.parent.mkdir(parents=True, exist_ok=True)
        man.write_bytes(kw["pdf"])
        (man.parent / "pages.json").write_text("[]")
        home = flow.home_cards(hid)
        assert home is not None
        (home / "bosch").mkdir(parents=True, exist_ok=True)
        (home / "bosch" / "ZZ0000TEST.yaml").write_bytes(Path(BOSCH).read_bytes())
        (home / "bosch" / "ZZ0000TEST.check.json").write_text("{}")
        return await super().__call__(**kw)


def old_photo(photo_id: str, jpeg: bytes, household: str) -> None:
    """A plate photo kept before photos stopped being kept: a delete still removes it."""
    d = adding.plates_dir(household)
    assert d is not None
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{photo_id}.jpg").write_bytes(jpeg)


def files(root: Path) -> set[str]:
    return {str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()}


async def recorded(priv: Path, hid: str) -> str:
    """A session of this home that sent a plate photo, recorded as a live one is."""
    s = session(priv / "sessions", household=hid)
    s.start()
    s.plate_reader = Reader(Plate("bosch", "SMV4HAX40N", AMBER.fields[:1]))
    await s.handle(photo())
    await s.idle()
    (s.rec.dir / "frames" / "f1.jpg").write_bytes(b"\xff\xd8 a frame of the kitchen")
    await s.close()
    return s.session_id


async def test_deleting_a_household_leaves_none_of_its_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                             caplog: pytest.LogCaptureFixture) -> None:
    priv = tmp_path / "private"
    monkeypatch.setenv("FIXIT_PRIVATE", str(priv))
    conn = l5.open_store(":memory:")
    hid, other = add_household(conn), add_household(conn)
    fake = Uploads(ingested("checked", card_path=Path(BOSCH).resolve(), model="ZZ0000TEST"))
    client = calls.make_client("replay", fixtures_root=tmp_path / "fx", private=None)
    app = create_app(client=client, record_root=priv / "sessions", store=Store(conn, SECRET), ingest=fake)
    app.include_router(make_router(Deps(conn, SECRET, lambda _: None)), prefix="/api/v1")  # as the served app does
    tc = TestClient(app)

    sid = await recorded(priv, hid)
    (priv / "plates").mkdir(parents=True, exist_ok=True)
    (priv / "plates" / "pl_1.jpg").write_bytes(b"\xff\xd8 kept flat before homes had folders")
    old_photo("pl_o", b"\xff\xd8 the other home's plate", other)
    (priv / "plates" / "pl_x.jpg").write_bytes(b"\xff\xd8 a plate no recording of this home sent")
    ledger = Ledger(priv / "ledger.jsonl", priv / "budgets.toml")
    for sha, household, session_id in (("a" * 64, hid, sid), ("b" * 64, None, sid), ("c" * 64, other, None),
                                       ("d" * 64, hid, None), ("d" * 64, other, None),
                                       ("e" * 64, hid, None), ("e" * 64, None, None)):  # a test run's same body
        ledger.append({"request_sha256": sha, "household": household, "session_id": session_id, "usd": 0.01,
                       "lane": "pilot"})
        (priv / "requests").mkdir(exist_ok=True)
        (priv / "requests" / f"{sha}.json").write_text("{}")
        (priv / "fixtures" / "pilot").mkdir(parents=True, exist_ok=True)
        (priv / "fixtures" / "pilot" / f"{sha}.jsonl").write_text("{}")
    shown = display.keep(b"\xff\xd8 the home's display", hid)
    theirs = display.keep(b"\xff\xd8 the other home's display", other)
    for home in (hid, hid, other):
        rec = feedback.record(home, source="app", lang="en", answers=dict(NONE), quote="none", quote_name=None)
        feedback.append(priv, rec, deleting=erase.deleting)
    (priv / "fixtures-rerun" / "l1").mkdir(parents=True)
    (priv / "fixtures-rerun" / "l1" / f"{'a' * 64}.jsonl").write_text("{}")

    with tc:
        a = tc.post("/api/v1/appliances", headers=auth(hid), json={"brand": "bosch", "model": "ZZ0000TEST"}).json()
        r = tc.post(f"/api/v1/appliances/{a['id']}/manual", headers={**auth(hid), "content-type": "application/pdf"},
                    content=PDF)
        assert r.status_code == 201
        for _ in range(200):  # the upload's ingest runs in the background
            got = tc.get(f"/api/v1/appliances/{a['id']}", headers=auth(hid)).json()
            if len(fake.calls) == 2 and got.get("ingest", {"stage": "done"})["stage"] == "done":
                break
            time.sleep(0.01)
        before = files(priv)
        assert {f"sessions/{sid}/meta.json", f"sessions/{sid}/frames/f1.jpg", f"plates/homes/{hid}/pl_1.json",
                f"manuals/homes/{hid}/bosch/ZZ0000TEST.pdf", f"cards/homes/{hid}/bosch/ZZ0000TEST.yaml"} <= before
        with caplog.at_level(logging.INFO, logger="app.erase"):
            assert tc.delete("/api/v1/household", headers=auth(hid)).status_code == 204
        assert tc.get("/api/v1/household", headers=auth(hid)).status_code == 401

    left = files(priv)
    assert not [f for f in left if hid in f or sid in f]
    assert not [f for f in left if "a" * 64 in f or "b" * 64 in f]
    assert {"plates/pl_x.jpg", f"plates/homes/{other}/pl_o.jpg", f"requests/{'c' * 64}.json",
            f"requests/{'d' * 64}.json", f"fixtures/pilot/{'d' * 64}.jsonl",
            f"requests/{'e' * 64}.json"} <= left  # a body another home or a run with no home also sent stays
    assert "plates/pl_1.jpg" not in left and all(PDF not in (priv / f).read_bytes() for f in left)
    lines = ledger.path.read_text().splitlines()
    assert len(lines) == 7 and hid not in "".join(lines) and sid not in "".join(lines[:1])
    assert round(ledger.totals().total, 2) == 0.07  # every call's cost stays
    [msg] = [r.getMessage() for r in caplog.records if r.name == "app.erase"]
    assert msg.startswith("household deleted: sessions_open 0, ingests 0, sessions 3, plates 2,")
    assert "displays 1," in msg and "feedback_lines 2, ledger_lines 3" in msg
    assert f"displays/homes/{other}/{theirs}.jpg" in left and not [f for f in left if shown in f]
    [kept] = (priv / "feedback" / "feedback.jsonl").read_text().splitlines()
    assert json.loads(kept)["household"] == other  # another home's answers stay
    assert hid not in msg and "kitchen" not in msg and "ZZ0000TEST" not in msg


def test_a_second_delete_finds_nothing_and_a_bad_id_names_no_folder(tmp_path: Path,
                                                                   monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "private"))
    conn = l5.open_store(":memory:")
    hid = add_household(conn)
    old_photo("pl_1", b"\xff\xd8", hid)
    assert erase.household(conn, hid)["plates"] == 1
    assert erase.household(conn, hid) == dict.fromkeys(
        ("sessions_open", "ingests", "sessions", "plates", "cards", "manuals", "displays", "requests", "fixtures",
         "feedback_lines", "ledger_lines", "card_rows"), 0)
    with pytest.raises(ValueError):
        erase.household(conn, "../cards")


def served(priv: Path, conn: Any, ingest: Any = None) -> tuple[Any, TestClient]:
    client = calls.make_client("replay", fixtures_root=priv.parent / "fx", private=None)
    app = create_app(client=client, record_root=priv / "sessions", store=Store(conn, SECRET), ingest=ingest)
    app.include_router(make_router(Deps(conn, SECRET, lambda _: None)), prefix="/api/v1")
    return app, TestClient(app)


def closed(sock: Any) -> WebSocketDisconnect:
    with pytest.raises(WebSocketDisconnect) as e:
        while True:
            sock.receive_json()
    return e.value


def test_an_open_session_is_closed_before_the_sweep_and_no_new_one_starts(tmp_path: Path,
                                                                         monkeypatch: pytest.MonkeyPatch) -> None:
    priv = tmp_path / "private"
    monkeypatch.setenv("FIXIT_PRIVATE", str(priv))
    conn = l5.open_store(":memory:")
    hid, other = add_household(conn), add_household(conn)
    app, tc = served(priv, conn)
    token = l5.household_token(SECRET, hid)
    with tc, tc.websocket_connect("/ws?mode=capture") as sock, tc.websocket_connect("/ws?mode=capture") as theirs:
        sock.send_text(json.dumps(hello(household=token)))
        sid = sock.receive_json()["session_id"]
        theirs.send_text(json.dumps(hello(household=l5.household_token(SECRET, other))))
        their_sid = theirs.receive_json()["session_id"]
        assert (priv / "sessions" / sid / "meta.json").exists()
        assert tc.delete("/api/v1/household", headers=auth(hid)).status_code == 204
        e = closed(sock)
        assert e.code == erase.CLOSE_CODE == GONE["x-close-code"]
        assert GoneClose.model_validate_json(e.reason).error == "household_deleted"
        assert sid not in app.state.registry.sessions and their_sid in app.state.registry.sessions
        assert not (priv / "sessions" / sid).exists() and (priv / "sessions" / their_sid / "meta.json").exists()
        for again in ({"session_id": sid}, {}):  # neither a resume nor a new session starts
            with tc.websocket_connect("/ws?mode=capture") as late:
                late.send_text(json.dumps(hello(household=token, **again)))
                assert closed(late).code == erase.CLOSE_CODE
        with tc.websocket_connect("/ws?mode=capture") as forged:  # the id alone, unsigned, is not a deleted home
            forged.send_text(json.dumps(hello(household=f"{hid}-forged")))
            assert closed(forged).code == 1008
    assert not [f for f in files(priv) if hid in f or sid in f]


class Slow(FakeIngest):
    """An ingest still running when the household is deleted: it would write the manual after the sweep."""

    async def __call__(self, **kw: Any) -> Any:
        self.calls.append(kw)
        await asyncio.sleep(30)
        man = pages.manual_path("bosch", "ZZ0000TEST", kw["household"])
        man.parent.mkdir(parents=True, exist_ok=True)
        man.write_bytes(PDF)
        return self.result


def test_a_running_ingest_is_cancelled_before_the_sweep(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                        caplog: pytest.LogCaptureFixture) -> None:
    priv = tmp_path / "private"
    monkeypatch.setenv("FIXIT_PRIVATE", str(priv))
    conn = l5.open_store(":memory:")
    hid = add_household(conn)
    fake = Slow(ingested("checked", card_path=Path(BOSCH).resolve(), model="ZZ0000TEST"))
    _, tc = served(priv, conn, fake)
    with tc:
        tc.post("/api/v1/appliances", headers=auth(hid), json={"brand": "bosch", "model": "ZZ0000TEST"})
        for _ in range(200):
            if fake.calls:
                break
            time.sleep(0.01)
        with caplog.at_level(logging.INFO, logger="app.erase"):
            assert tc.delete("/api/v1/household", headers=auth(hid)).status_code == 204
    assert "ingests 1" in caplog.records[-1].getMessage()
    assert not (priv / "manuals").exists() or not files(priv / "manuals")


def test_only_an_admin_deletes_and_a_refused_delete_removes_nothing(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.l1.test_library import bearer, person

    priv = tmp_path / "private"
    monkeypatch.setenv("FIXIT_PRIVATE", str(priv))
    conn = l5.open_store(":memory:")
    hid = add_household(conn)
    old_photo("pl_1", b"\xff\xd8", hid)
    _, tc = served(priv, conn)
    with tc:
        for token in (person(conn, hid), person(conn, hid, view_only=True)):
            assert tc.delete("/api/v1/household", headers=bearer(token)).status_code == 403
        assert files(priv) == {f"plates/homes/{hid}/pl_1.jpg"} and not erase.deleting(hid)
        assert tc.delete("/api/v1/household", headers=auth(hid)).status_code == 204
    assert files(priv) == set()


def test_its_own_cards_leave_the_library_and_a_card_it_shared_stays(tmp_path: Path,
                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    priv = tmp_path / "private"
    monkeypatch.setenv("FIXIT_PRIVATE", str(priv))
    conn = l5.open_store(":memory:")
    hid, other = add_household(conn), add_household(conn)
    aid = add_appliance(conn, hid, "dw")

    def row(path: str, built_by: str | None, shared: bool) -> None:
        conn.execute("INSERT INTO card_library (card_path, brand, model, card_sha256, origin, covers, shared, reason,"
                     " built_by, built_at) VALUES (?, 'bosch', 'ZZ0000TEST', ?, ?, ?, ?, ?, ?, ?)",
                     (path, "0" * 64, "maker_site" if shared else "household_upload", "exact" if shared else "none",
                      int(shared), None if shared else "uploaded", built_by, "2026-10-27T12:00:00+00:00"))

    upload = f"{priv}/cards/homes/{hid}/bosch/ZZ0000TEST.yaml"
    archived = f"{priv}/cards/homes/{hid}/bosch/archive/v1.yaml"
    theirs, shared = f"{priv}/cards/homes/{other}/bosch/ZZ0000TEST.yaml", "cards/bosch/SMV4HAX40N.yaml"
    row(upload, hid, False)
    row(archived, None, False)  # an older version: only its path names the home
    row(theirs, other, False)
    row(shared, hid, True)
    conn.execute("INSERT INTO card_reports (id, household_id, appliance_id, card_path, card_sha256, kind, note,"
                 " status, by_label, created_at) VALUES ('r1', ?, ?, ?, ?, 'other', 'the filter looks different',"
                 " 'open', 'Admin', '2026-10-27T12:00:00+00:00')", (hid, aid, upload, "0" * 64))
    assert erase.household(conn, hid)["card_rows"] == 2
    left = {r[0]: r[1] for r in conn.execute("SELECT card_path, built_by FROM card_library")}
    assert left == {theirs: other, shared: None}  # the shared card no longer names who built it
    assert conn.execute("SELECT count(*) FROM card_reports").fetchone()[0] == 0

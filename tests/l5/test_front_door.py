"""The front door (contracts 1.28): a home opened without an invite code, through the open door or a judges' link,
and the return link that opens a home on another device.

The door is shut with no door_log row and after a close row; only the operator verb writes a row. A judges' link
keeps its caps in its own row, set by whoever makes it. Every refusal is the contract's body."""

from __future__ import annotations

import hashlib
import re
import sqlite3
import stat
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from app.feeds.errors import install_errors
from app.feeds.router import Deps, PublicOrigin, make_router
from app.schedule import door, invites, store
from contracts.models.card import Card
from tests.l5.conftest import ROOT, T0
from tests.l5.test_pilot_door import Clock, join

SPEC = yaml.safe_load((ROOT / "contracts/api.openapi.yaml").read_text())
REG = Registry().with_resource("urn:api", Resource.opaque(SPEC))
CREATED, CAP, DOOR_STATE = (Draft202012Validator({"$ref": f"urn:api#/components/schemas/{n}"}, registry=REG)
                             for n in ("HouseholdCreated", "CapError", "DoorState"))
DOOR = {"consent": True, "age_ok": True, "lang": "en"}


class Pools:
    """calls.pool_open as the tests set it: open, or closed until a time."""
    def __init__(self) -> None:
        self.closed: dict[str, datetime | None] = {}
        self.asked: list[str] = []

    def __call__(self, origin: PublicOrigin) -> tuple[bool, datetime | None]:
        self.asked.append(origin)
        return (False, self.closed[origin]) if origin in self.closed else (True, None)


@pytest.fixture
def clock() -> Clock:
    return Clock(T0)


@pytest.fixture
def pools() -> Pools:
    return Pools()


@pytest.fixture
def spent() -> dict[str, float]:
    return {}


@pytest.fixture
def api(conn: sqlite3.Connection, secret: bytes, loader: Callable[[str], Card | None], clock: Clock, pools: Pools,
        spent: dict[str, float]) -> Iterator[TestClient]:
    deps = Deps(conn, secret, loader, None, clock, invite_failures_per_hour=50, pool_open=pools,
                spend=lambda hids: sum(spent.get(h, 0.0) for h in hids))
    app = FastAPI()
    app.include_router(make_router(deps), prefix="/api/v1")
    install_errors(app)
    with TestClient(app, base_url="https://fixit.test") as c:
        yield c


def homes(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT count(*) FROM households").fetchone()[0])


def verb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *argv: str) -> None:
    monkeypatch.setenv("FIXIT_DB", str(tmp_path / "store.sqlite"))
    # the verb makes a link "now"; now is T0, the clock the api runs on, so a link's expiry never meets the wall clock
    new_link = door.new_judges_link
    monkeypatch.setattr(door, "new_judges_link", lambda conn, **kw: new_link(conn, **{"at": T0, **kw}))
    invites.main(list(argv))


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- the open door ----------------------------------------------------------------------------------------------

def test_the_door_is_shut_with_no_row_and_with_a_close_row(api: TestClient, conn: sqlite3.Connection) -> None:
    def refused() -> None:
        before = homes(conn)
        r = api.post("/api/v1/households", json=DOOR)
        assert r.status_code == 403 and r.json()["error"] == "door_closed", r.text
        assert homes(conn) == before

    refused()  # no row: shut
    door.set_door(conn, "open", by="Sam", at=T0)
    door.set_door(conn, "closed", by="Sam", at=T0 + timedelta(minutes=1))
    refused()  # the latest row is a close
    join(api, conn)  # an invite code still opens a home with the door shut


def test_through_the_open_door_a_home_and_its_first_admin(api: TestClient, conn: sqlite3.Connection,
                                                         pools: Pools) -> None:
    door.set_door(conn, "open", by="Sam", at=T0)
    assert api.post("/api/v1/households", json={"consent": True}).status_code == 422  # no code: age_ok is required
    r = api.post("/api/v1/households", json=DOOR)
    assert r.status_code == 201, r.text
    CREATED.validate(r.json())
    hid = r.json()["id"]
    row = conn.execute("SELECT * FROM households WHERE id = ?", (hid,)).fetchone()
    assert (row["joined_via"], row["invite_link_id"], row["is_test"], row["lang"]) == ("door", None, 0, "en")
    assert row["invite_code"] not in r.text  # the code nobody is shown
    assert store.first_admin(conn, hid) is not None and pools.asked == ["door"]
    assert api.get("/api/v1/household", headers=bearer(r.json()["token"])).json()["id"] == hid


def test_the_open_door_turns_away_when_the_free_credits_are_gone(api: TestClient, conn: sqlite3.Connection,
                                                                pools: Pools) -> None:
    door.set_door(conn, "open", by="Sam", at=T0)
    pools.closed["door"] = datetime(2026, 10, 28, tzinfo=UTC)
    r = api.post("/api/v1/households", json=DOOR)
    assert r.status_code == 429 and homes(conn) == 0
    CAP.validate(r.json())
    assert r.json()["error"] == "global_cap" and r.json()["reopens_at"] == "2026-10-28T00:00:00Z"
    pools.closed["door"] = None  # a one-off pool: no time is known
    assert api.post("/api/v1/households", json=DOOR).json()["reopens_at"] is None


def test_only_the_operator_verb_writes_a_door_row(api: TestClient, conn: sqlite3.Connection, tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch,
                                                 capsys: pytest.CaptureFixture[str]) -> None:
    # no code path but the verb: one function writes the table, and only the verb calls it
    sources = [p for d in ("app", "calls", "deploy", "integrations") for p in (ROOT / d).rglob("*.py")]
    writes = re.compile(r"(INSERT|REPLACE|UPDATE|DELETE)\b[^\"]*\bdoor_log\b", re.I)
    assert [p.relative_to(ROOT).as_posix() for p in sources if writes.search(p.read_text())] == ["app/schedule/door.py"]
    assert len(writes.findall((ROOT / "app/schedule/door.py").read_text())) == 1
    callers = [p.relative_to(ROOT).as_posix() for p in sources if re.search(r"\bset_door\(", p.read_text())]
    assert sorted(callers) == ["app/schedule/door.py", "app/schedule/invites.py"]
    # no route writes one, whatever is posted
    for body in (DOOR, {**DOOR, "door": "open"}, {"consent": True, "invite_code": "NOPE"}):
        api.post("/api/v1/households", json=body)
    _, auth = join(api, conn)
    api.patch("/api/v1/household", json={"door": "open"}, headers=auth)
    assert door.door(conn) is None
    # the verb: who and when, and the latest row wins
    with pytest.raises(SystemExit):
        verb(tmp_path, monkeypatch, "door", "open")  # who decided is not optional
    verb(tmp_path, monkeypatch, "door")
    assert capsys.readouterr().out.strip() == "door: shut (never opened)"
    verb(tmp_path, monkeypatch, "door", "open", "--by", "Sam")
    verb(tmp_path, monkeypatch, "door", "close", "--by", "Sam")
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("door: open since ") and out[0].endswith(", by Sam")
    assert out[1].startswith("door: shut since ")
    other = store.open_store(tmp_path / "store.sqlite")
    rows = other.execute("SELECT state, by, at FROM door_log ORDER BY id").fetchall()
    assert [(r["state"], r["by"]) for r in rows] == [("open", "Sam"), ("closed", "Sam")] and all(r["at"] for r in rows)
    assert not door.door_open(other)


def test_door_homes_stop_per_address_and_per_day_until_midnight(conn: sqlite3.Connection, secret: bytes,
                                                                 loader: Callable[[str], Card | None], clock: Clock,
                                                                 pools: Pools) -> None:
    deps = Deps(conn, secret, loader, None, clock, pool_open=pools, door_homes_per_address_day=2,
                door_homes_per_day=3)
    app = FastAPI()
    app.include_router(make_router(deps), prefix="/api/v1")
    install_errors(app)
    door.set_door(conn, "open", by="Sam", at=T0)
    here, there = (TestClient(app, base_url="https://fixit.test", client=(ip, 5000)) for ip in ("10.0.0.1", "10.0.0.2"))
    assert [here.post("/api/v1/households", json=DOOR).status_code for _ in range(2)] == [201, 201]
    r = here.post("/api/v1/households", json=DOOR)
    assert r.status_code == 429 and r.json()["error"] == "address_cap"
    CAP.validate(r.json())
    assert r.json()["reopens_at"] == "2026-10-28T00:00:00Z"
    assert here.get("/api/v1/households/door").json() == {"state": "capped", "reopens_at": "2026-10-28T00:00:00Z"}
    assert there.get("/api/v1/households/door").json() == {"state": "open", "reopens_at": None}
    assert there.post("/api/v1/households", json=DOOR).status_code == 201  # three today, in all
    r = there.post("/api/v1/households", json=DOOR)
    assert r.status_code == 429 and r.json()["error"] == "door_cap" and homes(conn) == 3
    CAP.validate(r.json())
    assert r.json()["reopens_at"] == "2026-10-28T00:00:00Z"
    clock.t = datetime(2026, 10, 28, 0, 0, tzinfo=UTC)  # a new UTC day
    assert here.post("/api/v1/households", json=DOOR).status_code == 201
    pools.closed["door"] = None
    assert there.get("/api/v1/households/door").json() == {"state": "capped", "reopens_at": None}


def test_the_door_state_reads_only(api: TestClient, conn: sqlite3.Connection, pools: Pools) -> None:
    assert api.get("/api/v1/households/door").json() == {"state": "shut", "reopens_at": None}
    door.set_door(conn, "open", by="Sam", at=T0)
    assert api.get("/api/v1/households/door").json() == {"state": "open", "reopens_at": None}
    pools.closed["door"] = datetime(2026, 10, 28, tzinfo=UTC)
    assert api.get("/api/v1/households/door").json() == {"state": "capped", "reopens_at": "2026-10-28T00:00:00Z"}
    pools.closed["door"] = None
    for state in (api.get("/api/v1/households/door").json(), {"state": "shut", "reopens_at": None},
                  {"state": "open", "reopens_at": None}):
        DOOR_STATE.validate(state)
    assert homes(conn) == 0 and door.door(conn)["state"] == "open"  # nothing made, nothing written


# ---- a judges' link ---------------------------------------------------------------------------------------------

def make_link(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], *,
              homes_: int = 3, total: float = 5.0, expires: str = "2026-11-10") -> tuple[str, str]:
    out = tmp_path / f"link-{homes_}-{total}-{expires}.txt"
    verb(tmp_path, monkeypatch, "link", "new", "--judges", "--label", "test link", "--cap-home", "1",
         "--total", str(total), "--homes", str(homes_), "--expires", expires, "--out", str(out),
         "--origin", "https://fixit.test/")
    printed = capsys.readouterr().out
    url = out.read_text().strip()
    code = url.split("#k=", 1)[1]
    assert url == f"https://fixit.test/try#k={code}" and code not in printed  # the code is never on screen
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    return printed.split()[0], code


@pytest.fixture
def link_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, secret: bytes, loader: Callable[[str], Card | None],
             clock: Clock, pools: Pools, spent: dict[str, float]) -> Iterator[tuple[TestClient, sqlite3.Connection]]:
    """The api over the store the verb writes to."""
    conn = store.open_store(tmp_path / "store.sqlite")
    deps = Deps(conn, secret, loader, None, clock, invite_failures_per_hour=50, pool_open=pools,
                spend=lambda hids: sum(spent.get(h, 0.0) for h in hids))
    app = FastAPI()
    app.include_router(make_router(deps), prefix="/api/v1")
    install_errors(app)
    with TestClient(app, base_url="https://fixit.test") as c:
        yield c, conn


def scan(api: TestClient, code: str, **extra: Any) -> Any:
    return api.post("/api/v1/households/try", json={"code": code, "consent": True, "age_ok": True, **extra})


def test_a_judges_link_opens_its_own_home_per_scan(link_api: tuple[TestClient, sqlite3.Connection], tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch, pools: Pools,
                                                  capsys: pytest.CaptureFixture[str]) -> None:
    api, conn = link_api
    lid, code = make_link(tmp_path, monkeypatch, capsys)
    stored = conn.execute("SELECT * FROM invite_links WHERE id = ?", (lid,)).fetchone()
    assert stored["code_sha256"] == hashlib.sha256(code.encode()).hexdigest() and code not in tuple(stored)
    assert stored["expires_at"] == "2026-11-10T23:00:00+00:00"  # the end of 10 Nov in Amsterdam
    a, b = scan(api, code, lang="en"), scan(api, code)
    assert a.status_code == b.status_code == 201, a.text
    CREATED.validate(a.json())
    assert a.json()["id"] != b.json()["id"] and a.json()["cap_usd_day"] == 1.0
    row = conn.execute("SELECT * FROM households WHERE id = ?", (a.json()["id"],)).fetchone()
    assert (row["joined_via"], row["invite_link_id"], row["is_test"]) == ("link", lid, 0)
    assert pools.asked == ["judges", "judges"]
    assert scan(api, code, extra="x").status_code == 422
    assert api.post("/api/v1/households/try", json={"code": code, "consent": True}).status_code == 422  # age_ok


def test_a_judges_link_stops_at_its_homes_and_its_total(link_api: tuple[TestClient, sqlite3.Connection],
                                                       tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pools: Pools,
                                                       spent: dict[str, float],
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    api, conn = link_api
    _, code = make_link(tmp_path, monkeypatch, capsys, homes_=2, total=5.0)
    first = scan(api, code).json()["id"]
    spent[first] = 5.0  # the link's total is spent
    r = scan(api, code)
    assert r.status_code == 429 and r.json()["error"] == "judges_cap"
    assert r.json()["reopens_at"] is None  # the link's total, ever: it does not reopen
    CAP.validate(r.json())
    spent[first] = 4.0
    pools.closed["judges"] = None
    r = scan(api, code)
    assert r.status_code == 429 and r.json() == {**r.json(), "error": "global_cap", "reopens_at": None}
    del pools.closed["judges"]
    assert scan(api, code).status_code == 201
    r = scan(api, code)  # two homes: full, for good
    assert r.status_code == 429 and r.json()["error"] == "judges_full" and r.json()["reopens_at"] is None
    CAP.validate(r.json())
    assert int(conn.execute("SELECT count(*) FROM households").fetchone()[0]) == 2


def test_unknown_expired_and_revoked_links_get_one_answer(link_api: tuple[TestClient, sqlite3.Connection],
                                                         tmp_path: Path, monkeypatch: pytest.MonkeyPatch, clock: Clock,
                                                         capsys: pytest.CaptureFixture[str]) -> None:
    api, _ = link_api
    lid, revoked = make_link(tmp_path, monkeypatch, capsys)
    verb(tmp_path, monkeypatch, "link", "revoke", lid)
    _, live = make_link(tmp_path, monkeypatch, capsys, expires="2026-10-28")
    answers = [scan(api, "not-a-code"), scan(api, revoked)]
    clock.t = datetime(2026, 10, 28, 23, 0, tzinfo=UTC)  # midnight in Amsterdam: expired
    answers.append(scan(api, live))
    assert {(a.status_code, a.text) for a in answers} == {(404, answers[0].text)}
    with pytest.raises(SystemExit):
        verb(tmp_path, monkeypatch, "link", "new", "--judges", "--label", "x", "--cap-home", "1", "--total", "1",
             "--homes", "1", "--expires", "2026-11-10", "--out", str(tmp_path / "link-3-5.0-2026-11-10.txt"))


def test_link_new_needs_every_cap_from_its_maker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    full = ["link", "new", "--judges", "--label", "x", "--cap-home", "1", "--total", "1", "--homes", "1",
            "--expires", "2026-11-10", "--out", str(tmp_path / "c.txt")]
    for flag in ("--cap-home", "--total", "--homes", "--expires"):  # no default stands in for a decision
        i = full.index(flag)
        with pytest.raises(SystemExit):
            verb(tmp_path, monkeypatch, *full[:i], *full[i + 2:])
    assert not (tmp_path / "c.txt").exists()


# ---- the return link --------------------------------------------------------------------------------------------

def test_a_return_link_opens_the_home_once_as_the_same_person(api: TestClient, conn: sqlite3.Connection,
                                                             clock: Clock) -> None:
    hid, auth = join(api, conn)
    r = api.post("/api/v1/household/return-link", headers=auth)
    assert r.status_code == 201, r.text
    url = r.json()["url"]
    assert re.fullmatch(r"https://fixit\.test/#return/[\w-]+", url)
    assert r.json()["expires_at"] == "2026-10-27T12:15:00Z"
    code = url.rsplit("/", 1)[1]
    assert conn.execute("SELECT count(*) FROM return_codes WHERE code_sha256 = ?",
                        (hashlib.sha256(code.encode()).hexdigest(),)).fetchone()[0] == 1
    back = api.post("/api/v1/households/return", json={"code": code})
    assert back.status_code == 201, back.text
    CREATED.validate(back.json())
    assert back.json()["id"] == hid and back.json()["token"] != auth["Authorization"][7:]
    other = store.caller_from_token(conn, b"", back.json()["token"])
    admin = store.first_admin(conn, hid)
    assert other is not None and admin is not None
    assert (other.by_kind, other.person_id) == ("app", admin["id"])
    renter = api.patch("/api/v1/household", json={"renter": True}, headers=bearer(back.json()["token"]))
    assert renter.status_code == 200  # an admin again, on the new phone
    again = api.post("/api/v1/households/return", json={"code": code})
    assert again.status_code == 404  # single use
    first = api.post("/api/v1/household/return-link", headers=auth).json()["url"].rsplit("/", 1)[1]
    second = api.post("/api/v1/household/return-link", headers=auth).json()["url"].rsplit("/", 1)[1]
    assert api.post("/api/v1/households/return", json={"code": first}).text == again.text  # replaced
    clock.t = T0 + timedelta(minutes=15)
    assert api.post("/api/v1/households/return", json={"code": second}).text == again.text  # expired
    assert api.post("/api/v1/households/return", json={"code": "x", "y": 1}).status_code == 422


def member(conn: sqlite3.Connection, hid: str, *, view_only: bool) -> tuple[str, dict[str, str]]:
    pid = "p-m" + ("v" if view_only else "w")
    conn.execute("INSERT INTO people (id, household_id, name, role, view_only, created_at) VALUES"
                 " (?, ?, ?, 'member', ?, ?)", (pid, hid, pid, int(view_only), store.now_iso(T0)))
    return pid, bearer(door.issue_app_token(conn, hid, pid, at=T0))


def test_who_may_make_a_return_link(api: TestClient, conn: sqlite3.Connection) -> None:
    hid, auth = join(api, conn)
    for kind in ("agent", "ha"):
        _, tok = store.issue_token(conn, hid, by_kind=kind, scope="write", label=kind, at=T0)  # type: ignore[arg-type]
        assert api.post("/api/v1/household/return-link", headers=bearer(tok)).status_code == 403
    _, viewer = member(conn, hid, view_only=True)
    assert api.post("/api/v1/household/return-link", headers=viewer).status_code == 403
    pid, writer = member(conn, hid, view_only=False)
    code = api.post("/api/v1/household/return-link", headers=writer).json()["url"].rsplit("/", 1)[1]
    admin_code = api.post("/api/v1/household/return-link", headers=auth).json()["url"].rsplit("/", 1)[1]
    tok = api.post("/api/v1/households/return", json={"code": code}).json()["token"]
    them = store.caller_from_token(conn, b"", tok)
    assert them is not None and them.person_id == pid  # the member comes back as the member, not the admin
    assert api.post("/api/v1/households/return", json={"code": admin_code}).status_code == 201  # each their own
    late = api.post("/api/v1/household/return-link", headers=writer).json()["url"].rsplit("/", 1)[1]
    conn.execute("UPDATE people SET removed_at = ? WHERE id = ?", (store.now_iso(T0), pid))
    assert api.post("/api/v1/households/return", json={"code": late}).status_code == 404  # they left the home
    elsewhere, _ = join(api, conn)  # a code whose person is not in its household brings back nobody
    stray, _ = door.new_return_code(conn, elsewhere, store.first_admin(conn, hid)["id"], at=T0)
    assert api.post("/api/v1/households/return", json={"code": stray}).status_code == 404


def test_a_returned_phone_works_on_the_appliance_routes(tmp_path: Path, conn: sqlite3.Connection, secret: bytes,
                                                       loader: Callable[[str], Card | None], clock: Clock) -> None:
    """The token a return link gives is a person's app token: the appliance routes take it as they take the
    household's, and a view-only person may read there but not change anything (403, not signed out)."""
    import calls
    from app.server import create_app
    from app.store import Store

    app = create_app(client=calls.make_client("replay", fixtures_root=tmp_path / "fx", private=None),
                     record_root=tmp_path / "sessions", store=Store(conn, secret))
    if not any(getattr(r, "path", "") == "/api/v1/households" for r in app.routes):
        app.include_router(make_router(Deps(conn, secret, loader, None, clock)), prefix="/api/v1")
    install_errors(app)
    api = TestClient(app, base_url="https://fixit.test")
    hid, auth = join(api, conn)
    code = api.post("/api/v1/household/return-link", headers=auth).json()["url"].rsplit("/", 1)[1]
    phone = bearer(api.post("/api/v1/households/return", json={"code": code}).json()["token"])
    assert api.get("/api/v1/appliances", headers=phone).status_code == 200
    assert api.get("/api/v1/due", headers=phone).status_code == 200
    made = api.post("/api/v1/appliances", json={"model": "WM-0000", "alias": "washer"}, headers=phone)
    assert made.status_code not in (401, 403), made.text
    _, viewer = member(conn, hid, view_only=True)
    assert api.get("/api/v1/appliances", headers=viewer).status_code == 200
    assert api.post("/api/v1/appliances", json={"model": "WM-0000"}, headers=viewer).status_code == 403
    assert api.get("/api/v1/appliances", headers=bearer("fxt_nope")).status_code == 401

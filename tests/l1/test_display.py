"""The camera read of a display or a light (app.display, POST /appliances/{id}/display): the model may answer only
with the card's own codes and indicator rows, code checks its answer again, and the user reads the card's cited rows
through the same lookup as a typed code. The photo stays in the private folder; a refused call is the contract's
CapError. No live call: a stub stands in for the model."""

from __future__ import annotations

import json
import stat
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import calls
import rules
from app import cards, display
from app.api import cap_error
from app.schedule import store as l5
from app.server import create_app
from app.store import Store
from contracts.models.card import Card
from contracts.models.safety import Ok
from tests.l1.test_api import BOSCH, SECRET, auth
from tests.l1.test_faults import bosch, conforms, row, tabled  # noqa: F401  (fixtures)
from tests.l5.conftest import add_appliance, add_household

JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
DISPLAY = "/appliances/{appliance_id}/display"


class Reply:
    def __init__(self, data: Any) -> None:
        self.data = data

    def parsed(self) -> Any:
        return self.data


class Stub:
    """The model's reply, and what it was sent."""

    def __init__(self, data: Any = None, error: Exception | None = None) -> None:
        self.data, self.error, self.sent = data, error, []  # type: ignore[var-annotated]

    async def respond(self, role: str, input: Any, **kw: Any) -> Reply:
        self.sent.append({"role": role, "input": input, **kw})
        if self.error is not None:
            raise self.error
        return Reply(self.data)


async def test_a_read_code_answers_as_a_typed_one(bosch: Any) -> None:  # noqa: F811
    stub = Stub({"code": "E:32-00", "row_id": None, "uncertain": False})
    got = await display.read(JPEG, bosch, client=stub, lane="l1", household="h", household_cap_usd=0.45)  # type: ignore[arg-type]
    assert got == {"code": "E:32-00", "row_id": None, "uncertain": False}
    out = display.answer(bosch, bosch, got, "en", renter=False, country="nl")
    assert out["matches"][0]["job_id"] == "fix_e3200_inlet_sieve" and out["read"] == got
    conforms(out, DISPLAY, "post")


def test_a_light_for_the_landlords_job_says_so_to_a_renter(bosch: Any, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    raw = Card.model_validate({**bosch.model_dump(mode="json"), "troubleshooting": [
        row("sieve_light", [], job="fix_e3200_inlet_sieve", indicator="The tap symbol lights up.")]})
    card = cards.check_card(raw, "nl", None).card
    got = {"code": None, "row_id": "sieve_light", "uncertain": False}
    monkeypatch.setattr(rules, "landlord_jobs", lambda card, country: {"fix_e3200_inlet_sieve": None})
    assert display.answer(card, raw, got, "en", renter=True, country="nl")["landlord"] is True
    assert display.answer(card, raw, got, "en", renter=False, country="nl")["landlord"] is False


async def test_the_model_may_answer_only_with_the_cards_own_codes_and_lights(tabled: Any) -> None:  # noqa: F811
    card, _ = tabled
    stub = Stub({"code": None, "row_id": "tap_light", "uncertain": False})
    await display.read(JPEG, card, client=stub, lane="l1", household="h", household_cap_usd=0.45)  # type: ignore[arg-type]
    sent = stub.sent[0]
    assert sent["role"] == "plate" and sent["images"] == [JPEG] and sent["household_cap_usd"] == 0.45
    props = sent["format"]["schema"]["properties"]
    assert props["code"]["anyOf"][0]["enum"] == ["E:32-00", "E:15-00", "E:25-00", "E:44-00", "E:92-40", "E:01-00",
                                                "E:90-10"]
    assert props["row_id"]["anyOf"][0]["enum"] == ["tap_light"] and sent["format"]["strict"] is True
    assert "The tap symbol lights up." in sent["input"]


async def test_a_read_light_is_its_row(tabled: Any) -> None:  # noqa: F811
    card, raw = tabled
    stub = Stub({"code": None, "row_id": "tap_light", "uncertain": False})
    got = await display.read(JPEG, card, client=stub, lane="l1", household="h", household_cap_usd=None)  # type: ignore[arg-type]
    out = display.answer(card, raw, got, "en", renter=False, country="nl")
    m = out["matches"][0]
    assert m["row_id"] == "tap_light" and m["code"] is None and m["remedy"] and out["notice"] is None
    conforms(out, DISPLAY, "post")


@pytest.mark.parametrize("data", [{"code": "E:99-99", "row_id": "nope", "uncertain": False}, None, "E:32-00",
                                  {"code": None, "row_id": None, "uncertain": False}])
async def test_an_answer_off_the_card_is_no_read_and_says_so(bosch: Any, data: Any) -> None:  # noqa: F811
    got = await display.read(JPEG, bosch, client=Stub(data), lane="l1", household="h", household_cap_usd=None)  # type: ignore[arg-type]
    assert got == {"code": None, "row_id": None, "uncertain": True}
    out = display.answer(bosch, bosch, got, "nl", renter=False, country="nl")
    assert out["matches"] == [] and out["notice"] == display.UNREAD["nl"]


def test_the_unread_line_passes_g0(bosch: Any) -> None:  # noqa: F811
    for lang, text in display.UNREAD.items():
        assert isinstance(rules.check_sentence(text, lang, bosch, Ok(state="OK"), country="nl"), rules.Pass)


def test_cap_error_is_the_contracts() -> None:
    now = datetime(2026, 10, 27, 22, 0, tzinfo=UTC)
    assert cap_error("household h1: $0.45 today", now) == {"error": "home_cap", "reopens_at": "2026-10-28T00:00:00Z"}
    assert cap_error("total: $40 spent") == {"error": "global_cap", "reopens_at": None}


@pytest.fixture
def api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "private"))
    conn = l5.open_store(":memory:")
    a, b = add_household(conn), add_household(conn)
    ids = {"a": a, "b": b, "wa": add_appliance(conn, a, "washer", BOSCH)}
    stub = Stub({"code": "E:92-40", "row_id": None, "uncertain": False})
    tc = TestClient(create_app(client=stub, record_root=tmp_path / "s", store=Store(conn, SECRET)))  # type: ignore[arg-type]
    return tc, ids, stub, tmp_path


def post(tc: TestClient, aid: str, hid: str, body: bytes = JPEG, ctype: str = "image/jpeg") -> Any:
    return tc.post(f"/api/v1/appliances/{aid}/display", content=body,
                   headers={**auth(hid), "Content-Type": ctype})


def test_the_route_reads_keeps_the_photo_and_answers(api: Any) -> None:
    tc, ids, stub, tmp = api
    r = post(tc, ids["wa"], ids["a"])
    assert r.status_code == 200 and r.json()["matches"][0]["job_id"] == "fix_e9240_clean_sieves"
    conforms(r.json(), DISPLAY, "post")
    kept = list((tmp / "private" / "displays").rglob("*.jpg"))
    assert [k.parent for k in kept] == [tmp / "private" / "displays" / "homes" / ids["a"]]  # the home's own folder
    assert kept[0].read_bytes() == JPEG and stub.sent[0]["household"] == ids["a"]
    assert stat.S_IMODE(kept[0].stat().st_mode) == 0o600
    assert {stat.S_IMODE(d.stat().st_mode) for d in (kept[0].parent, *kept[0].parents[:2])} == {0o700}


def test_untrace_moves_flat_photos_aside_and_deletes_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    priv = tmp_path / "private"
    monkeypatch.setenv("FIXIT_PRIVATE", str(priv))
    assert display.untrace(date(2026, 10, 29)) == 0 and not (priv / "displays").exists()
    (priv / "displays").mkdir(parents=True)
    for name in ("a", "b"):
        (priv / "displays" / f"{name}.jpg").write_bytes(JPEG + name.encode())
    home = priv / "displays" / "homes" / "h_1"
    home.mkdir(parents=True)
    (home / "c.jpg").write_bytes(JPEG)
    assert display.untrace(date(2026, 10, 29)) == 2
    to = priv / "displays" / "untraced-2026-10-29"
    assert sorted(p.name for p in to.iterdir()) == ["a.jpg", "b.jpg"] and (to / "b.jpg").read_bytes() == JPEG + b"b"
    assert stat.S_IMODE(to.stat().st_mode) == 0o700 and stat.S_IMODE((to / "a.jpg").stat().st_mode) == 0o600
    assert not list((priv / "displays").glob("*.jpg")) and (home / "c.jpg").exists()  # a home's photos stay put
    assert display.untrace(date(2026, 10, 30)) == 0  # a second run finds nothing to move


@pytest.mark.parametrize(("body", "ctype", "status", "error"), [
    (JPEG, "image/png", 415, "not_an_image"), (b"%PDF-1.7", "image/jpeg", 415, "not_an_image"),
    (b"\xff\xd8\xff" + b"\x00" * display.MAX_BYTES, "image/jpeg", 413, "too_large")])
def test_the_route_refuses_what_is_not_a_photo(api: Any, body: bytes, ctype: str, status: int, error: str) -> None:
    tc, ids, stub, tmp = api
    r = post(tc, ids["wa"], ids["a"], body, ctype)
    assert r.status_code == status and r.json()["error"] == error and stub.sent == []
    assert not (tmp / "private" / "displays").exists()


def test_another_households_appliance_is_a_404_and_no_call(api: Any) -> None:
    tc, ids, stub, _ = api
    assert post(tc, ids["wa"], ids["b"]).status_code == 404 and stub.sent == []
    assert tc.post(f"/api/v1/appliances/{ids['wa']}/display", content=JPEG,
                   headers={"Content-Type": "image/jpeg"}).status_code == 401


def test_a_refused_call_is_a_cap_error(api: Any) -> None:
    tc, ids, stub, _ = api
    stub.error = calls.BudgetExceeded("household h: $0.46 today + $0.01 estimate > cap $0.45")
    r = post(tc, ids["wa"], ids["a"])
    assert r.status_code == 429 and r.json()["error"] == "home_cap" and r.json()["reopens_at"].endswith("T00:00:00Z")
    assert json.dumps(r.json()).count("$") == 0  # the ledger's figures stay on the server

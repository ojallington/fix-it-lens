"""What a typed fault code means (app.faults, GET /appliances/{id}/meaning): the card's fix job for it, found however
the code is typed; the codes to pick from when it is partial; a technician's job when the rules refused its fix; and
a fixed line with the troubleshooting page when the manual does not list it. No model is called."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema
import pytest
import yaml
from fastapi.testclient import TestClient

import calls
import rules
from app import cards, faults
from app.schedule import store as l5
from app.server import create_app
from app.store import Store
from contracts.models.card import Card, FaultCode
from contracts.models.safety import Ok
from tests.l1.test_api import BOSCH, SECRET, auth
from tests.l5.conftest import add_appliance, add_household

ROOT = Path(__file__).resolve().parents[2]
SPEC = yaml.safe_load((ROOT / "contracts/api.openapi.yaml").read_text())
ROW_FIELDS = ("row_id", "meaning", "cause", "remedy", "remedy_kind", "escalate_to", "symbol")


def conforms(out: dict[str, Any], path: str = "/appliances/{appliance_id}/meaning", method: str = "get") -> None:
    """The answer against the contract's own 200 schema for that route."""
    schema = SPEC["paths"][path][method]["responses"]["200"]["content"]["application/json"]["schema"]
    jsonschema.Draft202012Validator({**schema, "components": SPEC["components"]}).validate(out)


@pytest.fixture
def world(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    """Two households: a has the Bosch; b has an appliance with no checked card."""
    conn = l5.open_store(":memory:")
    a, b = add_household(conn), add_household(conn)
    ids = {"a": a, "b": b, "wa": add_appliance(conn, a, "washer", BOSCH), "wb": add_appliance(conn, b, "washer")}
    client = calls.make_client("replay", fixtures_root=tmp_path / "fx", private=None)
    return TestClient(create_app(client=client, record_root=tmp_path / "s", store=Store(conn, SECRET))), ids


@pytest.fixture(scope="module")
def bosch() -> Any:
    return cards.read(ROOT / BOSCH)


@pytest.mark.parametrize("typed", ["E:32-00", "e3200", "E 32 00", " E.32/00 ", "E:32-OO"])
def test_a_code_is_found_however_it_is_typed(bosch: Any, typed: str) -> None:
    out = faults.meaning(bosch, bosch, typed, "en")
    assert [m["job_id"] for m in out["matches"]] == ["fix_e3200_inlet_sieve"] and out["notice"] is None
    m = out["matches"][0]
    assert m["code"] == "E:32-00" and m["page"] == 45 and m["quote"] and m["title"]["en"].startswith("Clean")
    conforms(out)


def test_a_job_for_several_codes_is_found_by_each(bosch: Any) -> None:
    for code in ("E:01-00", "E9010"):
        assert [m["job_id"] for m in faults.meaning(bosch, bosch, code, "en")["matches"]] == \
            ["fix_other_codes_power_reset"]


def test_a_partial_code_offers_the_cards_codes_to_pick(bosch: Any) -> None:
    out = faults.meaning(bosch, bosch, "E32", "nl")
    assert out["matches"] == [] and out["candidates"] == ["E:32-00"]
    assert out["notice"] == faults.NOTICES["pick"]["nl"]
    assert faults.meaning(bosch, bosch, "9240", "en")["candidates"] == ["E:92-40"]
    assert faults.meaning(bosch, bosch, "E", "en")["candidates"] == []  # one character never lists every code


def test_a_code_not_on_the_card_is_not_listed_with_the_page(bosch: Any) -> None:
    out = faults.meaning(bosch, bosch, "E:99-99", "en")
    assert out["matches"] == out["candidates"] == out["refused"] == [] and out["table_page"] == 45
    assert out["notice"] == "This code isn't in your manual's list. Your manual's troubleshooting starts on page 45."
    conforms(out)
    nl = faults.meaning(bosch, bosch, "E:99-99", "nl")["notice"]
    assert nl is not None and nl.endswith("pagina 45.")
    no_fix = bosch.model_copy(update={"jobs": [j for j in bosch.jobs if j.kind != "fix"]})
    out = faults.meaning(no_fix, no_fix, "E:99-99", "en")
    assert out["table_page"] is None and out["notice"] == faults.NOTICES["not_listed"]["en"]


def test_a_refused_fix_is_a_technicians_job_and_its_title_is_not_shown(bosch: Any) -> None:
    checked = bosch.model_copy(update={"jobs": [j for j in bosch.jobs if j.id != "fix_e3200_inlet_sieve"]})
    out = faults.meaning(checked, bosch, "E3200", "en")
    assert out["matches"] == [] and out["refused"] == ["E:32-00"]
    assert out["notice"] == faults.NOTICES["not_guided"]["en"] and "inlet" not in str(out).lower()
    assert faults.meaning(checked, None, "E3200", "en")["refused"] == []  # no raw card: read as not listed
    only = bosch.model_copy(update={"jobs": [j for j in bosch.jobs if j.kind != "fix"]})
    assert faults.meaning(only, bosch, "E3200", "en")["table_page"] == 45  # every fix refused: the page stays


def test_every_notice_passes_g0(bosch: Any) -> None:
    for kind, table in faults.NOTICES.items():
        for lang, text in table.items():
            r = rules.check_sentence(text.format(page=45), lang, bosch, Ok(state="OK"), country="nl")
            assert isinstance(r, rules.Pass), (kind, lang, r)


def test_the_route_answers_for_your_own_appliance_only(world: Any) -> None:
    tc, ids = world
    r = tc.get(f"/api/v1/appliances/{ids['wa']}/meaning", params={"code": "e3200"}, headers=auth(ids["a"]))
    assert r.status_code == 200 and r.json()["matches"][0]["job_id"] == "fix_e3200_inlet_sieve"
    assert r.json()["code"] == "e3200" and r.json()["landlord"] is False
    conforms(r.json())
    other = tc.get(f"/api/v1/appliances/{ids['wa']}/meaning", params={"code": "e3200"}, headers=auth(ids["b"]))
    assert other.status_code == 404
    assert tc.get(f"/api/v1/appliances/{ids['wa']}/meaning", params={"code": "e3200"}).status_code == 401


@pytest.mark.parametrize("code", ["", "   ", "E" * 21])
def test_an_empty_or_long_code_is_invalid(world: Any, code: str) -> None:
    tc, ids = world
    r = tc.get(f"/api/v1/appliances/{ids['wa']}/meaning", params={"code": code}, headers=auth(ids["a"]))
    assert r.status_code == 422 and r.json()["error"] == "invalid"


def test_no_checked_card_is_a_404(world: Any) -> None:
    tc, ids = world
    r = tc.get(f"/api/v1/appliances/{ids['wb']}/meaning", params={"code": "E10"}, headers=auth(ids["b"]))
    assert r.status_code == 404


def test_more_than_five_candidates_asks_for_more_of_the_code(bosch: Any) -> None:
    fix = next(j for j in bosch.jobs if j.id == "fix_other_codes_power_reset")
    many = fix.model_copy(update={"fault_codes": [FaultCode(f"E:4{i}-00") for i in range(6)]})
    card = bosch.model_copy(update={"jobs": [*bosch.jobs, many.model_copy(update={"id": "fix_many"})]})
    out = faults.meaning(card, card, "E4", "en")
    assert out["candidates"] == [] and out["notice"] == faults.NOTICES["more"]["en"]
    five = many.model_copy(update={"id": "fix_five", "fault_codes": many.fault_codes[:5]})
    card = bosch.model_copy(update={"jobs": [*bosch.jobs, five]})
    assert len(faults.meaning(card, card, "E4", "en")["candidates"]) == 5


# ---- the manual's fault table on the card (card.troubleshooting) ----------------------------------------------------

def row(rid: str, codes: list[str], kind: str = "user", job: str | None = None, **text: Any) -> dict[str, Any]:
    lt = {k: {"en": v} for k, v in text.items()}
    return {"id": rid, "codes": codes, "indicator": lt.get("indicator"), "symbol": None, "symptom": None,
            "meaning": lt.get("meaning", {"en": "The water supply is blocked.", "nl": "De watertoevoer is verstopt."}),
            "cause": None, "remedy": lt.get("remedy", {"en": "Clean the filter in the inlet hose."}),
            "remedy_kind": kind, "escalate_to": None, "job_ref": job, "prevented_by": [], "repeat": None,
            "page": 47, "quote_ref": "q_machine_cleaning_interval"}


ROWS = [row("e3200", ["E:32-00"], job="fix_e3200_inlet_sieve"),
        row("e1500", ["E:15-00"], "technician", remedy="Call the customer service."),
        row("e2500", ["E:25-00"], "installer", remedy="Call your installer."),
        row("e4400", ["E:44-00"], meaning="The door was opened during the programme."),
        row("tap_light", [], indicator="The tap symbol lights up."),
        row("e7700", ["E:77-00"], remedy="Take the cover off the live wiring.")]


@pytest.fixture(scope="module")
def tabled(bosch: Any) -> tuple[Any, Any]:
    """The Bosch card with a fault table, as written and as the loader checks it (rules for nl)."""
    raw = Card.model_validate({**bosch.model_dump(mode="json"), "troubleshooting": ROWS})
    return cards.check_card(raw, "nl", None).card, raw


def test_a_held_row_leaves_the_card_and_the_card_stays_usable(bosch: Any, tabled: Any) -> None:
    raw = tabled[1]
    loaded = cards.check_card(raw, "nl", None)
    assert set(loaded.rows_held) == {"e7700"} and "nl.mains.wiring" in loaded.rows_held["e7700"]
    assert [r.id for r in loaded.card.troubleshooting or []] == ["e3200", "e1500", "e2500", "e4400", "tap_light"]
    assert loaded.card.checks is not None and loaded.card.checks.rules.status == "pass"


def test_a_row_whose_job_left_keeps_its_cited_remedy_and_loses_its_job(bosch: Any) -> None:
    raw = Card.model_validate({**bosch.model_dump(mode="json"), "troubleshooting": ROWS[:1]})
    rows, held = cards.check_rows(raw, {"clean_filters"}, "nl", {"nl", "en"})
    assert held == {} and rows[0].job_ref is None and rows[0].remedy


def test_a_users_row_matches_with_its_job_and_its_own_page(tabled: Any) -> None:
    card, raw = tabled
    out = faults.meaning(card, raw, "e3200", "en")
    assert len(out["matches"]) == 1 and out["notice"] is None  # the row and the job's own code: one match
    m = out["matches"][0]
    assert m["job_id"] == "fix_e3200_inlet_sieve" and m["page"] == 47 and m["title"]["en"].startswith("Clean")
    assert out["table_page"] == 47


def test_a_row_with_no_job_is_titled_by_its_meaning(tabled: Any) -> None:
    out = faults.meaning(*tabled, "E44-00", "en")
    assert out["matches"][0]["job_id"] is None
    assert out["matches"][0]["title"] == {"en": "The door was opened during the programme."}


@pytest.mark.parametrize(("code", "kind"), [("E1500", "technician"), ("E2500", "installer")])
def test_a_call_is_the_rows_meaning_with_who_acts_and_no_job(tabled: Any, code: str, kind: str) -> None:
    out = faults.meaning(*tabled, code, "nl")
    m = out["matches"][0]
    assert m["remedy_kind"] == kind and m["job_id"] is None and m["escalate_to"] is None
    assert m["meaning"]["en"] == "The water supply is blocked." and m["remedy"]["en"].startswith("Call")
    assert out["refused"] == [m["code"]] and out["notice"] is None
    conforms(out)


def test_a_held_row_is_a_technicians_job_and_its_words_stay_out(tabled: Any) -> None:
    out = faults.meaning(*tabled, "E7700", "nl")
    assert out["matches"] == [] and out["refused"] == ["E:77-00"]
    assert out["notice"] == faults.NOTICES["not_guided"]["nl"]
    assert "wiring" not in str(out)
    conforms(out)


def test_a_row_match_carries_the_whole_row_and_a_job_match_none_of_it(tabled: Any, bosch: Any) -> None:
    row_out = faults.meaning(*tabled, "E3200", "en")
    assert set(ROW_FIELDS) <= set(row_out["matches"][0]) and row_out["matches"][0]["row_id"] == "e3200"
    job_out = faults.meaning(bosch, bosch, "E3200", "en")
    assert not set(ROW_FIELDS) & set(job_out["matches"][0])
    for out in (row_out, job_out):
        conforms(out)


def test_landlord_is_said_only_to_a_renter_for_the_landlords_job(tabled: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    assert faults.meaning(*tabled, "E3200", "en", renter=True)["landlord"] is False  # not the landlord's in nl
    monkeypatch.setattr(rules, "landlord_jobs", lambda card, country: {"fix_e3200_inlet_sieve": None})
    assert faults.meaning(*tabled, "E3200", "en", renter=True)["landlord"] is True
    assert faults.meaning(*tabled, "E3200", "en", renter=False)["landlord"] is False
    assert faults.meaning(*tabled, "E1500", "en", renter=True)["landlord"] is False  # a call has no job


def test_row_codes_are_candidates_too(tabled: Any) -> None:
    assert faults.meaning(*tabled, "1500", "en")["candidates"] == ["E:15-00"]


def test_a_code_read_from_a_drawing_says_unconfirmed_until_a_person_confirms_it(tabled: Any, tmp_path: Path) -> None:
    card_file = tmp_path / "card.yaml"
    card_file.write_text("the card")
    entry = {"row": "e4400", "code": "E:44-00", "page": 47, "confirmed_by": None, "confirmed_at": None}
    rec = card_file.with_suffix(".check.json")
    rec.write_text(json.dumps({"drawn_codes": [entry]}))
    drawn = faults.drawn_unconfirmed(card_file)
    assert drawn == {("e4400", "E:44-00")}
    out = faults.meaning(*tabled, "E44-00", "en", drawn=drawn)
    assert out["matches"][0]["unconfirmed"] is True
    conforms(out)
    assert faults.meaning(*tabled, "E32-00", "en", drawn=drawn)["matches"][0]["unconfirmed"] is False
    rec.write_text(json.dumps({"drawn_codes": [{**entry, "confirmed_by": "A reviewer", "confirmed_at": "2026-10-27"}]}))
    assert faults.drawn_unconfirmed(card_file) == frozenset()
    assert faults.drawn_unconfirmed(tmp_path / "no-record.yaml") == frozenset()


def test_a_job_code_match_is_never_unconfirmed(bosch: Any) -> None:
    out = faults.meaning(bosch, bosch, "e3200", "en", drawn=frozenset({("e3200", "E:32-00")}))
    assert [m["unconfirmed"] for m in out["matches"]] == [False]
    conforms(out)

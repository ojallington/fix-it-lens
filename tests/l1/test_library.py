"""The card library from the app's side: which card an appliance is linked to, the reports a person files on it,
"this isn't my appliance", how the card was checked (CardRecord), and the reviewers' CLI. The sharing rules (K5) are
tested where an appliance gets its card: a fetched manual whose own model list covers the model is shared; an upload,
an uncovered model and a different version stay with the home that built them; a maker whose terms forbid it gets no
card; a plate never leaves the home."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator

import calls
from app import library
from app.schedule import store as l5
from app.schedule.due import due
from app.server import checked_card_loader, create_app
from app.store import Store
from tests.l1.test_api import SECRET, FakeIngest, auth, ingested
from tests.l5.conftest import T0, add_household

ROOT = Path(__file__).resolve().parents[2]
MODEL = "SMV9ZZ99X"  # a model no repo card has
SPEC = yaml.safe_load((ROOT / "contracts" / "api.openapi.yaml").read_text())


def conforms(body: Any, schema: str) -> None:
    Draft202012Validator({**SPEC["components"]["schemas"][schema], "components": SPEC["components"]}).validate(body)


def card_dict(model: str = MODEL, *, source: str = "maker_site", models: list[str] | None = None,
              pdf: bytes = b"%PDF-1.7 the maker's manual") -> dict[str, Any]:
    raw = yaml.safe_load((ROOT / "cards/bosch/SMV4HAX40N.yaml").read_text())
    sha = hashlib.sha256(pdf).hexdigest()
    manual = {**raw["manual"], "pdf_sha256": sha, "source": source, "models": [model] if models is None else models,
              "source_url": raw["manual"]["source_url"] if source == "maker_site" else f"urn:fixit:upload:{sha}"}
    if manual["models"]:
        manual["models_page"] = 2
    else:
        manual.pop("models_page", None)
    return {**raw, "model": model, "aliases": [], "manual": manual}


def home_card(private: Path, hid: str, card: dict[str, Any], pdf: bytes = b"%PDF-1.7 the maker's manual",
              drawn: list[dict[str, Any]] | None = None, ours: list[dict[str, Any]] | None = None) -> Path:
    """A card as ingest leaves it for one home: the card, its check record and its manual under that home."""
    from ingest import check_pr

    path = private / "cards" / "homes" / hid / "bosch" / f"{card['model']}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(card, allow_unicode=True, sort_keys=False))
    check_pr.record_path(path).write_text(json.dumps({"card_sha256": check_pr.card_sha256(path),
                                                      "drawn_codes": drawn or [], "fixit_steps": ours or []}))
    manual = private / "manuals" / "homes" / hid / "bosch" / f"{card['model']}.pdf"
    manual.parent.mkdir(parents=True, exist_ok=True)
    manual.write_bytes(pdf)
    return path


class Building(FakeIngest):
    """Ingest that writes the card it builds when it is called, as the real one does."""
    build: Any = None

    async def __call__(self, **kw: Any) -> Any:
        if self.build is not None:
            self.result = ingested("checked", card_path=self.build(), model=MODEL)
        return await super().__call__(**kw)


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    private = tmp_path / "private"
    private.mkdir()
    monkeypatch.setenv("FIXIT_PRIVATE", str(private))
    conn = l5.open_store(":memory:")
    fake = Building(None)
    client = calls.make_client("replay", fixtures_root=tmp_path / "fx", private=None)
    app = create_app(client=client, record_root=tmp_path / "s", store=Store(conn, SECRET), ingest=fake)

    class W:
        pass

    w = W()
    w.tc, w.conn, w.fake, w.private = TestClient(app), conn, fake, private  # type: ignore[attr-defined]
    return w


def add(w: Any, hid: str, path: Path | None, *, alias: str = "dw",
        body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Add an appliance whose ingest ends on `path`, and wait for it."""
    w.fake.result = ingested("checked" if path else "failed", card_path=path, model=MODEL)
    with w.tc:
        a = w.tc.post("/api/v1/appliances", headers=auth(hid),
                      json=body or {"alias": alias, "brand": "bosch", "model": MODEL})
        assert a.status_code == 201, a.text
        out: dict[str, Any] = w.tc.get(f"/api/v1/appliances/{a.json()['id']}", headers=auth(hid)).json()
    return out


def linked(w: Any, aid: str) -> str:
    return str(w.conn.execute("SELECT card_path FROM cards_ref WHERE appliance_id = ?", (aid,)).fetchone()[0])


def person(conn: sqlite3.Connection, hid: str, role: str = "member", view_only: bool = False) -> str:
    """A person in the home with their own app token."""
    pid, token = "p_" + secrets.token_hex(4), "fxt_" + secrets.token_urlsafe(16)
    conn.execute("INSERT INTO people (id, household_id, name, role, view_only, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (pid, hid, f"{role}{int(view_only)}", role, int(view_only), T0.isoformat()))
    conn.execute("INSERT INTO household_tokens (id, household_id, by_kind, scope, person_id, label, token_sha256,"
                 " created_at) VALUES (?, ?, 'app', 'write', ?, 'phone', ?, ?)",
                 ("t_" + pid, hid, pid, hashlib.sha256(token.encode()).hexdigest(), T0.isoformat()))
    return token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---- K1/K5: what an appliance is linked to ---------------------------------------------------------------------------

def test_a_fetched_manual_that_lists_the_model_is_shared_and_linked(world: Any) -> None:
    hid = add_household(world.conn)
    own = home_card(world.private, hid, card_dict())
    a = add(world, hid, own)
    shared = world.private / "cards" / "bosch" / f"{MODEL}.yaml"
    assert a["card_status"] == "checked" and shared.exists() and linked(world, a["id"]) == str(shared)
    row = world.conn.execute("SELECT * FROM card_library WHERE card_path = ?", (str(shared),)).fetchone()
    assert row["shared"] == 1 and row["covers"] == "exact" and row["origin"] == "maker_site"
    other = add_household(world.conn)  # the next home with the model gets the same card, built once
    b = add(world, other, shared, alias="dw")
    assert linked(world, b["id"]) == str(shared)


@pytest.mark.parametrize("card,reason", [
    (card_dict(source="household_upload"), "uploaded"),
    (card_dict(models=[]), "no_model_list"),
    (card_dict(models=["SMV1AAA11X"]), "model_not_in_list"),
])
def test_a_card_that_may_not_be_shared_stays_with_its_home(world: Any, card: dict[str, Any], reason: str) -> None:
    hid = add_household(world.conn)
    own = home_card(world.private, hid, card)
    a = add(world, hid, own)
    assert a["card_status"] == "checked" and linked(world, a["id"]) == str(own)
    assert not (world.private / "cards" / "bosch" / f"{MODEL}.yaml").exists()
    row = world.conn.execute("SELECT shared, reason FROM card_library WHERE card_path = ?", (str(own),)).fetchone()
    assert (row["shared"], row["reason"]) == (0, reason)


def test_a_different_manual_for_a_shared_model_is_a_new_private_version(world: Any) -> None:
    first, second = add_household(world.conn), add_household(world.conn)
    add(world, first, home_card(world.private, first, card_dict()))
    mine = home_card(world.private, second, card_dict(pdf=b"%PDF-1.7 a newer print"), pdf=b"%PDF-1.7 a newer print")
    b = add(world, second, mine)
    assert linked(world, b["id"]) == str(mine)
    rec = world.tc.get(f"/api/v1/appliances/{b['id']}/card/record", headers=auth(second)).json()
    conforms(rec, "CardRecord")
    assert rec["shared"] is False and rec["private_reason"] == "version_conflict" and rec["version"] == 2
    assert "version_conflict" in "\n".join(library.review_list(world.conn))


def test_a_maker_whose_terms_forbid_it_gets_no_card_anywhere(world: Any) -> None:
    hid = add_household(world.conn)
    a = add(world, hid, None, body={"alias": "wm", "brand": "Samsung", "model": "WW90T534DAW"})
    assert world.fake.calls == [] and a["card_status"] == "failed"
    assert a["ingest"]["notice"]["reason"] == "maker_terms"
    assert not list((world.private / "cards").rglob("*.yaml")) if (world.private / "cards").exists() else True
    assert world.conn.execute("SELECT count(*) FROM card_library").fetchone()[0] == 0


def test_a_plate_never_leaves_the_home(world: Any) -> None:
    from app import adding

    hid = add_household(world.conn)
    old = adding.plates_dir(hid)
    assert old is not None
    old.mkdir(parents=True)
    (old / "pl_k5.jpg").write_bytes(b"\xff\xd8 plate with serial FD9912345678")  # kept before photos were not
    adding.keep_settled("pl_k5", "bosch", MODEL, hid)
    add(world, hid, home_card(world.private, hid, card_dict()), body={"alias": "dw", "plate_photo_id": "pl_k5"})
    shared = world.private / "cards" / "bosch"
    assert shared.exists() and all(b"FD9912345678" not in f.read_bytes() for f in shared.rglob("*") if f.is_file())
    assert not any(f.suffix == ".jpg" for f in (world.private / "cards").rglob("*"))
    assert world.fake.calls[-1].get("plate_jpeg") is None  # an older kept photo is never read again


# ---- reports -------------------------------------------------------------------------------------------------------

def test_a_report_is_filed_on_the_linked_version_and_never_edits_the_card(world: Any) -> None:
    hid = add_household(world.conn)
    a = add(world, hid, home_card(world.private, hid, card_dict()))
    card_file = Path(linked(world, a["id"]))
    before = card_file.read_bytes()
    job = yaml.safe_load(before)["jobs"][0]
    with world.tc:
        r = world.tc.post(f"/api/v1/appliances/{a['id']}/reports", headers=auth(hid),
                          json={"kind": "wrong_step", "job_id": job["id"], "step_id": job["steps"][0]["id"],
                                "note": "The filter is on the left, not the right."})
        assert r.status_code == 201, r.text
        conforms(r.json(), "Report")
        got = r.json()
        assert got["status"] == "open" and got["card_sha256"] == hashlib.sha256(before).hexdigest()
        assert got["by"] == {"kind": "app", "person_id": got["by"]["person_id"], "label": "Admin"}
        assert card_file.read_bytes() == before
        mine = world.tc.get("/api/v1/reports", headers=auth(hid)).json()
        assert [x["id"] for x in mine] == [got["id"]]
        assert world.tc.get(f"/api/v1/reports?appliance_id={a['id']}", headers=auth(hid)).json() == mine
        other = add_household(world.conn)
        assert world.tc.get("/api/v1/reports", headers=auth(other)).json() == []


@pytest.mark.parametrize("body,status,error", [
    ({"kind": "wrong_step"}, 422, None),
    ({"kind": "wrong_page"}, 422, None),
    ({"kind": "other"}, 422, None),
    ({"kind": "wrong_appliance"}, 422, None),  # that one is POST .../not-mine
    ({"kind": "unsafe", "note": "x" * 501}, 422, None),
    ({"kind": "unsafe", "job_id": "no_such_job"}, 422, "unknown_job"),
    ({"kind": "wrong_step", "step_id": "no_such_step"}, 422, "unknown_step"),
    ({"kind": "unsafe", "colour": "red"}, 422, None),
])
def test_a_report_the_contract_refuses_is_a_422(world: Any, body: dict[str, Any], status: int,
                                                 error: str | None) -> None:
    hid = add_household(world.conn)
    a = add(world, hid, home_card(world.private, hid, card_dict()))
    with world.tc:
        r = world.tc.post(f"/api/v1/appliances/{a['id']}/reports", headers=auth(hid), json=body)
    assert r.status_code == status and (error is None or r.json()["error"] == error)
    assert world.conn.execute("SELECT count(*) FROM card_reports").fetchone()[0] == 0


def test_who_may_report(world: Any) -> None:
    hid = add_household(world.conn)
    a = add(world, hid, home_card(world.private, hid, card_dict()))
    viewer = person(world.conn, hid, view_only=True)
    _, agent = l5.issue_token(world.conn, hid, by_kind="agent", scope="write", label="Claude desktop")
    _, ha = l5.issue_token(world.conn, hid, by_kind="ha", scope="write", label="Home Assistant")
    url, body = f"/api/v1/appliances/{a['id']}/reports", {"kind": "unsafe", "note": "The cover gets hot."}
    with world.tc:
        assert world.tc.post(url, headers=bearer(viewer), json=body).status_code == 201  # a viewer may report
        for token in (agent, ha, l5.feed_token(SECRET, hid)):
            assert world.tc.post(url, headers=bearer(token), json=body).status_code in (401, 403)
        assert world.tc.post(url, headers=auth(add_household(world.conn)), json=body).status_code == 404
        b = add(world, hid, None, alias="nothing")
        assert world.tc.post(f"/api/v1/appliances/{b['id']}/reports", headers=auth(hid),
                             json=body).json()["error"] == "no_card"


def test_twenty_open_reports_is_the_limit(world: Any) -> None:
    hid = add_household(world.conn)
    a = add(world, hid, home_card(world.private, hid, card_dict()))
    url = f"/api/v1/appliances/{a['id']}/reports"
    with world.tc:
        for i in range(library.OPEN_REPORTS_MAX):
            assert world.tc.post(url, headers=auth(hid), json={"kind": "other", "note": f"n{i}"}).status_code == 201
        r = world.tc.post(url, headers=auth(hid), json={"kind": "other", "note": "one more"})
        assert r.status_code == 429 and r.json()["error"] == "too_many_reports"
        assert world.tc.post(f"/api/v1/appliances/{a['id']}/not-mine", headers=auth(hid)).status_code == 429
        rid = world.tc.get("/api/v1/reports", headers=auth(hid)).json()[0]["id"]
        assert library.close(world.conn, rid, "read; the card is right")
        assert world.tc.post(url, headers=auth(hid), json={"kind": "other", "note": "now"}).status_code == 201


# ---- not mine ------------------------------------------------------------------------------------------------------

def test_not_mine_unlinks_for_this_home_only_and_never_relinks(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.adding import REASONS

    first, second = add_household(world.conn), add_household(world.conn)
    a = add(world, first, home_card(world.private, first, card_dict()))
    shared = Path(linked(world, a["id"]))
    b = add(world, second, shared)
    loader = checked_card_loader("nl")
    monkeypatch.setattr("app.ports.IngestManuals.manual_pages", lambda self, brand, model, household=None: [])
    with world.tc:
        r = world.tc.post(f"/api/v1/appliances/{b['id']}/not-mine", headers=auth(second),
                          json={"note": "Mine is the 45 cm one."})
        assert r.status_code == 200, r.text
        got = r.json()
        assert got["card_status"] == "failed"
        assert got["ingest"]["notice"] == {"kind": "lookup_failed", "reason": "not_mine", "maker_page": None,
                                           "text": REASONS["not_mine"]}
        assert world.tc.get(f"/api/v1/appliances/{b['id']}", headers=auth(second)).json()["ingest"] == got["ingest"]
        assert world.tc.post(f"/api/v1/appliances/{b['id']}/not-mine", headers=auth(second)).status_code == 409
        rep = world.tc.get("/api/v1/reports", headers=auth(second)).json()
        assert [x["kind"] for x in rep] == ["wrong_appliance"] and rep[0]["note"] == "Mine is the 45 cm one."
    assert due(world.conn, second, T0, loader) == []
    assert linked(world, a["id"]) == str(shared)  # the other home keeps it
    again = add(world, second, shared, alias="dw")  # typed again: the same card is found and not linked
    assert again["id"] == b["id"] and again["card_status"] == "failed"
    assert again["ingest"]["notice"]["reason"] == "not_mine"
    upload = world.private / "cards" / "homes" / second / "bosch" / f"{MODEL}.yaml"
    world.fake.build = lambda: home_card(world.private, second, card_dict(source="household_upload",
                                                                         pdf=b"%PDF-1.7 mine"), pdf=b"%PDF-1.7 mine")
    with world.tc:
        r = world.tc.post(f"/api/v1/appliances/{b['id']}/manual", headers={**auth(second),
                          "Content-Type": "application/pdf"}, content=b"%PDF-1.7 mine")
        assert r.status_code in (200, 201, 202), r.text
    assert world.fake.calls[-1]["pdf"] == b"%PDF-1.7 mine"  # the upload is built, never swapped for the shared card
    assert linked(world, b["id"]) == str(upload)


def test_who_may_say_not_mine(world: Any) -> None:
    hid = add_household(world.conn)
    a = add(world, hid, home_card(world.private, hid, card_dict()))
    viewer, member = person(world.conn, hid, view_only=True), person(world.conn, hid)
    _, agent = l5.issue_token(world.conn, hid, by_kind="agent", scope="write", label="Claude desktop")
    url = f"/api/v1/appliances/{a['id']}/not-mine"
    with world.tc:
        assert world.tc.post(url, headers=bearer(viewer)).status_code == 403
        assert world.tc.post(url, headers=bearer(agent)).status_code == 403
        r = world.tc.post(url, headers=bearer(member))
        assert r.status_code == 200 and r.json()["card_status"] == "failed"
    rep = library.reports(world.conn, hid)[0]
    assert rep["by"]["label"] == "member0" and rep["by"]["person_id"]


# ---- how the card was checked --------------------------------------------------------------------------------------

def test_the_card_record_is_counts_and_never_a_note_or_a_name(world: Any) -> None:
    first, second = add_household(world.conn), add_household(world.conn)
    own = home_card(world.private, first, card_dict())
    a = add(world, first, own)
    shared = Path(linked(world, a["id"]))
    shared.with_suffix(".check.json").write_text(json.dumps({"quotes": [{"ok": True}, {"ok": True}, {"ok": False}]}))
    b = add(world, second, shared)
    with world.tc:
        world.tc.post(f"/api/v1/appliances/{b['id']}/reports", headers=auth(second),
                      json={"kind": "unsafe", "note": "secret words from the other home"})
        r = world.tc.get(f"/api/v1/appliances/{a['id']}/card/record", headers=auth(first))
        assert r.status_code == 200
        rec = r.json()
        conforms(rec, "CardRecord")
        assert rec["shared"] is True and rec["private_reason"] is None and rec["homes"] == 2 and rec["version"] == 1
        assert rec["quotes"] == {"checked": 3, "passed": 2}
        assert rec["reports_open"] == {"total": 1, "safety": 1} and rec["reviewed"] == {"by_person": False, "at": None}
        assert "secret" not in r.text and str(world.private) not in r.text
        nothing = add(world, first, None, alias="nothing")
        assert world.tc.get(f"/api/v1/appliances/{nothing['id']}/card/record", headers=auth(first)).status_code == 404


def test_a_persons_usable_read_of_these_very_bytes_counts_as_checked(world: Any) -> None:
    hid = add_household(world.conn)
    a = add(world, hid, home_card(world.private, hid, card_dict()))
    path, sha = world.conn.execute("SELECT card_path, card_sha256 FROM cards_ref WHERE appliance_id = ?",
                                   (a["id"],)).fetchone()
    reads = world.private / "first_time" / "verdicts.jsonl"
    reads.parent.mkdir()

    def reviewed(*lines: dict[str, Any]) -> Any:
        reads.write_text("".join(json.dumps({"card": path, "card_sha256": sha, "verdict": "usable",
                                             "at": "2026-10-27", **v}) + "\n" for v in lines))
        with world.tc:
            return world.tc.get(f"/api/v1/appliances/{a['id']}/card/record", headers=auth(hid)).json()["reviewed"]

    nobody = {"by_person": False, "at": None}
    assert reviewed({"verdict_by": "builder"}) == nobody  # the build's own read is not a person's
    assert reviewed({"verdict_by": "a reviewer", "card_sha256": "0" * 64}) == nobody  # other bytes than these
    assert reviewed({"verdict_by": "a reviewer", "verdict": "wrong"}) == nobody
    assert reviewed({"verdict_by": "a reviewer"}) == {"by_person": True, "at": "2026-10-27"}
    later = {"verdict_by": "a reviewer", "verdict": "wrong"}
    assert reviewed({"verdict_by": "a reviewer"}, later) == nobody  # the last


# ---- the reviewers' CLI ----------------------------------------------------------------------------------------------

def test_a_reviewer_links_a_card_whose_manual_lists_no_models(world: Any) -> None:
    hid = add_household(world.conn)
    own = home_card(world.private, hid, card_dict(models=[]))
    a = add(world, hid, own)
    assert f"card {own}" in "\n".join(library.review_list(world.conn))
    assert library.review_link(world.conn, str(own), " ") == "a review needs the reviewer's name"
    said = library.review_link(world.conn, str(own), "A reviewer")
    shared = world.private / "cards" / "bosch" / f"{MODEL}.yaml"
    assert said.startswith("shared") and shared.exists()
    row = world.conn.execute("SELECT * FROM card_library WHERE card_path = ?", (str(shared),)).fetchone()
    assert row["shared"] == 1 and row["covers"] == "none" and row["linked_by_review_by"] == "A reviewer"
    other = add_household(world.conn)
    b = add(world, other, shared)
    rec = world.tc.get(f"/api/v1/appliances/{b['id']}/card/record", headers=auth(other)).json()
    conforms(rec, "CardRecord")
    assert rec["reviewed"]["by_person"] is True and rec["reviewed"]["at"] and "A reviewer" not in json.dumps(rec)
    assert linked(world, a["id"]) == str(own)  # the home that built it keeps its own file until it adds again


def test_codes_read_from_a_drawing_keep_the_card_home_until_a_person_confirms_them(world: Any) -> None:
    from ingest import check_pr

    hid = add_household(world.conn)
    drawn = [{"row": 0, "code": "E20", "page": 30, "confirmed_by": None, "confirmed_at": None}]
    own = home_card(world.private, hid, card_dict(), drawn=drawn)
    a = add(world, hid, own)
    assert a["card_status"] == "checked" and linked(world, a["id"]) == str(own)  # the home uses its card at once
    assert not (world.private / "cards" / "bosch" / f"{MODEL}.yaml").exists()
    rec = world.tc.get(f"/api/v1/appliances/{a['id']}/card/record", headers=auth(hid)).json()
    conforms(rec, "CardRecord")
    assert (rec["shared"], rec["private_reason"], rec["unconfirmed_codes"]) == (False, "codes_unconfirmed", 1)
    listed = "\n".join(library.review_list(world.conn))
    assert f"card {own}" in listed and "E20" in listed and "tools.confirm_codes" in listed
    assert library.review_link(world.conn, str(own), "A reviewer").startswith("not shared: codes_unconfirmed")
    drawn[0] |= {"confirmed_by": "A reviewer", "confirmed_at": "2026-10-27T16:00:00Z"}  # as confirm_codes
    check_pr.record_path(own).write_text(json.dumps({"card_sha256": check_pr.card_sha256(own), "drawn_codes": drawn}))
    assert library.review_link(world.conn, str(own), "A reviewer").startswith("shared")
    assert library.unconfirmed_codes(world.private / "cards" / "bosch" / f"{MODEL}.yaml") == []


def test_a_restore_step_ingest_added_keeps_the_card_home_until_a_person_accepts_it(world: Any) -> None:
    from ingest import check_pr

    hid = add_household(world.conn)
    ours = [{"job": "clean_filters", "step": "plug_back_in", "accepted_by": None, "accepted_at": None}]
    own = home_card(world.private, hid, card_dict(), ours=ours)
    a = add(world, hid, own)
    assert a["card_status"] == "checked" and linked(world, a["id"]) == str(own)
    rec = world.tc.get(f"/api/v1/appliances/{a['id']}/card/record", headers=auth(hid)).json()
    conforms(rec, "CardRecord")
    assert (rec["private_reason"], rec["unaccepted_steps"], rec["unconfirmed_codes"]) == ("steps_unaccepted", 1, 0)
    listed = "\n".join(library.review_list(world.conn))
    assert "clean_filters/plug_back_in" in listed and "tools.confirm_codes" in listed
    assert library.review_link(world.conn, str(own), "A reviewer").startswith("not shared: steps_unaccepted")
    ours[0] |= {"accepted_by": "A reviewer", "accepted_at": "2026-10-27T17:00:00Z"}
    check_pr.record_path(own).write_text(json.dumps({"card_sha256": check_pr.card_sha256(own), "fixit_steps": ours}))
    assert library.review_link(world.conn, str(own), "A reviewer").startswith("shared")


def test_the_review_list_marks_a_held_note_and_close_needs_words(world: Any) -> None:
    hid = add_household(world.conn)
    a = add(world, hid, home_card(world.private, hid, card_dict()))
    with world.tc:
        rid = world.tc.post(f"/api/v1/appliances/{a['id']}/reports", headers=auth(hid),
                            json={"kind": "unsafe", "note": "Take the cover off the live wiring."}).json()["id"]
    listed = "\n".join(library.review_list(world.conn))
    assert rid in listed and "[note held by G0]" in listed
    assert not library.close(world.conn, rid, "  ")
    assert library.close(world.conn, rid, "read; a new card version drops that step")
    assert not library.close(world.conn, rid, "again")
    assert rid not in "\n".join(library.review_list(world.conn))


def test_the_cli_runs_on_the_store(world: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    db = tmp_path / "store.sqlite"
    l5.open_store(db).close()
    assert library.main(["--db", str(db), "list"]) == 0 and "nothing to review" in capsys.readouterr().out
    assert library.main(["--db", str(db), "close", "r_none", "--resolution", "x"]) == 1
    assert library.main(["--db", str(tmp_path / "missing.sqlite"), "list"]) == 2

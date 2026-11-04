"""The card loader on a fresh install.

A repo card with no manual copy passes its citation check on its committed record (cards/<brand>/<model>.check.json,
written by `make check-pr` with the manual and checked in CI): the record's card digest equals the card's bytes and
every quote is in it as passed. A fresh install has only the repo card and must still get reminders. An edited card
or a failed quote leaves it unchecked: no reminders, and no session.

The rules refuse per job: a job whose step or done-when line G0 holds is refused with its reason, and the card's
other jobs stay usable (one held clause in clean_drain_pump never blocks clean_filters, M1).

A card ingest built in the private folder gives reminders only with its check record, bound to the card's bytes and
to the manual beside it; an edited card, a missing record or another copy of the manual gives none."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

import calls
import rules
from app import cards, config
from app.schedule import store
from app.schedule.due import due
from app.server import checked_card_loader, create_app
from tests.l1.test_server import hello
from tests.l5.conftest import T0, add_appliance, add_household

ROOT = Path(__file__).resolve().parents[2]
REL = "cards/bosch/SMV4HAX40N.yaml"
HELD = "Take the cover off the live wiring."  # held by nl.mains.wiring; the fixture adds it to clean_drain_pump


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh install: the repo's Bosch card and its record, and an empty private folder (no manual). The card has
    one held clause added to clean_drain_pump (HELD), so these tests never depend on the card's own wording."""
    card = tmp_path / REL
    card.parent.mkdir(parents=True)
    raw = yaml.safe_load((ROOT / REL).read_text())
    step = next(s for j in raw["jobs"] if j["id"] == "clean_drain_pump" for s in j["steps"] if s.get("done_when"))
    step["done_when"].append({"id": "held_line", "text": {"en": HELD},
                              "quote_ref": step["done_when"][0]["quote_ref"]})
    card.write_text(yaml.safe_dump(raw, allow_unicode=True, sort_keys=False))
    rec = json.loads((ROOT / REL).with_suffix(".check.json").read_text())
    rec["record_version"] = 2  # the record as ingest writes it (version 2)
    rec["card_sha256"] = hashlib.sha256(card.read_bytes()).hexdigest()
    card.with_suffix(".check.json").write_text(json.dumps(rec))
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setattr(config, "CARDS_DIR", tmp_path / "cards")
    (tmp_path / "private").mkdir()
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "private"))
    return card


def reminders(card_rel: str = REL) -> list[object]:
    conn: sqlite3.Connection = store.open_store(":memory:")
    hid = add_household(conn)
    add_appliance(conn, hid, "dishwasher", card_path=card_rel)
    return list(due(conn, hid, T0, checked_card_loader("nl")))


def test_a_matching_record_passes_and_gives_reminders(repo: Path) -> None:
    loaded = cards.load(repo, "nl", None)
    assert loaded.card.checks is not None and loaded.card.checks.citation.status == "pass" and loaded.usable
    assert loaded.citation_label == "checked (record, CI-verified)"
    assert reminders()


def test_a_card_edited_after_its_record_is_unchecked(repo: Path) -> None:
    repo.write_text(repo.read_text() + "\n# edited\n")
    loaded = cards.load(repo, "nl", None)
    assert loaded.card.checks is not None and loaded.card.checks.citation.status == "unchecked"
    assert not loaded.usable and any("card digest" in f for f in loaded.failures)
    assert reminders() == []


def test_a_failed_quote_in_the_record_is_unchecked(repo: Path) -> None:
    rec_file = repo.with_suffix(".check.json")
    rec = json.loads(rec_file.read_text())
    rec["quotes"][0]["ok"] = False
    rec_file.write_text(json.dumps(rec))
    loaded = cards.load(repo, "nl", None)
    assert loaded.card.checks is not None and loaded.card.checks.citation.status == "unchecked" and not loaded.usable
    assert reminders() == []


def test_no_record_or_a_card_outside_the_repo_is_unchecked(repo: Path, tmp_path: Path) -> None:
    repo.with_suffix(".check.json").unlink()
    assert cards.load(repo, "nl", None).citation_label == "unchecked"
    elsewhere = tmp_path / "private" / "cards" / "SMV4HAX40N.yaml"
    elsewhere.parent.mkdir(parents=True)
    shutil.copy(ROOT / REL, elsewhere)
    shutil.copy((ROOT / REL).with_suffix(".check.json"), elsewhere.with_suffix(".check.json"))
    assert cards.load(elsewhere, "nl", None).citation_label == "unchecked"




def test_a_held_clause_refuses_its_job_and_not_the_card(repo: Path) -> None:
    assert HELD in repo.read_text()
    loaded = cards.load(repo, "nl", None)
    assert loaded.usable and loaded.card.checks is not None and loaded.card.checks.rules.status == "pass"
    assert set(loaded.refused) == {"clean_drain_pump"} and "nl.mains.wiring" in loaded.refused["clean_drain_pump"]
    jobs = [j.id for j in loaded.card.jobs]
    assert "clean_filters" in jobs and "clean_drain_pump" not in jobs
    assert any(f.startswith("job clean_drain_pump refused:") for f in loaded.card.checks.rules.failures)
    assert {d.job_id for d in reminders()} and "clean_drain_pump" not in {d.job_id for d in reminders()}


def test_every_job_held_refuses_the_card(repo: Path) -> None:
    card = cards.read(repo)
    only = card.model_copy(update={"jobs": [j for j in card.jobs if j.id == "clean_drain_pump"]})
    loaded = cards.check_card(only, "nl", None)
    assert not loaded.usable and loaded.card.checks is not None and loaded.card.checks.rules.status == "fail"


def test_a_red_zone_failure_refuses_the_whole_card(repo: Path) -> None:
    card = cards.read(repo)
    zones = [z.model_copy(update={"ring_cm": 1}) for z in card.red_zones]
    loaded = cards.check_card(card.model_copy(update={"red_zones": zones}), "nl", None)
    assert not loaded.usable and loaded.card.checks is not None and loaded.card.checks.rules.status == "fail"


def test_the_server_refuses_only_the_held_job(repo: Path, tmp_path: Path) -> None:
    client = calls.make_client("replay", fixtures_root=tmp_path / "fx", private=None)
    tc = TestClient(create_app(client=client, record_root=tmp_path / "sessions"))
    with tc.websocket_connect("/ws?card=bosch/SMV4HAX40N&job=clean_drain_pump") as sock:
        sock.send_text(json.dumps(hello()))
        assert sock.receive_json()["kind"] == "job_refused"
    with tc.websocket_connect("/ws?card=bosch/SMV4HAX40N&job=clean_filters") as sock:
        sock.send_text(json.dumps(hello()))
        assert sock.receive_json()["type"] == "hello_ok"


@pytest.mark.parametrize("country", ["uk", "us", "de"])
def test_a_card_is_checked_only_in_the_countrys_languages(repo: Path, country: str) -> None:
    from contracts.models.safety import Ok

    loaded = cards.load(repo, country, None)
    assert loaded.card.checks is not None
    assert not any("(nl)" in f for f in loaded.card.checks.rules.failures)  # nl has no table in an en-only file
    assert "clean_filters" in [j.id for j in loaded.card.jobs]
    # ...and G0 still fails closed at run time on a language the file does not cover
    nl_line = cards.read(repo).jobs[0].steps[0].text.root["nl"]
    assert isinstance(rules.check_sentence(nl_line, "nl", loaded.card, Ok(state="OK"), country=country), rules.Hold)


def test_an_english_line_in_an_nl_session_is_still_held_at_run_time(repo: Path) -> None:
    from contracts.models.safety import Ok

    loaded = cards.load(repo, "nl", None)
    assert isinstance(rules.check_sentence(HELD, "en", loaded.card, Ok(state="OK"), country="nl"), rules.Hold)


# ---- a card ingest built for a household's appliance (FIXIT_PRIVATE/cards) ------------------------------------------

@pytest.fixture
def private(repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The Bosch card as ingest leaves it in the private folder: the card, the manual it was built from, and the check
    record bound to both. The manual is a stand-in file; its page text is the card's own quotes on their pages."""
    from app.ports import IngestManuals, Page
    from ingest import check_pr

    card = tmp_path / "private" / "cards" / "bosch" / "SMV4HAX40N.yaml"
    card.parent.mkdir(parents=True)
    shutil.copy(repo, card)
    manual = tmp_path / "private" / "manuals" / "bosch" / "SMV4HAX40N.pdf"
    manual.parent.mkdir(parents=True)
    manual.write_bytes(b"%PDF-1.7 stand-in")
    rec = json.loads(repo.with_suffix(".check.json").read_text())
    rec["pdf_sha256"] = hashlib.sha256(manual.read_bytes()).hexdigest()
    card.with_suffix(".check.json").write_text(json.dumps(rec))
    repo.with_suffix(".check.json").unlink()  # the repo card no longer vouches for anything
    raw = check_pr.read_card(card)
    text: dict[int, list[str]] = {}
    for _, q in check_pr.card_quotes(raw):
        text.setdefault(q["page"], []).append(q["text"])
    pages = [Page(n, "\n".join(text.get(n, [])) or "-") for n in range(1, raw["manual"]["page_count"] + 1)]
    monkeypatch.setattr(IngestManuals, "manual_pages", lambda self, brand, model, household=None: pages)
    return card


def test_a_recorded_private_card_gives_reminders(private: Path) -> None:
    assert cards.private_record_failures(private) == []
    due_now = reminders(str(private))
    assert due_now and "clean_drain_pump" not in {d.job_id for d in due_now}  # a held job stays refused
    assert reminders(str(private.relative_to(config.ROOT)))  # the path as the store keeps it under ROOT


def test_a_private_card_edited_after_its_record_gives_none(private: Path) -> None:
    private.write_text(private.read_text() + "\n# edited\n")
    assert any("card digest" in f for f in cards.private_record_failures(private))
    assert reminders(str(private)) == []


def test_a_private_card_without_a_record_gives_none(private: Path) -> None:
    private.with_suffix(".check.json").unlink()
    assert cards.private_record_failures(private) and reminders(str(private)) == []


def test_a_private_card_checked_against_another_manual_gives_none(private: Path, tmp_path: Path) -> None:
    manual = tmp_path / "private" / "manuals" / "bosch" / "SMV4HAX40N.pdf"
    manual.write_bytes(b"%PDF-1.7 another copy")
    assert any("pdf digest" in f for f in cards.private_record_failures(private))
    assert reminders(str(private)) == []
    manual.unlink()
    assert any("no manual" in f for f in cards.private_record_failures(private))
    assert reminders(str(private)) == []


def test_a_recorded_card_elsewhere_gives_none(private: Path, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere" / "SMV4HAX40N.yaml"
    elsewhere.parent.mkdir()
    shutil.copy(private, elsewhere)
    shutil.copy(private.with_suffix(".check.json"), elsewhere.with_suffix(".check.json"))
    assert reminders(str(elsewhere)) == []


def test_a_homes_own_card_is_bound_to_that_homes_manual(private: Path, tmp_path: Path) -> None:
    """A card ingest built for one home cites that home's copy of the manual, not the shared one."""
    home = tmp_path / "private" / "cards" / "homes" / "h_one" / "bosch" / "SMV4HAX40N.yaml"
    home.parent.mkdir(parents=True)
    shutil.move(private, home)
    shutil.move(private.with_suffix(".check.json"), home.with_suffix(".check.json"))
    shared = tmp_path / "private" / "manuals" / "bosch" / "SMV4HAX40N.pdf"
    own = tmp_path / "private" / "manuals" / "homes" / "h_one" / "bosch" / "SMV4HAX40N.pdf"
    assert any("no manual" in f for f in cards.private_record_failures(home))  # the shared copy is not its manual
    own.parent.mkdir(parents=True)
    shutil.move(shared, own)
    assert cards.private_record_failures(home) == [] and reminders(str(home))
    shared.write_bytes(b"%PDF-1.7 another home's manual")  # a shared copy beside it changes nothing
    assert cards.private_record_failures(home) == []


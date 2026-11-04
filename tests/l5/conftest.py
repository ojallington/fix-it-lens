"""Shared fixtures for these tests: an in-memory store, households with an appliance, and the Acme example card."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

from app.schedule import store
from contracts.models.card import Card

ROOT = Path(__file__).resolve().parents[2]
ACME = ROOT / "contracts/examples/card/acme-wm-0000.json"
CARD_PATH = "cards/acme/WM-0000.yaml"
T0 = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def acme_card(citation: Literal["pass", "fail", "unchecked"] = "pass") -> Card:
    data = json.loads(ACME.read_text())
    data["checks"] = {"citation": {"status": citation, "failures": [] if citation == "pass" else ["q_interval"]},
                      "rules": {"status": "pass", "failures": []}}
    return Card.model_validate(data)


@pytest.fixture
def conn() -> sqlite3.Connection:
    return store.open_store(":memory:")


@pytest.fixture
def secret() -> bytes:
    return b"\x01" * 32


def add_household(conn: sqlite3.Connection, *, country: str = "nl", lang: str = "en", cap: float = 0.45) -> str:
    inv = store.create_invites(conn, 1, cap_usd_day=cap, country=country, lang=lang, at=T0)[0]
    hid = store.claim_invite(conn, inv.code, consent=True, at=T0)
    assert hid == inv.household_id
    return hid


def add_appliance(conn: sqlite3.Connection, household_id: str, alias: str = "washer",
                  card_path: str = CARD_PATH) -> str:
    aid = f"a_{household_id}_{alias}"
    conn.execute("INSERT INTO appliances (id, household_id, alias, kind, brand, model, added_by, created_at)"
                 " VALUES (?, ?, ?, 'washer', 'acme', 'WM-0000', 'typed', ?)",
                 (aid, household_id, alias, T0.isoformat()))
    conn.execute("INSERT INTO cards_ref (appliance_id, card_path, card_sha256, status) VALUES (?, ?, ?, 'checked')",
                 (aid, card_path, "0" * 64))
    return aid


@pytest.fixture
def loader() -> Callable[[str], Card | None]:
    cards = {CARD_PATH: acme_card("pass"), "cards/acme/failed.yaml": acme_card("fail"),
             "cards/acme/unchecked.yaml": acme_card("unchecked")}
    return cards.get


@pytest.fixture(autouse=True)
def _no_real_private_folder(monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory) -> None:
    """No test reads or sweeps the real private folder: unless a test sets its own, FIXIT_PRIVATE is a fresh one.
    The replay host sets FIXIT_PRIVATE and FIXIT_CALLS for its whole process; recording both here puts them back
    after each test, so a test that builds it leaks nothing into the next."""
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path_factory.mktemp("private")))
    monkeypatch.delenv("FIXIT_DB", raising=False)
    calls_mode = os.environ.get("FIXIT_CALLS")
    if calls_mode is None:
        monkeypatch.delenv("FIXIT_CALLS", raising=False)
    else:
        monkeypatch.setenv("FIXIT_CALLS", calls_mode)

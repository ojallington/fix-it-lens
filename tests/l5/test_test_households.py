"""A test household (households.is_test): never an own home, sent no reminders, billed to the lane doing the work."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from app.feeds import caps, ics, push
from app.schedule import invites, store
from contracts.models.card import Card
from tests.l5.conftest import T0, add_appliance

JOB = "clean_debris_filter"


def a_test_household(conn: sqlite3.Connection) -> str:
    inv = store.create_invites(conn, 1, is_test=True, lang="en", at=T0)[0]
    hid = store.claim_invite(conn, inv.code, consent=True, at=T0)
    assert hid is not None
    return hid


def test_a_test_household_is_never_own(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        store.create_invites(conn, 1, is_own=True, is_test=True)
    with pytest.raises(sqlite3.IntegrityError):  # and the store refuses it too
        conn.execute("INSERT INTO households (id, invite_code, is_own, is_test, created_at)"
                     " VALUES ('x', 'C', 1, 1, '2026-10-27')")


def test_it_is_sent_no_reminder_and_logs_none(conn: sqlite3.Connection, loader: Callable[[str], Card | None]) -> None:
    hid = a_test_household(conn)
    aid = add_appliance(conn, hid)
    store.set_last_done(conn, hid, aid, JOB, source="never", on=None, at=T0)
    push.subscribe(conn, hid, {"endpoint": "https://push.test/1", "keys": {"p256dh": "k", "auth": "a"}}, at=T0)
    sent: list[object] = []
    assert push.dispatch(conn, T0, loader, lambda sub, r: sent.append(r) or 201) == [] and sent == []
    body = ics.feed(conn, hid, T0, loader, "https://fixit.test")
    assert b"BEGIN:VEVENT" in body                        # its own calendar still shows the dates
    assert conn.execute("SELECT count(*) FROM pilot_events").fetchone()[0] == 0


def test_it_bills_the_lane_not_the_pilot(conn: sqlite3.Connection) -> None:
    hid = a_test_household(conn)
    assert caps.household_lane(conn, hid, "l1") == "l1"


def test_the_cli_makes_and_lists_one(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                     capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("FIXIT_DB", str(tmp_path / "store.sqlite"))
    invites.main(["new", "1", "--test"])
    hid = capsys.readouterr().out.split()[1]
    invites.main(["list"])
    assert f"{hid}  cap $0.45  test unused" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        invites.main(["new", "1", "--test", "--own"])

"""Push: a recorded subscription receives the encrypted payload with the card's page, once per due date."""

from __future__ import annotations

import base64
import json
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import http_ece
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.feeds import push
from app.schedule import store
from contracts.models.card import Card
from tests.l5.conftest import T0, add_appliance, add_household

JOB = "clean_debris_filter"


def b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


class Browser:
    """Stands in for the phone's push subscription: holds the keys, so it can decrypt what the push service gets."""

    def __init__(self) -> None:
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.auth = b"0123456789abcdef"
        pub = self.key.public_key().public_bytes(serialization.Encoding.X962,
                                                 serialization.PublicFormat.UncompressedPoint)
        self.sub = {"endpoint": "https://push.example.invalid/send/abc", "keys": {"p256dh": b64(pub),
                                                                                "auth": b64(self.auth)}}

    def decrypt(self, body: bytes) -> dict[str, Any]:
        out: dict[str, Any] = json.loads(http_ece.decrypt(body, private_key=self.key, auth_secret=self.auth,
                                                          version="aes128gcm"))
        return out


class FakeSession:
    def __init__(self, status: int = 201) -> None:
        self.status = status
        self.posts: list[tuple[str, bytes, dict[str, str]]] = []

    def post(self, url: str, data: bytes, headers: dict[str, str], timeout: float | None = None) -> Any:
        self.posts.append((url, data, headers))
        return type("R", (), {"status_code": self.status, "text": "", "headers": {}, "reason": "Gone"})()


def setup(conn: sqlite3.Connection, tmp_path: Path) -> tuple[str, str, Browser, push.Vapid]:
    hid = add_household(conn)
    aid = add_appliance(conn, hid)
    store.set_last_done(conn, hid, aid, JOB, source="never", on=None, at=T0)
    b = Browser()
    push.subscribe(conn, hid, b.sub, at=T0)
    return hid, aid, b, push.load_vapid(tmp_path / "vapid.pem", create=True)


def test_recorded_subscription_gets_page(conn: sqlite3.Connection, loader: Callable[[str], Card | None],
                                         tmp_path: Path) -> None:
    hid, aid, browser, vapid = setup(conn, tmp_path)
    session = FakeSession()
    sent = push.dispatch(conn, T0, loader, push.webpush_sender(vapid, session=session))
    assert [(s.delivered, s.gone) for s in sent] == [(1, 0)]
    [(url, body, headers)] = session.posts
    assert url == browser.sub["endpoint"] and headers["Authorization"].startswith("vapid t=")
    msg = browser.decrypt(body)
    assert msg["page"] == 3 and msg["footnote"] == "from your manual, p. 3"
    assert msg["title"] == "Your washer's debris filter is due." and "10 minutes. Start?" in msg["body"]
    assert msg["key"] == f"{aid}:{JOB}:2026-10-27"
    # Once per due date: the next tick sends nothing.
    assert push.dispatch(conn, T0, loader, push.webpush_sender(vapid, session=session)) == []
    assert len(session.posts) == 1
    assert [r["kind"] for r in conn.execute("SELECT kind FROM pilot_events")] == ["reminder_sent"]


def test_not_due_is_not_pushed(conn: sqlite3.Connection, loader: Callable[[str], Card | None],
                               tmp_path: Path) -> None:
    hid, aid, _, vapid = setup(conn, tmp_path)
    store.set_last_done(conn, hid, aid, JOB, source="user", on=T0.date(), at=T0)
    session = FakeSession()
    assert push.dispatch(conn, datetime(2027, 1, 26, 12, tzinfo=UTC), loader,
                         push.webpush_sender(vapid, session=session)) == []
    assert push.dispatch(conn, datetime(2027, 1, 27, 8, tzinfo=UTC), loader,
                         push.webpush_sender(vapid, session=session))[0].delivered == 1


def test_gone_endpoint_is_dropped_and_not_counted(conn: sqlite3.Connection, loader: Callable[[str], Card | None],
                                                  tmp_path: Path) -> None:
    hid, _, _, vapid = setup(conn, tmp_path)
    sent = push.dispatch(conn, T0, loader, push.webpush_sender(vapid, session=FakeSession(410)))
    assert [(s.delivered, s.gone) for s in sent] == [(0, 1)]
    assert push.subscriptions(conn, hid) == []
    assert conn.execute("SELECT count(*) FROM pilot_events").fetchone()[0] == 0


def test_vapid_key_is_0600(tmp_path: Path) -> None:
    v = push.load_vapid(tmp_path / "k.pem", create=True)
    assert (tmp_path / "k.pem").stat().st_mode & 0o777 == 0o600
    assert len(base64.urlsafe_b64decode(v.public_key + "==")) == 65


def test_a_snoozed_reminder_is_sent_once_more_and_the_date_never_moves(
        conn: sqlite3.Connection, loader: Callable[[str], Card | None], tmp_path: Path) -> None:
    hid, aid, _, vapid = setup(conn, tmp_path)
    session = FakeSession()
    send = push.webpush_sender(vapid, session=session)
    [sent] = push.dispatch(conn, T0, loader, send)
    due_before = conn.execute("SELECT * FROM job_state").fetchall()
    assert store.record_reminder_snoozed(conn, hid, sent.key, 3, at=T0)
    assert push.dispatch(conn, T0 + timedelta(hours=2, minutes=59), loader, send) == []  # still snoozed
    assert [s.delivered for s in push.dispatch(conn, T0 + timedelta(hours=3), loader, send)] == [1]
    assert push.dispatch(conn, T0 + timedelta(hours=8), loader, send) == []               # once more, not again
    assert store.record_reminder_snoozed(conn, hid, sent.key, 1, at=T0 + timedelta(hours=8))  # a second snooze
    assert len(push.dispatch(conn, T0 + timedelta(hours=9), loader, send)) == 1
    assert len(session.posts) == 3
    assert conn.execute("SELECT * FROM job_state").fetchall() == due_before                # due_on, last_done
    [row] = conn.execute("SELECT snooze_until FROM pilot_events WHERE kind = 'reminder_snoozed' ORDER BY id LIMIT 1")
    assert row["snooze_until"] == "2026-10-27T15:00:00+00:00"


def test_only_a_sent_reminder_can_be_snoozed(conn: sqlite3.Connection) -> None:
    hid = add_household(conn)
    assert not store.record_reminder_snoozed(conn, hid, "ap_x:job:2026-10-27", at=T0)
    assert conn.execute("SELECT count(*) FROM pilot_events").fetchone()[0] == 0


def test_a_reminder_only_in_the_calendar_feed_is_not_snoozed(conn: sqlite3.Connection) -> None:
    # the push loop is the only thing that sends again: a 204 here would promise a reminder that never comes
    hid = add_household(conn)
    aid = add_appliance(conn, hid)
    key = f"{aid}:{JOB}:2026-10-27"
    store.record_reminder_sent(conn, hid, key, "ics", at=T0)
    assert not store.record_reminder_snoozed(conn, hid, key, at=T0)
    store.record_reminder_sent(conn, hid, key, "push", at=T0)
    assert store.record_reminder_snoozed(conn, hid, key, at=T0)

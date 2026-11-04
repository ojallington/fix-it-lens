"""Web push (VAPID) for due reminders. The keys are read by path (~/.config/fleet/fixit-vapid.pem), never from env.

`dispatch` runs on a timer: each due job whose moment has come is pushed once per due date to each person its alert
reaches (their own alerts, contracts Alerts), and a person in quiet hours gets it when they end. A job that is not due
is never pushed, and pushing changes no date.
"""

from __future__ import annotations

import base64
import json
import os
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app.schedule import people, store
from app.schedule.dates import household_tz
from app.schedule.due import CardLoader, Due, due
from app.schedule.remind import Reminder, reminder

DEFAULT_VAPID = Path.home() / ".config/fleet/fixit-vapid.pem"
# The VAPID contact; push services check its shape only. Set the deployment's own with FIXIT_VAPID_SUB.
VAPID_SUBJECT = os.environ.get("FIXIT_VAPID_SUB", "mailto:fixit@example.com")


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


@dataclass(frozen=True)
class Vapid:
    pem_path: Path
    public_key: str  # applicationServerKey for pushManager.subscribe, base64url of the raw P-256 point


def load_vapid(path: Path = DEFAULT_VAPID, *, create: bool = False) -> Vapid:
    if not path.exists():
        if not create:
            raise FileNotFoundError(f"no VAPID key at {path}")
        key = ec.generate_private_key(ec.SECP256R1())
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(pem)
    priv = serialization.load_pem_private_key(path.read_bytes(), password=None)
    assert isinstance(priv, ec.EllipticCurvePrivateKey)
    raw = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return Vapid(path, _b64url(raw))


@dataclass(frozen=True)
class Subscription:
    id: int
    household_id: str
    person_id: str | None   # None: subscribed before people had their own alerts; the home's first admin
    endpoint: str
    p256dh: str
    auth: str

    def info(self) -> dict[str, Any]:
        return {"endpoint": self.endpoint, "keys": {"p256dh": self.p256dh, "auth": self.auth}}


def subscribe(conn: sqlite3.Connection, household_id: str, sub: dict[str, Any], person_id: str | None = None,
              at: datetime | None = None) -> None:
    """Keep a browser PushSubscription for one person. An endpoint moves to whoever registered it last."""
    endpoint, keys = str(sub["endpoint"]), sub["keys"]
    if not endpoint.startswith("https://"):
        raise ValueError("a push endpoint is https")
    conn.execute(
        "INSERT INTO push_subscriptions (household_id, person_id, endpoint, p256dh, auth, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (endpoint) DO UPDATE SET household_id = excluded.household_id,"
        " person_id = excluded.person_id, p256dh = excluded.p256dh, auth = excluded.auth",
        (household_id, person_id, endpoint, str(keys["p256dh"]), str(keys["auth"]), store.now_iso(at)))


def unsubscribe(conn: sqlite3.Connection, household_id: str, endpoint: str) -> None:
    conn.execute("DELETE FROM push_subscriptions WHERE household_id = ? AND endpoint = ?", (household_id, endpoint))


def subscriptions(conn: sqlite3.Connection, household_id: str) -> list[Subscription]:
    return [Subscription(r["id"], r["household_id"], r["person_id"], r["endpoint"], r["p256dh"], r["auth"])
            for r in conn.execute("SELECT * FROM push_subscriptions WHERE household_id = ? ORDER BY id",
                                  (household_id,))]


def _phones(conn: sqlite3.Connection, household_id: str) -> dict[str, list[Subscription]]:
    """Each person's subscribed browsers; an older one with no person is the first admin's."""
    admin = store.first_admin(conn, household_id)
    out: dict[str, list[Subscription]] = {}
    for sub in subscriptions(conn, household_id):
        pid = sub.person_id or (admin["id"] if admin else None)
        if pid is not None:
            out.setdefault(pid, []).append(sub)
    return out


# Sends one encrypted push; returns the push service's HTTP status.
Sender = Callable[[Subscription, Reminder], int]


def webpush_sender(vapid: Vapid, ttl_s: int = 12 * 3600, session: Any = None) -> Sender:
    from pywebpush import WebPushException, webpush

    def send(sub: Subscription, r: Reminder) -> int:
        try:
            resp = webpush(sub.info(), json.dumps(r.payload()), vapid_private_key=str(vapid.pem_path),
                           vapid_claims={"sub": VAPID_SUBJECT}, ttl=ttl_s, timeout=10,
                           headers={"Urgency": "high", "Topic": r.key.replace(":", "-")[:32]},
                           requests_session=session)
        except WebPushException as e:
            return int(e.response.status_code) if e.response is not None else 0
        return int(getattr(resp, "status_code", 201))

    return send


@dataclass(frozen=True)
class Sent:
    household_id: str
    key: str
    delivered: int          # browsers that took it
    gone: int               # browsers the push service no longer knows; dropped
    people: tuple[str, ...] = ()   # the people it reached
    waiting: tuple[str, ...] = ()  # the people in quiet hours, who get it when theirs end


def _push(conn: sqlite3.Connection, d: Due, r: Reminder, now: datetime, send: Sender, *, again: bool,
          log: bool) -> Sent:
    """Push one reminder to the people its alert reaches (people.alerted) on their own browsers. Someone in quiet
    hours waits; with `again` false, someone already pushed it (since its last snooze) is skipped. Each person
    pushed gets a person_pushes row, and the home's first push of it a reminder_sent row, when `log` is set."""
    hid = d.household_id
    hh = conn.execute("SELECT country FROM households WHERE id = ?", (hid,)).fetchone()
    local = now.astimezone(household_tz(hh["country"])).time()
    phones = _phones(conn, hid)
    delivered = gone = 0
    reached: list[str] = []
    waiting: list[str] = []
    for p, a in people.alerted(conn, hid, appliance_id=d.appliance_id, assignee=d.assignee, channel="push"):
        if p["id"] not in phones or not (again or store.person_to_push(conn, hid, p["id"], r.key, now)):
            continue
        if people.quiet_now(a, local):
            waiting.append(p["name"])
            continue
        took = 0
        for sub in phones[p["id"]]:
            status = send(sub, r)
            if 200 <= status < 300:
                took += 1
            elif status in (404, 410):
                gone += 1
                conn.execute("DELETE FROM push_subscriptions WHERE id = ?", (sub.id,))
        if took:
            delivered += took
            reached.append(p["name"])
            if log:
                store.record_person_push(conn, p["id"], r.key, at=now)
    if reached and log and store.reminder_to_send(conn, hid, r.key, "push", now):
        store.record_reminder_sent(conn, hid, r.key, "push", at=now)
    return Sent(hid, r.key, delivered, gone, tuple(reached), tuple(waiting))


def dispatch(conn: sqlite3.Connection, now: datetime, load_card: CardLoader, send: Sender) -> list[Sent]:
    """Push every due reminder to each person it reaches who has not had it for its due date, and once more after a
    snooze. Someone in quiet hours gets it when they end. A 404/410 endpoint is dropped."""
    out: list[Sent] = []
    households = [r["id"] for r in conn.execute(
        "SELECT DISTINCT s.household_id AS id FROM push_subscriptions s JOIN households h ON h.id = s.household_id"
        " WHERE h.is_test = 0 AND h.deleted_at IS NULL ORDER BY s.household_id")]  # a test household gets none
    for hid in households:
        for d in due(conn, hid, now, load_card):
            if not d.is_due(now) or d.due_on is None:
                continue
            sent = _push(conn, d, reminder(d, now.date()), now, send, again=False, log=True)
            if sent.people or sent.gone:
                out.append(sent)
    return out


class NotDue(LookupError):
    pass


def send_now(conn: sqlite3.Connection, household_id: str, job_id: str, now: datetime, load_card: CardLoader,
             send: Sender, *, appliance_id: str | None = None) -> tuple[Reminder, Sent]:
    """An operator's push of one reminder that is due now, in the words `dispatch` would send, to the people it
    reaches. It sends whether or not they had it already (for filming, or a second look), but only a job that is
    due: it moves no date. Each person's alerts still hold, quiet hours too. A test household may be sent one too;
    only a real one logs it."""
    hh = conn.execute("SELECT is_test, deleted_at FROM households WHERE id = ?", (household_id,)).fetchone()
    if hh is None or hh["deleted_at"] is not None:
        raise NotDue(f"{household_id}: no such household")
    jobs = [d for d in due(conn, household_id, now, load_card)
            if d.job_id == job_id and (appliance_id is None or d.appliance_id == appliance_id)]
    if not jobs:
        raise NotDue(f"{household_id}: no {job_id} on a checked card")
    if len(jobs) > 1:
        raise NotDue(f"{household_id}: {job_id} on {len(jobs)} appliances; name one")
    d = jobs[0]
    if d.due_on is None or not d.is_due(now):
        when = "has no date yet (last done not answered)" if d.due_on is None else f"is due on {d.due_on}"
        raise NotDue(f"{d.alias} {job_id} {when}; nothing is sent before it is due")
    r = reminder(d, now.date())
    return r, _push(conn, d, r, now, send, again=True, log=not hh["is_test"])

"""The schedule's side of the store (contracts/store.sql): households and their invite codes, `job_state`, and the
pilot events. Each pilot_events kind has one writer, here. app/store.py owns appliances, history and sessions.

A due date moves only through three doors: `set_last_done` (the user's answer to "When did you last do this?"),
`record_job_done_checked` (every step check of the job passed) and `record_job_done_by_person` (the job's steps all
done, some on the person's word where the camera could not see). Nothing else writes `last_done`.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from contracts import STORE_SQL

LastDoneSource = Literal["user", "checked", "never", "unknown"]
Channel = Literal["push", "ics", "caldav", "ha", "app"]

# Invite codes avoid the look-alikes (0/O, 1/I/L, 5/S, 8/B).
INVITE_ALPHABET = "ACDEFGHJKMNPQRTUVWXY234679"
INVITE_LEN = 10

class NotFound(LookupError):
    """The row does not exist, or belongs to another household."""


def now_iso(at: datetime | None = None) -> str:
    return (at or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")


def _migrations() -> list[tuple[int, str]]:
    text = STORE_SQL.read_text()
    parts = re.split(r"^-- migration (\d+)\b.*$", text, flags=re.M)
    return [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts), 2)]


class _Fetched(sqlite3.Cursor):
    """A cursor whose rows were all read while the connection was held, so reading them later touches nothing."""
    rows: list[Any]

    def fetchone(self) -> Any:
        return self.rows.pop(0) if self.rows else None

    def fetchall(self) -> list[Any]:
        out, self.rows = self.rows, []
        return out

    def fetchmany(self, size: int | None = 1) -> list[Any]:
        n = self.arraysize if size is None else size
        out, self.rows = self.rows[:n], self.rows[n:]
        return out

    def __iter__(self) -> _Fetched:
        return self

    def __next__(self) -> Any:
        if not self.rows:
            raise StopIteration
        return self.rows.pop(0)


class _Shared(sqlite3.Connection):
    """One connection shared by the server's worker threads, one statement at a time.

    Requests run in a thread pool against this one connection. Without a lock, two statements at once fail
    ("bad parameter or other API misuse") or hand one request another's rows, so a good token could read as none.
    Each statement runs and is read to the end under the lock; a `with conn:` block holds it to its end."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.lock = threading.RLock()

    def execute(self, sql: str, parameters: Any = (), /) -> sqlite3.Cursor:
        with self.lock:
            cur = _Fetched(self)
            cur.row_factory = self.row_factory
            sqlite3.Cursor.execute(cur, sql, parameters)
            cur.rows = sqlite3.Cursor.fetchall(cur)
            return cur

    def executemany(self, sql: str, parameters: Any, /) -> sqlite3.Cursor:
        with self.lock:
            return super().executemany(sql, parameters)

    def executescript(self, script: str, /) -> sqlite3.Cursor:
        with self.lock:
            return super().executescript(script)

    def __enter__(self) -> _Shared:
        self.lock.acquire()
        return self

    def __exit__(self, *exc: Any) -> Literal[False]:
        try:
            super().__exit__(*exc)
        finally:
            self.lock.release()
        return False


def open_store(path: Path | str) -> sqlite3.Connection:
    """Open (and migrate) the store, creating it 0600. ':memory:' works for tests. The connection is safe to share
    between threads (`_Shared`)."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(FileExistsError):  # a new store is the owner's alone; its journal gets the same mode
            os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None, factory=_Shared)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA secure_delete = ON")  # a deleted row is overwritten, not left in the file's free pages
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    for n, sql in _migrations():
        if n > version:
            conn.executescript(sql)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---- secrets and tokens ----------------------------------------------------------------------------------------

def load_secret(path: Path) -> bytes:
    """The server's token secret, read by path; created 0600 on first use."""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    return bytes.fromhex(path.read_text().strip())


def _sign(secret: bytes, purpose: str, household_id: str) -> str:
    return hmac.new(secret, f"{purpose}:{household_id}".encode(), hashlib.sha256).hexdigest()[:40]


def household_token(secret: bytes, household_id: str, epoch: int = 0) -> str:
    """The household's own bearer token. Epoch 0 is the token every household had before rotation existed; each
    `rotate_household_token` signs the next epoch in, so an older token no longer verifies."""
    return f"{household_id}-{_sign(secret, 'bearer', household_id if not epoch else f'{household_id}#{epoch}')}"


def current_household_token(conn: sqlite3.Connection, secret: bytes, household_id: str) -> str:
    row = conn.execute("SELECT bearer_epoch FROM households WHERE id = ?", (household_id,)).fetchone()
    return household_token(secret, household_id, int(row["bearer_epoch"]) if row is not None else 0)


def rotate_household_token(conn: sqlite3.Connection, secret: bytes, household_id: str) -> str:
    """A new bearer token for the household; the old one stops working at once. App, agent, Home Assistant and
    calendar tokens are not touched."""
    with conn:
        cur = conn.execute("UPDATE households SET bearer_epoch = bearer_epoch + 1 WHERE id = ? AND deleted_at IS NULL",
                           (household_id,))
    if cur.rowcount != 1:
        raise KeyError(household_id)
    return current_household_token(conn, secret, household_id)


def feed_token(secret: bytes, household_id: str) -> str:
    return f"{household_id}-{_sign(secret, 'feed', household_id)}"


def _from_token(conn: sqlite3.Connection, secret: bytes, purpose: str, token: str) -> sqlite3.Row | None:
    hid, _, sig = token.partition("-")
    if not hid or not sig:
        return None
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM households WHERE id = ? AND deleted_at IS NULL AND consent_at IS NOT NULL", (hid,)
    ).fetchone()
    epoch = int(row["bearer_epoch"]) if row is not None and purpose == "bearer" else 0
    want = _sign(secret, purpose, hid if not epoch else f"{hid}#{epoch}")
    return row if row is not None and hmac.compare_digest(sig, want) else None


def household_from_token(conn: sqlite3.Connection, secret: bytes, token: str) -> sqlite3.Row | None:
    """The household behind a bearer token, or None (bad token, unclaimed or deleted household)."""
    return _from_token(conn, secret, "bearer", token)


def household_from_feed_token(conn: sqlite3.Connection, secret: bytes, token: str) -> sqlite3.Row | None:
    return _from_token(conn, secret, "feed", token)


def person_feed_token(secret: bytes, household_id: str, person_id: str) -> str:
    """One person's calendar: `<household>.<person>.<signature>` (a household's own is `<household>-<signature>`)."""
    return f"{household_id}.{person_id}.{_sign(secret, 'feed', f'{household_id}:{person_id}')}"


def from_feed_token(conn: sqlite3.Connection, secret: bytes,
                    token: str) -> tuple[sqlite3.Row, str | None] | None:
    """The household behind a feed token, and the person whose feed it is (None: the whole home's), or None for a
    bad token, a gone household, or a person who has left it."""
    if token.count(".") != 2:
        row = household_from_feed_token(conn, secret, token)
        return None if row is None else (row, None)
    hid, pid, sig = token.split(".")
    if not hmac.compare_digest(sig, _sign(secret, "feed", f"{hid}:{pid}")):
        return None
    row = household_from_feed_token(conn, secret, feed_token(secret, hid))
    if row is None or conn.execute("SELECT 1 FROM people WHERE id = ? AND household_id = ? AND removed_at IS NULL",
                                   (pid, hid)).fetchone() is None:
        return None
    return row, pid


ByKind = Literal["app", "agent", "ha"]
TokenKind = Literal["agent", "ha", "feed"]
Scope = Literal["read", "write"]


@dataclass(frozen=True)
class Caller:
    """Who a request comes from: the household, the kind of client, the person it speaks for, and what it may do.
    The household's own bearer token is the app's and speaks for the household's first admin; the others are tokens
    the household issued (household_tokens)."""
    household: sqlite3.Row
    by_kind: ByKind | Literal["feed"]
    scope: Scope
    label: str
    person_id: str | None = None
    token_id: str | None = None

    @property
    def household_id(self) -> str:
        return str(self.household["id"])


def first_admin(conn: sqlite3.Connection, household_id: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT id, name FROM people WHERE household_id = ? AND role = 'admin' AND removed_at IS NULL"
        " ORDER BY created_at, rowid LIMIT 1", (household_id,)).fetchone()
    return row


def is_admin(conn: sqlite3.Connection, person_id: str | None) -> bool:
    return person_id is not None and conn.execute(
        "SELECT 1 FROM people WHERE id = ? AND role = 'admin' AND removed_at IS NULL",
        (person_id,)).fetchone() is not None


def _sha(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_token(conn: sqlite3.Connection, household_id: str, *, by_kind: TokenKind, scope: Scope, label: str,
                created_by: str | None = None, at: datetime | None = None) -> tuple[str, str]:
    """A new token for the household's agent, Home Assistant or calendar: (id, token). Only its hash is stored, so
    the token is shown once. A feed token is read only."""
    if by_kind == "feed" and scope != "read":
        raise ValueError("a feed token is read only")
    tid = "t_" + secrets.token_hex(8)
    token = "fxt_" + secrets.token_urlsafe(32)
    conn.execute("INSERT INTO household_tokens (id, household_id, by_kind, scope, label, token_sha256, created_by,"
                 " created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (tid, household_id, by_kind, scope, label, _sha(token), created_by, now_iso(at)))
    return tid, token


def revoke_token(conn: sqlite3.Connection, household_id: str, token_id: str, at: datetime | None = None) -> bool:
    cur = conn.execute("UPDATE household_tokens SET revoked_at = ? WHERE id = ? AND household_id = ?"
                       " AND revoked_at IS NULL AND by_kind != 'app'", (now_iso(at), token_id, household_id))
    return cur.rowcount == 1


def list_tokens(conn: sqlite3.Connection, household_id: str) -> list[sqlite3.Row]:
    return conn.execute("SELECT id, by_kind, scope, label, created_at, last_used_at FROM household_tokens"
                        " WHERE household_id = ? AND revoked_at IS NULL AND by_kind != 'app' ORDER BY rowid",
                        (household_id,)).fetchall()


def caller_from_token(conn: sqlite3.Connection, secret: bytes, token: str,
                      at: datetime | None = None) -> Caller | None:
    """The caller behind a bearer token: the household's own (the app, for its first admin), or one it issued and
    has not revoked."""
    row = household_from_token(conn, secret, token)
    if row is not None:
        admin = first_admin(conn, str(row["id"]))
        return Caller(row, "app", "write", admin["name"] if admin else "Admin", admin["id"] if admin else None)
    if not token.startswith("fxt_"):
        return None
    tok = conn.execute(
        "SELECT t.id AS token_id, t.by_kind, t.scope, t.label, t.person_id, t.household_id FROM household_tokens t"
        " JOIN households h ON h.id = t.household_id WHERE t.token_sha256 = ? AND t.revoked_at IS NULL"
        " AND h.deleted_at IS NULL AND h.consent_at IS NOT NULL", (_sha(token),)).fetchone()
    if tok is None:
        return None
    conn.execute("UPDATE household_tokens SET last_used_at = ? WHERE id = ?", (now_iso(at), tok["token_id"]))
    hh = conn.execute("SELECT * FROM households WHERE id = ?", (tok["household_id"],)).fetchone()
    label = tok["label"]
    if tok["person_id"]:
        p = conn.execute("SELECT name FROM people WHERE id = ?", (tok["person_id"],)).fetchone()
        label = p["name"] if p else label
    return Caller(hh, tok["by_kind"], tok["scope"], label, tok["person_id"], tok["token_id"])


def token_scope(conn: sqlite3.Connection, secret: bytes, token: str) -> Scope | None:
    """What a bearer token may do, without counting it as used: "write" for the household's own, the scope of one it
    issued to an agent or its Home Assistant, None for anything else (a calendar token, revoked, unknown)."""
    if household_from_token(conn, secret, token) is not None:
        return "write"
    if not token.startswith("fxt_"):
        return None
    row = conn.execute(
        "SELECT t.scope FROM household_tokens t JOIN households h ON h.id = t.household_id WHERE t.token_sha256 = ?"
        " AND t.revoked_at IS NULL AND t.by_kind != 'feed' AND h.deleted_at IS NULL", (_sha(token),)).fetchone()
    return None if row is None else row["scope"]  # type: ignore[no-any-return]


# ---- invite codes and households -------------------------------------------------------------------------------

@dataclass(frozen=True)
class Invite:
    household_id: str
    code: str


def normalise_code(code: str) -> str:
    return re.sub(r"[\s-]", "", code).upper()


def create_invites(conn: sqlite3.Connection, n: int, *, cap_usd_day: float = 0.45, is_own: bool = False,
                   is_test: bool = False, country: str = "nl", lang: str = "nl",
                   at: datetime | None = None) -> list[Invite]:
    """Pre-create n households, each waiting for its code to be claimed. A test household (is_test) is never one
    of our own homes, is left out of the pilot's counts, and is sent no reminders."""
    if is_own and is_test:
        raise ValueError("a test household is never an own home")
    out = []
    for _ in range(n):
        code = "".join(secrets.choice(INVITE_ALPHABET) for _ in range(INVITE_LEN))
        hid = "h_" + secrets.token_hex(8)
        conn.execute(
            "INSERT INTO households (id, invite_code, country, lang, cap_usd_day, is_own, is_test, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (hid, code, country, lang, cap_usd_day, int(is_own), int(is_test), now_iso(at)),
        )
        out.append(Invite(hid, code))
    return out


def claim_invite(conn: sqlite3.Connection, code: str, *, consent: bool, country: str | None = None,
                 lang: str | None = None, at: datetime | None = None) -> str | None:
    """Claim an unused invite code with consent; the household id, or None for a bad, used or withdrawn code."""
    if consent is not True:
        return None
    cur = conn.execute(
        "UPDATE households SET consent_at = ?, country = COALESCE(?, country), lang = COALESCE(?, lang)"
        " WHERE invite_code = ? AND consent_at IS NULL AND deleted_at IS NULL RETURNING id",
        (now_iso(at), country, lang, normalise_code(code)),
    )
    row = cur.fetchone()
    if row is None:
        return None
    conn.execute("INSERT INTO people (id, household_id, name, role, created_at) VALUES (?, ?, 'Admin', 'admin', ?)",
                 ("p-" + secrets.token_hex(8), row["id"], now_iso(at)))  # whoever opened it is its first admin
    return str(row["id"])


def delete_household(conn: sqlite3.Connection, household_id: str) -> None:
    """Deletion on request (the consent form): every row of the household goes, by cascade."""
    conn.execute("DELETE FROM households WHERE id = ?", (household_id,))


def _owned_appliance(conn: sqlite3.Connection, household_id: str, appliance_id: str) -> None:
    if conn.execute("SELECT 1 FROM appliances WHERE id = ? AND household_id = ? AND deleted_at IS NULL",
                    (appliance_id, household_id)).fetchone() is None:
        raise NotFound(f"appliance {appliance_id}")


# ---- last_done: the only two doors -----------------------------------------------------------------------------

def set_last_done(conn: sqlite3.Connection, household_id: str, appliance_id: str, job_id: str, *,
                  source: Literal["user", "never", "unknown"], on: date | None, at: datetime | None = None) -> None:
    """The user's answer to "When did you last do this?": a date, "never" or "don't know"."""
    _owned_appliance(conn, household_id, appliance_id)
    if source == "user":
        if on is None:
            raise ValueError("a user answer needs a date")
        today = (at or datetime.now(UTC)).date()
        if on > today:
            raise ValueError("last done cannot be in the future")
    elif on is not None:
        raise ValueError(f"{source!r} takes no date")
    ts = now_iso(at)
    with conn:
        conn.execute(
            "INSERT INTO job_state (appliance_id, job_id, last_done, last_done_source, updated_at)"
            " VALUES (?, ?, ?, ?, ?) ON CONFLICT (appliance_id, job_id) DO UPDATE SET"
            " last_done = excluded.last_done, last_done_source = excluded.last_done_source,"
            " updated_at = excluded.updated_at",
            (appliance_id, job_id, on.isoformat() if on else None, source, ts),
        )
        if on is not None:
            conn.execute(
                "INSERT INTO history (household_id, appliance_id, kind, job_id, text, on_date, created_at)"
                " VALUES (?, ?, 'user_entry', ?, 'last done (user)', ?, ?)",
                (household_id, appliance_id, job_id, on.isoformat(), ts),
            )


def record_job_done_checked(conn: sqlite3.Connection, household_id: str, appliance_id: str, job_id: str,
                            session_id: str, *, on: date, at: datetime | None = None) -> None:
    """Every step check of the job passed (the judge plus the SAM witness). Only the session loop calls this."""
    _owned_appliance(conn, household_id, appliance_id)
    ts = now_iso(at)
    with conn:
        conn.execute(
            "INSERT INTO job_state (appliance_id, job_id, last_done, last_done_source, updated_at)"
            " VALUES (?, ?, ?, 'checked', ?) ON CONFLICT (appliance_id, job_id) DO UPDATE SET"
            " last_done = excluded.last_done, last_done_source = 'checked', updated_at = excluded.updated_at",
            (appliance_id, job_id, on.isoformat(), ts),
        )
        conn.execute(
            "INSERT INTO history (household_id, appliance_id, kind, job_id, session_id, on_date, created_at)"
            " VALUES (?, ?, 'job_done', ?, ?, ?, ?)",
            (household_id, appliance_id, job_id, session_id, on.isoformat(), ts),
        )
        _event(conn, household_id, "job_done_checked", appliance_id=appliance_id, job_id=job_id,
               session_id=session_id, at=at)


def record_job_done_by_person(conn: sqlite3.Connection, household_id: str, appliance_id: str, job_id: str,
                              session_id: str, *, on: date, at: datetime | None = None) -> None:
    """The job's steps all done, some confirmed by the person where the camera could not see them: last done on the
    person's word (source user), with a history line that says so. Never `checked`. Only the session loop calls this."""
    _owned_appliance(conn, household_id, appliance_id)
    ts = now_iso(at)
    with conn:
        conn.execute(
            "INSERT INTO job_state (appliance_id, job_id, last_done, last_done_source, updated_at)"
            " VALUES (?, ?, ?, 'user', ?) ON CONFLICT (appliance_id, job_id) DO UPDATE SET"
            " last_done = excluded.last_done, last_done_source = 'user', updated_at = excluded.updated_at",
            (appliance_id, job_id, on.isoformat(), ts),
        )
        conn.execute(
            "INSERT INTO history (household_id, appliance_id, kind, job_id, session_id, text, on_date, created_at)"
            " VALUES (?, ?, 'job_done', ?, ?, 'confirmed by you', ?, ?)",
            (household_id, appliance_id, job_id, session_id, on.isoformat(), ts),
        )


# ---- pilot events ----------------------------------------------------------------------------------------------

def _event(conn: sqlite3.Connection, household_id: str, kind: str, *, appliance_id: str | None = None,
           job_id: str | None = None, session_id: str | None = None, channel: Channel | None = None,
           reminder_key: str | None = None, snooze_until: datetime | None = None,
           at: datetime | None = None) -> None:
    conn.execute(
        "INSERT INTO pilot_events (household_id, appliance_id, job_id, session_id, kind, channel, reminder_key,"
        " snooze_until, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (household_id, appliance_id, job_id, session_id, kind, channel, reminder_key,
         now_iso(snooze_until) if snooze_until else None, now_iso(at)),
    )


def record_job_started(conn: sqlite3.Connection, household_id: str, appliance_id: str, job_id: str,
                       session_id: str, at: datetime | None = None) -> None:
    _owned_appliance(conn, household_id, appliance_id)
    _event(conn, household_id, "job_started", appliance_id=appliance_id, job_id=job_id, session_id=session_id, at=at)


def record_job_dismissed(conn: sqlite3.Connection, household_id: str, appliance_id: str, job_id: str,
                         session_id: str | None = None, at: datetime | None = None) -> None:
    _owned_appliance(conn, household_id, appliance_id)
    _event(conn, household_id, "job_dismissed", appliance_id=appliance_id, job_id=job_id, session_id=session_id,
           at=at)


def reminder_key(appliance_id: str, job_id: str, due_on: date) -> str:
    return f"{appliance_id}:{job_id}:{due_on.isoformat()}"


def reminder_was_sent(conn: sqlite3.Connection, household_id: str, key: str, channel: Channel) -> bool:
    return conn.execute(
        "SELECT 1 FROM pilot_events WHERE household_id = ? AND kind = 'reminder_sent' AND reminder_key = ?"
        " AND channel = ?", (household_id, key, channel)).fetchone() is not None


def reminder_to_send(conn: sqlite3.Connection, household_id: str, key: str, channel: Channel,
                     now: datetime) -> bool:
    """Never sent on this channel, or its latest snooze has run out and nothing was sent since: once more."""
    if not reminder_was_sent(conn, household_id, key, channel):
        return True
    snooze = conn.execute(
        "SELECT id, snooze_until FROM pilot_events WHERE household_id = ? AND kind = 'reminder_snoozed'"
        " AND reminder_key = ? ORDER BY id DESC LIMIT 1", (household_id, key)).fetchone()
    if snooze is None or datetime.fromisoformat(snooze["snooze_until"]) > now:
        return False
    return conn.execute(
        "SELECT 1 FROM pilot_events WHERE household_id = ? AND kind = 'reminder_sent' AND reminder_key = ?"
        " AND channel = ? AND id > ?", (household_id, key, channel, snooze["id"])).fetchone() is None


def record_reminder_snoozed(conn: sqlite3.Connection, household_id: str, key: str, hours: int = 24,
                            at: datetime | None = None) -> bool:
    """Snooze a reminder this household was pushed: the push loop sends it once more after `hours`. It never
    touches due_on or last_done. False (and nothing logged) for anyone else's key, and for a reminder that only
    reached a calendar feed: nothing would come again, so the snooze is not promised."""
    sent = conn.execute(
        "SELECT appliance_id, job_id FROM pilot_events WHERE household_id = ? AND kind = 'reminder_sent'"
        " AND reminder_key = ? AND channel = 'push' LIMIT 1", (household_id, key)).fetchone()
    if sent is None:
        return False
    t = at or datetime.now(UTC)
    _event(conn, household_id, "reminder_snoozed", appliance_id=sent["appliance_id"], job_id=sent["job_id"],
           reminder_key=key, snooze_until=t + timedelta(hours=hours), at=t)
    return True


def person_to_push(conn: sqlite3.Connection, household_id: str, person_id: str, key: str, now: datetime) -> bool:
    """This person was never pushed this reminder, or the home's latest snooze of it has run out and they have not
    been pushed it since: once more (person_pushes)."""
    pushed = [r[0] for r in conn.execute("SELECT at FROM person_pushes WHERE person_id = ? AND reminder_key = ?",
                                         (person_id, key))]
    if not pushed:
        return True
    snooze = conn.execute(
        "SELECT snooze_until FROM pilot_events WHERE household_id = ? AND kind = 'reminder_snoozed'"
        " AND reminder_key = ? ORDER BY id DESC LIMIT 1", (household_id, key)).fetchone()
    if snooze is None or datetime.fromisoformat(snooze["snooze_until"]) > now:
        return False
    until = datetime.fromisoformat(snooze["snooze_until"])
    return all(datetime.fromisoformat(at) < until for at in pushed)


def record_person_push(conn: sqlite3.Connection, person_id: str, key: str, at: datetime | None = None) -> None:
    conn.execute("INSERT OR IGNORE INTO person_pushes (person_id, reminder_key, at) VALUES (?, ?, ?)",
                 (person_id, key, now_iso(at)))


def record_reminder_sent(conn: sqlite3.Connection, household_id: str, key: str, channel: Channel,
                         at: datetime | None = None) -> None:
    appliance_id, job_id, _ = key.split(":", 2)
    _event(conn, household_id, "reminder_sent", appliance_id=appliance_id, job_id=job_id, channel=channel,
           reminder_key=key, at=at)


def record_reminder_opened(conn: sqlite3.Connection, household_id: str, key: str, channel: Channel,
                           at: datetime | None = None) -> bool:
    """Log an open of a reminder this household was sent; False (and nothing logged) for anyone else's key."""
    sent = conn.execute(
        "SELECT appliance_id, job_id FROM pilot_events WHERE household_id = ? AND kind = 'reminder_sent'"
        " AND reminder_key = ? LIMIT 1", (household_id, key)).fetchone()
    if sent is None:
        return False
    if conn.execute("SELECT 1 FROM pilot_events WHERE household_id = ? AND kind = 'reminder_opened'"
                    " AND reminder_key = ? AND channel = ?", (household_id, key, channel)).fetchone():
        return True  # the first open counts; a reload does not
    _event(conn, household_id, "reminder_opened", appliance_id=sent["appliance_id"], job_id=sent["job_id"],
           channel=channel, reminder_key=key, at=at)
    return True

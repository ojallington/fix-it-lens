"""The front door (contracts 1.28): a household opened without an invite code, through the open door or a judges'
link, and the return link that opens a household on another device.

The door is shut unless the latest door_log row says open. Only `python -m app.schedule.invites door open|close`
writes that row (`set_door`), with who and when; no route, flag or setting opens it. A judges' link keeps its caps
in its own invite_links row, set when it is made; nothing here knows their values. Codes are shown once and only
their hashes are stored.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from app.schedule.store import INVITE_ALPHABET, INVITE_LEN, now_iso

DoorState = Literal["open", "closed"]
RETURN_VALID = timedelta(minutes=15)


def _sha(code: str) -> str:
    return hashlib.sha256(code.strip().encode()).hexdigest()


# ---- the door ---------------------------------------------------------------------------------------------------

def set_door(conn: sqlite3.Connection, state: DoorState, *, by: str, at: datetime | None = None) -> None:
    """Open or shut the door. The operator verb is its only caller."""
    if state not in ("open", "closed"):
        raise ValueError(f"door state {state!r}")
    with conn:
        conn.execute("INSERT INTO door_log (state, by, at) VALUES (?, ?, ?)", (state, by, now_iso(at)))


def door(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """The latest door_log row (state, by, at), or None: never opened."""
    row: sqlite3.Row | None = conn.execute("SELECT state, by, at FROM door_log ORDER BY id DESC LIMIT 1").fetchone()
    return row


def door_open(conn: sqlite3.Connection) -> bool:
    row = door(conn)
    return row is not None and row["state"] == "open"


# ---- households from the door and from a judges' link --------------------------------------------------------------

def _unshown_code() -> str:
    """households.invite_code is NOT NULL UNIQUE; a door or link home gets one nobody is ever shown."""
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(INVITE_LEN + 6))


def _first_admin(conn: sqlite3.Connection, hid: str, at: datetime | None) -> None:
    conn.execute("INSERT INTO people (id, household_id, name, role, created_at) VALUES (?, ?, 'Admin', 'admin', ?)",
                 ("p-" + secrets.token_hex(8), hid, now_iso(at)))  # whoever opened it is its first admin


def open_door_household(conn: sqlite3.Connection, *, country: str | None, lang: str | None,
                        at: datetime | None = None) -> str:
    """A new household through the open door; the caller has checked the door and the pool."""
    hid = "h_" + secrets.token_hex(8)
    ts = now_iso(at)
    with conn:
        conn.execute("INSERT INTO households (id, invite_code, country, lang, consent_at, created_at, joined_via)"
                     " VALUES (?, ?, ?, ?, ?, ?, 'door')",
                     (hid, _unshown_code(), country or "nl", lang or "nl", ts, ts))
        _first_admin(conn, hid, at)
    return hid


@dataclass(frozen=True)
class Link:
    id: str
    label: str
    cap_usd_day_home: float
    cap_usd_total: float
    max_homes: int
    expires_at: str
    homes: tuple[str, ...]          # the households it has opened


def live_link(conn: sqlite3.Connection, code: str, at: datetime | None = None) -> Link | None:
    """The judges' link behind a code, or None when it is unknown, expired or revoked (one answer for all three)."""
    row = conn.execute("SELECT * FROM invite_links WHERE code_sha256 = ? AND kind = 'judges' AND revoked_at IS NULL"
                       " AND expires_at > ?", (_sha(code), now_iso(at))).fetchone()
    if row is None:
        return None
    homes = tuple(r[0] for r in conn.execute("SELECT id FROM households WHERE invite_link_id = ?", (row["id"],)))
    return Link(row["id"], row["label"], float(row["cap_usd_day_home"]), float(row["cap_usd_total"]),
                int(row["max_homes"]), row["expires_at"], homes)


def open_link_household(conn: sqlite3.Connection, link: Link, *, country: str | None, lang: str | None,
                        at: datetime | None = None) -> str | None:
    """A new household from a judges' link, under its per-home cap; None when the link has opened all it may (the
    count and the insert are one statement, so two scans at once cannot both take the last place)."""
    hid = "h_" + secrets.token_hex(8)
    ts = now_iso(at)
    with conn:
        cur = conn.execute(
            "INSERT INTO households (id, invite_code, country, lang, cap_usd_day, consent_at, created_at, joined_via,"
            " invite_link_id) SELECT ?, ?, ?, ?, ?, ?, ?, 'link', ? WHERE (SELECT count(*) FROM households"
            " WHERE invite_link_id = ?) < ?",
            (hid, _unshown_code(), country or "nl", lang or "nl", link.cap_usd_day_home, ts, ts, link.id, link.id,
             link.max_homes))
        if cur.rowcount != 1:
            return None
        _first_admin(conn, hid, at)
    return hid


def new_judges_link(conn: sqlite3.Connection, *, label: str, cap_usd_day_home: float, cap_usd_total: float,
                    max_homes: int, expires_at: datetime, at: datetime | None = None) -> tuple[str, str]:
    """A judges' link: (id, code). The code is returned once; only its hash is stored."""
    if expires_at <= (at or datetime.now(UTC)):
        raise ValueError("the link would already be expired")
    lid = "l_" + secrets.token_hex(6)
    code = secrets.token_urlsafe(24)
    with conn:
        conn.execute("INSERT INTO invite_links (id, code_sha256, kind, label, cap_usd_day_home, cap_usd_total,"
                     " max_homes, expires_at, created_at) VALUES (?, ?, 'judges', ?, ?, ?, ?, ?, ?)",
                     (lid, _sha(code), label, cap_usd_day_home, cap_usd_total, max_homes, now_iso(expires_at),
                      now_iso(at)))
    return lid, code


def revoke_link(conn: sqlite3.Connection, link_id: str, at: datetime | None = None) -> bool:
    with conn:
        cur = conn.execute("UPDATE invite_links SET revoked_at = ? WHERE id = ? AND revoked_at IS NULL",
                           (now_iso(at), link_id))
    return cur.rowcount == 1


# ---- the return link ---------------------------------------------------------------------------------------------

def new_return_code(conn: sqlite3.Connection, household_id: str, person_id: str,
                    at: datetime | None = None) -> tuple[str, datetime]:
    """A single-use code to open this household, as this person, on another device: (code, expires_at). A new one
    replaces the person's unused one."""
    now = (at or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    code = secrets.token_urlsafe(24)
    with conn:
        conn.execute("DELETE FROM return_codes WHERE household_id = ? AND person_id = ? AND used_at IS NULL",
                     (household_id, person_id))
        conn.execute("INSERT INTO return_codes (code_sha256, household_id, person_id, created_at, expires_at)"
                     " VALUES (?, ?, ?, ?, ?)",
                     (_sha(code), household_id, person_id, now_iso(now), now_iso(now + RETURN_VALID)))
    return code, now + RETURN_VALID


def use_return_code(conn: sqlite3.Connection, code: str, at: datetime | None = None) -> tuple[str, str] | None:
    """Spend a return code: (household_id, person_id), or None when it is used, expired or unknown, its household
    is gone, or its person has left the home."""
    ts = now_iso(at)
    with conn:
        row = conn.execute(
            "UPDATE return_codes SET used_at = ? WHERE code_sha256 = ? AND used_at IS NULL AND expires_at > ?"
            " AND person_id IN (SELECT id FROM people WHERE removed_at IS NULL"
            " AND people.household_id = return_codes.household_id)"
            " AND household_id IN (SELECT id FROM households WHERE deleted_at IS NULL AND consent_at IS NOT NULL)"
            " RETURNING household_id, person_id", (ts, _sha(code), ts)).fetchone()
    return None if row is None else (str(row["household_id"]), str(row["person_id"]))


def issue_app_token(conn: sqlite3.Connection, household_id: str, person_id: str, *, label: str = "phone",
                    at: datetime | None = None) -> str:
    """A new app token: one person's phone, speaking for them. Shown once; only its hash is stored."""
    token = "fxt_" + secrets.token_urlsafe(32)
    with conn:
        conn.execute("INSERT INTO household_tokens (id, household_id, by_kind, scope, person_id, label, token_sha256,"
                     " created_by, created_at) VALUES (?, ?, 'app', 'write', ?, ?, ?, ?, ?)",
                     ("t_" + secrets.token_hex(8), household_id, person_id, label,
                      hashlib.sha256(token.encode()).hexdigest(), person_id, now_iso(at)))
    return token

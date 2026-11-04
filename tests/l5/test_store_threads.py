"""The store's one connection is shared by the server's worker threads: requests at once each read their own rows,
and a good token is never read as none."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.schedule import door, people, store
from tests.l5.conftest import T0, add_household


def test_a_token_read_from_many_threads_at_once(tmp_path: Path) -> None:
    conn = store.open_store(tmp_path / "store.sqlite")
    hid = add_household(conn)
    secret = b"s" * 32
    [admin] = people.people(conn, hid)
    app_token = door.issue_app_token(conn, hid, admin["id"], at=T0)  # its lookup writes last_used_at too
    tokens = [app_token, store.household_token(secret, hid)]

    def look(i: int) -> tuple[str | None, int]:
        c = store.caller_from_token(conn, secret, tokens[i % 2], at=T0)
        return (None if c is None else c.person_id), len(people.people(conn, hid))

    with ThreadPoolExecutor(16) as pool:
        seen = list(pool.map(look, range(2000)))
    assert set(seen) == {(admin["id"], 1)}

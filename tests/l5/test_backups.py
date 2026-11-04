"""Store backups are removed 7 days after they were made, by name pattern only; a new store is created 0600."""

from __future__ import annotations

import logging
import os
import stat
from datetime import timedelta
from pathlib import Path

import pytest

from app.schedule import backups, store
from tests.l5.conftest import T0, add_household


def made(path: Path, days_ago: float) -> Path:
    path.write_bytes(b"x")
    t = (T0 - timedelta(days=days_ago)).timestamp()
    os.utime(path, (t, t))
    return path


def test_backups_go_seven_days_after_they_were_made(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    made(tmp_path / "store.before-m4-20261018T0900.sqlite", 9)
    made(tmp_path / "store.before-softdelete.sqlite", 7)          # exactly seven days: goes
    made(tmp_path / "store.before-m11-20261026T1652.sqlite", 6.9)  # not yet
    made(tmp_path / "store.sqlite", 30)                             # the store itself, never
    made(tmp_path / "other.before-x.sqlite", 30)                    # not a store backup
    target = made(tmp_path / "elsewhere.sqlite", 30)
    (tmp_path / "store.before-link.sqlite").symlink_to(target)      # a link is not followed or removed
    (tmp_path / "store.before-dir.sqlite").mkdir()
    with caplog.at_level(logging.INFO, logger="fixit.backups"):
        removed = backups.sweep(tmp_path, now=T0)
    assert removed == ["store.before-m4-20261018T0900.sqlite", "store.before-softdelete.sqlite"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "elsewhere.sqlite", "other.before-x.sqlite", "store.before-dir.sqlite", "store.before-link.sqlite",
        "store.before-m11-20261026T1652.sqlite", "store.sqlite"]
    [line] = caplog.messages
    assert line == ("removed 2 store backup(s) older than 7 days: store.before-m4-20261018T0900.sqlite, "
                    "store.before-softdelete.sqlite")
    assert backups.sweep(tmp_path, now=T0) == []


def test_the_server_sweeps_at_start_and_on_a_timer(tmp_path: Path) -> None:
    old = made(tmp_path / "store.before-a.sqlite", 8)
    sweeper = backups.Sweeper(tmp_path, every_s=0.05).start()
    try:
        assert not old.exists()  # at start, before the first tick
        later = made(tmp_path / "store.before-b.sqlite", 8)
        for _ in range(100):
            if not later.exists():
                break
            sweeper._stop.wait(0.02)
        assert not later.exists()  # and again on the timer
    finally:
        sweeper.stop()
    assert "Sweeper(private_root()).start()" in (Path(__file__).parents[2] / "deploy/serve.py").read_text()


def test_a_new_store_is_the_owners_alone(tmp_path: Path) -> None:
    prev = os.umask(0o022)
    try:
        conn = store.open_store(tmp_path / "private/store.sqlite")
    finally:
        os.umask(prev)
    conn.execute("SELECT 1")
    assert stat.S_IMODE((tmp_path / "private/store.sqlite").stat().st_mode) == 0o600
    store.open_store(tmp_path / "private/store.sqlite")  # an existing store opens as it is


def test_a_deleted_households_rows_leave_the_file(tmp_path: Path) -> None:
    conn = store.open_store(tmp_path / "store.sqlite")
    assert conn.execute("PRAGMA secure_delete").fetchone()[0] == 1
    hid = add_household(conn, lang="en")
    conn.execute("UPDATE households SET country = 'gb' WHERE id = ?", (hid,))
    marker = hid.encode()
    assert marker in (tmp_path / "store.sqlite").read_bytes()
    store.delete_household(conn, hid)
    conn.close()
    assert marker not in (tmp_path / "store.sqlite").read_bytes()

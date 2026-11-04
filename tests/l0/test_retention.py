"""Record mode's request bodies and their recorded replies go together, 7 days after the newest was written."""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from calls import retention

T0 = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def made(path: Path, days_ago: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    t = (T0 - timedelta(days=days_ago)).timestamp()
    os.utime(path, (t, t))
    return path


def sha(c: str) -> str:
    return c * 64


def test_a_body_and_its_replies_go_together_after_seven_days(tmp_path: Path,
                                                               caplog: pytest.LogCaptureFixture) -> None:
    old = [made(tmp_path / "requests" / f"{sha('a')}.json", 9), made(tmp_path / "fixtures/l1" / f"{sha('a')}.jsonl", 8),
           made(tmp_path / "fixtures/l5" / f"{sha('a')}.jsonl", 7)]                  # exactly seven days: goes
    fresh_reply = [made(tmp_path / "requests" / f"{sha('b')}.json", 30),
                   made(tmp_path / "fixtures/l1" / f"{sha('b')}.jsonl", 2)]          # recorded again: both stay
    lone = [made(tmp_path / "requests" / f"{sha('c')}.json", 8),                    # a body with no reply here
            made(tmp_path / "fixtures/l1" / f"{sha('d')}.jsonl", 8)]                # a reply with no body
    young = made(tmp_path / "requests" / f"{sha('e')}.json", 6.9)
    keep = [made(tmp_path / "fixtures-l6/l6" / f"{sha('f')}.jsonl", 30),            # an eval's replays stay
            made(tmp_path / "requests" / "notes.json", 30),                         # not named by a hash
            made(tmp_path / "ledger.jsonl", 30), made(tmp_path / "store.sqlite", 30),
            made(tmp_path / "sessions/s1/events.jsonl", 30)]
    target = made(tmp_path / "elsewhere.json", 30)
    link = tmp_path / "requests" / f"{sha('9')}.json"
    link.symlink_to(target)                                                          # a link is never followed
    with caplog.at_level(logging.INFO, logger="fixit.retention"):
        assert retention.sweep(tmp_path, now=T0) == 5
    assert not any(p.exists() for p in old + lone)
    assert all(p.exists() for p in [*fresh_reply, young, *keep, target]) and link.is_symlink()
    assert caplog.messages == ["removed 5 recorded request file(s) older than 7 days"]
    assert retention.sweep(tmp_path, now=T0) == 0


def test_the_server_sweeps_at_start_and_on_a_timer(tmp_path: Path) -> None:
    old = made(tmp_path / "requests" / f"{sha('a')}.json", 8)
    sweeper = retention.Sweeper(tmp_path, every_s=0.05).start()
    try:
        assert not old.exists()  # at start, before the first tick
        later = made(tmp_path / "fixtures/l1" / f"{sha('b')}.jsonl", 8)
        for _ in range(100):
            if not later.exists():
                break
            sweeper._stop.wait(0.02)
        assert not later.exists()  # and again on the timer
    finally:
        sweeper.stop()
    retention.Sweeper(None).start().stop()  # no private folder: nothing to sweep
    assert "retention.Sweeper(private_root()).start()" in (Path(__file__).parents[2] / "deploy/serve.py").read_text()


def test_the_dry_run_names_what_would_go_and_removes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                                            capsys: pytest.CaptureFixture[str]) -> None:
    body = made(tmp_path / "requests" / f"{sha('a')}.json", 9)
    reply = made(tmp_path / "fixtures/l1" / f"{sha('a')}.jsonl", 8)
    young = made(tmp_path / "fixtures/l1" / f"{sha('b')}.jsonl", 2)
    assert retention.report(tmp_path, T0) == [
        "at 2026-10-29 12:00 UTC: 2 of 3 recorded file(s) would go",
        "  fixtures/l1/: 1 go, 2026-10-21 12:00 to 2026-10-21 12:00; 1 stay",
        "  requests/: 1 go, 2026-10-20 12:00 to 2026-10-20 12:00; 0 stay"]
    later = retention.report(tmp_path, T0 + timedelta(days=6))
    assert later[0] == "at 2026-11-04 12:00 UTC: 3 of 3 recorded file(s) would go"
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path))
    assert retention.main(["--dry-run", "--at", "2026-11-03"]) == 0
    out = capsys.readouterr().out
    assert "(dry run: nothing removed)" in out and "at 2026-11-03 00:00 UTC:" in out
    assert body.exists() and reply.exists() and young.exists()
    with pytest.raises(SystemExit):
        retention.main([])  # only a dry run from the command line; the server's sweep is the one that removes


def test_a_sealed_copy_is_never_swept(tmp_path: Path) -> None:
    sealed = [made(tmp_path / "fixtures-sealed-1027/l1" / f"{sha('a')}.jsonl", 30),
              made(tmp_path / "fixtures-sealed-1027/l5" / f"{sha('b')}.jsonl", 30)]
    original = made(tmp_path / "fixtures/l1" / f"{sha('a')}.jsonl", 30)  # the copy's original still goes
    assert retention.sweep(tmp_path, now=T0 + timedelta(days=365)) == 1
    assert all(p.exists() for p in sealed) and not original.exists()
    assert all(not f.is_relative_to(tmp_path / "fixtures-sealed-1027") for f in retention.due(tmp_path, T0))

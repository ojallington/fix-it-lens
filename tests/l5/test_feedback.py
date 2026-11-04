"""Feedback from pilot households: kept as typed, only from the pilot cohort, withdrawable, and gone with the home."""

from __future__ import annotations

import json
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import erase
from app.feeds import feedback
from app.feeds.errors import install_errors
from app.feeds.router import Deps, make_router
from contracts.models.card import Card
from tests.l5.conftest import T0
from tests.l5.test_manage import token
from tests.l5.test_people import joined
from tests.l5.test_pilot_door import Clock, join

ROOT = Path(__file__).resolve().parents[2]
URL = "/api/v1/household/feedback"
NONE = dict.fromkeys(("would_use", "would_use_why", "practical", "hard", "unsafe", "unsafe_which", "monthly",
                      "friend"))


@pytest.fixture
def private(tmp_path: Path) -> Path:
    return tmp_path / "private"


@pytest.fixture
def api(conn: sqlite3.Connection, secret: bytes, loader: Callable[[str], Card | None],
        private: Path) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(make_router(Deps(conn, secret, loader, None, Clock(T0), invite_failures_per_hour=50,
                                        private=private)), prefix="/api/v1")
    install_errors(app)
    with TestClient(app, base_url="https://fixit.test") as c:
        yield c


def form(**answers: Any) -> dict[str, Any]:
    return {"lang": "en", "answers": {**NONE, **answers}, "quote": "anonymous", "quote_name": None}


def lines(private: Path) -> list[dict[str, Any]]:
    path = private / "feedback" / "feedback.jsonl"
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()] if path.exists() else []


def test_a_pilot_home_is_kept_as_typed(api: TestClient, conn: sqlite3.Connection, private: Path) -> None:
    hid, auth = join(api, conn)
    why = "  Yes — it found the filter\nI never knew about.  "
    r = api.post(URL, json=form(would_use="yes", would_use_why=why, practical="partly"), headers=auth)
    assert r.status_code == 201, r.text
    [line] = lines(private)
    assert line["id"] == r.json()["id"] and line["household"] == hid and line["source"] == "app"
    assert line["answers"]["would_use_why"] == why  # never trimmed or rewritten
    assert set(line["answers"]) == set(NONE) and line["answers"]["friend"] is None
    assert line["at"] == "2026-10-27T12:00:00Z" and line["withdrawn_at"] is None
    assert stat.S_IMODE((private / "feedback").stat().st_mode) == 0o700
    assert stat.S_IMODE((private / "feedback" / "feedback.jsonl").stat().st_mode) == 0o600
    assert api.get(URL, headers=auth).status_code == 405  # nothing reads it back


def test_only_the_pilot_cohort_sends_it(api: TestClient, conn: sqlite3.Connection, private: Path) -> None:
    for change in ("is_test = 1", "is_own = 1", "joined_via = 'door'"):
        hid, auth = join(api, conn)
        conn.execute(f"UPDATE households SET {change} WHERE id = ?", (hid,))  # noqa: S608 (the test's own text)
        r = api.post(URL, json=form(would_use="no"), headers=auth)
        assert r.status_code == 403 and r.json()["error"] == "not_pilot", change
        assert api.get("/api/v1/household", headers=auth).json()["pilot"] is False  # so the phone never asks
    hid, auth = join(api, conn)
    assert api.get("/api/v1/household", headers=auth).json()["pilot"] is True
    for kind in ("agent", "ha"):
        assert api.post(URL, json=form(), headers=token(api, auth, kind, "write")).status_code == 403
    _, viewer = joined(api, auth, "Kim", view_only=True)
    assert api.post(URL, json=form(practical="yes"), headers=viewer).status_code == 201  # anyone in the home
    assert [x["household"] for x in lines(private)] == [hid]


@pytest.mark.parametrize("bad", [
    form(hard="x" * 2001),                                                   # longer is refused, never cut
    {**form(), "quote": "named"},                                            # named needs the name
    {**form(), "quote_name": "Sam"},                                         # and only named has one
    {**form(), "answers": {"would_use": "yes"}},                             # every key, null when unanswered
    form(would_use="perhaps"),
    {**form(), "lang": "de"},
    {**form(), "household": "h_other"},                                      # the server says whose it is
])
def test_a_bad_form_is_refused(api: TestClient, conn: sqlite3.Connection, private: Path, bad: dict[str, Any]) -> None:
    _, auth = join(api, conn)
    r = api.post(URL, json=bad, headers=auth)
    assert r.status_code == 422 and r.json()["error"] == "invalid", r.text
    assert lines(private) == []


def test_the_longest_answer_and_a_named_quote(api: TestClient, conn: sqlite3.Connection, private: Path) -> None:
    _, auth = join(api, conn)
    r = api.post(URL, json={**form(hard="x" * 2000), "quote": "named", "quote_name": "Sam"}, headers=auth)
    assert r.status_code == 201, r.text
    assert lines(private)[0]["quote_name"] == "Sam"


def test_a_quote_is_withdrawn_by_its_home_only(api: TestClient, conn: sqlite3.Connection, private: Path) -> None:
    _, auth = join(api, conn)
    _, other = join(api, conn)
    named = {**form(would_use="yes"), "quote": "named", "quote_name": "Sam"}
    fid = api.post(URL, json=named, headers=auth).json()["id"]
    assert api.post(f"{URL}/{fid}/withdraw", headers=other).status_code == 404
    assert api.post(f"{URL}/{fid}/withdraw", headers=auth).status_code == 204
    [line] = lines(private)
    assert line["withdrawn_at"] == "2026-10-27T12:00:00Z" and line["quote_name"] is None
    assert line["answers"]["would_use"] == "yes"  # still counted in the totals
    assert api.post(f"{URL}/{fid}/withdraw", headers=auth).status_code == 204  # twice is fine
    assert lines(private) == [line]
    assert api.post(f"{URL}/fb-000000000000/withdraw", headers=auth).status_code == 404


def test_the_operator_withdraws_one_asked_by_email(api: TestClient, conn: sqlite3.Connection, private: Path,
                                                   monkeypatch: pytest.MonkeyPatch,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    _, auth = join(api, conn)
    fid = api.post(URL, json=form(), headers=auth).json()["id"]
    monkeypatch.setenv("FIXIT_PRIVATE", str(private))
    assert feedback.main(["withdraw", fid]) == 0 and lines(private)[0]["withdrawn_at"] is not None
    assert feedback.main(["withdraw", "fb-ffffffffffff"]) == 1
    assert "no such answer" in capsys.readouterr().out


def test_a_home_being_deleted_sends_nothing(api: TestClient, conn: sqlite3.Connection, private: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    hid, auth = join(api, conn)
    monkeypatch.setattr(erase, "_deleting", {hid})
    r = api.post(URL, json=form(would_use="yes"), headers=auth)
    assert r.status_code == 410 and lines(private) == []


def test_erase_takes_a_homes_lines_and_leaves_the_others(api: TestClient, conn: sqlite3.Connection,
                                                         private: Path) -> None:
    a, auth_a = join(api, conn)
    b, auth_b = join(api, conn)
    for auth in (auth_a, auth_b, auth_a):
        api.post(URL, json=form(), headers=auth)
    assert feedback.erase(private, a) == 2
    assert [x["household"] for x in lines(private)] == [b]
    assert feedback.erase(private, a) == 0
    assert stat.S_IMODE((private / "feedback" / "feedback.jsonl").stat().st_mode) == 0o600


def test_an_answer_written_during_an_erase_is_kept(private: Path) -> None:
    def rec(hid: str) -> dict[str, Any]:
        return feedback.record(hid, source="pdf", lang=None, answers=dict(NONE), quote="none", quote_name=None)

    def never(_: str) -> bool:
        return False

    for _ in range(10):
        feedback.append(private, rec("h_gone"), deleting=never)
    writers = [threading.Thread(target=feedback.append, args=(private, rec("h_kept")), kwargs={"deleting": never})
               for _ in range(15)]
    erasers = [threading.Thread(target=feedback.erase, args=(private, "h_gone")) for _ in range(5)]
    for t in writers + erasers:
        t.start()
    for t in writers + erasers:
        t.join()
    assert sorted(x["household"] for x in lines(private)) == ["h_kept"] * 15


def test_a_home_sends_at_most_twenty(api: TestClient, conn: sqlite3.Connection, private: Path) -> None:
    _, auth = join(api, conn)
    for _ in range(feedback.MAX_LINES_PER_HOUSEHOLD):
        assert api.post(URL, json=form(), headers=auth).status_code == 201
    r = api.post(URL, json=form(), headers=auth)
    assert r.status_code == 429 and r.json()["error"] == "too_many"


def test_no_server_code_but_the_feedback_module_names_the_file() -> None:
    named = sorted(str(p.relative_to(ROOT)) for d in ("app", "deploy", "calls", "integrations", "agent")
                   for p in (ROOT / d).rglob("*.py") if "feedback.jsonl" in p.read_text(encoding="utf-8"))
    assert named == ["app/feeds/feedback.py"]


def _held_elsewhere(private: Path) -> subprocess.Popen[str]:
    """Another process holding the feedback lock until its stdin closes (as the PDF import would)."""
    feedback._dir(private)
    code = ("import fcntl, os, sys; fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600);"
            " fcntl.flock(fd, fcntl.LOCK_EX); print('held', flush=True); sys.stdin.read()")
    p = subprocess.Popen([sys.executable, "-c", code, str(private / "feedback" / feedback.LOCK)],
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert p.stdout is not None and p.stdout.readline().strip() == "held"
    return p


@pytest.mark.parametrize("held_by", ["thread", "process"])
def test_an_append_waits_for_the_lock(private: Path, held_by: str) -> None:
    rec = feedback.record("h_kept", source="pdf", lang=None, answers=dict(NONE), quote="none", quote_name=None)
    release = threading.Event()
    other: subprocess.Popen[str] | None = None
    if held_by == "process":
        other = _held_elsewhere(private)
    else:
        def hold() -> None:
            with feedback._locked(private):
                release.wait(10)
        holder = threading.Thread(target=hold)
        holder.start()
        time.sleep(0.1)
    t = threading.Thread(target=feedback.append, args=(private, rec), kwargs={"deleting": lambda _: False})
    t.start()
    t.join(0.5)
    assert t.is_alive() and lines(private) == []  # it waits; nothing is written past the lock
    if other is not None:
        assert other.stdin is not None
        other.stdin.close()
        other.wait(10)
    release.set()
    t.join(10)
    assert [x["id"] for x in lines(private)] == [rec["id"]]

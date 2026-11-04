"""The dishwasher's Machine Clean reminder, end to end on the real Bosch card: the words and the manual page come from
the card (the job's name, its interval and that interval's quote), the tap opens the job's landing (no session
starts until Start), and an operator can push it on demand (app/feeds/remind_now.py) without moving its date."""

from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from app.feeds import push, remind_now
from app.schedule import store
from app.schedule.due import due, due_name
from contracts.models.card import Card
from tests.l5.conftest import ROOT, add_appliance, add_household
from tests.l5.test_push import Browser, FakeSession

CARD = "cards/bosch/SMV4HAX40N.yaml"
JOB = "machine_cleaning"
NOW = datetime(2026, 10, 31, 9, 30, tzinfo=UTC)  # 10:30 in Amsterdam


def bosch() -> Card:
    data = yaml.safe_load((ROOT / CARD).read_text())
    data["checks"] = {"citation": {"status": "pass", "failures": []}, "rules": {"status": "pass", "failures": []}}
    return Card.model_validate(data)


@pytest.fixture
def home(conn: sqlite3.Connection, tmp_path: Path) -> tuple[str, str, Browser, push.Vapid]:
    hid = add_household(conn, lang="en")
    aid = add_appliance(conn, hid, alias="dishwasher", card_path=CARD)
    b = Browser()
    push.subscribe(conn, hid, b.sub, at=NOW)
    return hid, aid, b, push.load_vapid(tmp_path / "vapid.pem", create=True)


def card_page(card: Card) -> int:
    job = next(j for j in card.jobs if j.id == JOB)
    assert job.interval is not None
    return next(q.page for q in card.quotes if q.id == job.interval.quote_ref)


def test_the_words_come_from_the_card_and_the_tap_opens_its_landing(
        conn: sqlite3.Connection, home: tuple[str, str, Browser, push.Vapid]) -> None:
    hid, aid, browser, vapid = home
    card = bosch()
    store.set_last_done(conn, hid, aid, JOB, source="user", on=date(2026, 8, 31), at=NOW)  # every 2 months: 31 Oct
    session = FakeSession()
    r, sent = push.send_now(conn, hid, JOB, NOW, {CARD: card}.get, push.webpush_sender(vapid, session=session))
    assert (sent.delivered, sent.gone) == (1, 0)
    got = browser.decrypt(session.posts[0][1])  # what the phone's service worker receives
    assert got["title"] == "Your dishwasher's Machine Clean is due."  # the card's due_name, not code
    assert card_page(card) == 42 and got["footnote"] == "from your manual, p. 42"
    assert got["body"] == f"{got['body_main']}\nfrom your manual, p. 42" and got["body_main"].endswith("Start?")
    assert got["why"] == "Last done: 31 Aug. Your manual says every 2 months (p. 42)."
    # The tap (phone/public/sw.js) navigates to this URL with the title; ?reminder= opens the landing for this job
    # (phone/test/app.test.ts "notification landing"), and a session starts only from its Start button.
    q = {k: v[0] for k, v in parse_qs(urlsplit(got["url"]).query).items()}
    assert q == {"appliance": aid, "job": JOB, "reminder": r.key, "alias": "dishwasher", "why": got["why"]}
    assert r.key == f"{aid}:{JOB}:2026-10-31"
    kinds = [row["kind"] for row in conn.execute("SELECT kind FROM pilot_events")]
    assert kinds == ["reminder_sent"]  # a real household's push is logged like the timer's


def test_the_card_names_it_machine_clean() -> None:
    job = next(j for j in bosch().jobs if j.id == JOB)
    assert (due_name(job, "en"), due_name(job, "nl")) == ("Machine Clean", "machinereiniging")


def test_on_demand_sends_again_but_never_early(conn: sqlite3.Connection,
                                               home: tuple[str, str, Browser, push.Vapid]) -> None:
    hid, aid, _, vapid = home
    load = {CARD: bosch()}.get
    store.set_last_done(conn, hid, aid, JOB, source="user", on=date(2026, 10, 1), at=NOW)  # due 1 Dec
    with pytest.raises(push.NotDue, match="is due on 2026-12-01"):
        push.send_now(conn, hid, JOB, NOW, load, lambda s, r: 201)
    assert [d.due_on for d in due(conn, hid, NOW, load) if d.job_id == JOB] == [date(2026, 12, 1)]  # unmoved
    store.set_last_done(conn, hid, aid, JOB, source="never", on=None, at=NOW)  # "never": due today
    statuses = iter([201, 201])
    first, _ = push.send_now(conn, hid, JOB, NOW, load, lambda s, r: next(statuses))
    assert push.dispatch(conn, NOW, load, lambda s, r: 500) == []  # the timer does not send it twice that day
    again, sent = push.send_now(conn, hid, JOB, NOW, load, lambda s, r: next(statuses))  # the operator can
    assert first.key == again.key and sent.delivered == 1
    assert "You said it has never been done." in again.body


def test_a_test_household_can_be_sent_one_and_logs_nothing(conn: sqlite3.Connection) -> None:
    inv = store.create_invites(conn, 1, cap_usd_day=0.45, is_test=True, country="nl", lang="en", at=NOW)[0]
    hid = store.claim_invite(conn, inv.code, consent=True, at=NOW)
    assert hid is not None
    aid = add_appliance(conn, hid, alias="dishwasher", card_path=CARD)
    push.subscribe(conn, hid, Browser().sub, at=NOW)
    store.set_last_done(conn, hid, aid, JOB, source="unknown", on=None, at=NOW)
    load = {CARD: bosch()}.get
    assert push.dispatch(conn, NOW, load, lambda s, r: 201) == []  # the timer sends a test household nothing
    _, sent = push.send_now(conn, hid, JOB, NOW, load, lambda s, r: 201)
    assert sent.delivered == 1 and conn.execute("SELECT count(*) FROM pilot_events").fetchone()[0] == 0


def test_the_command_keeps_quiet_at_night_and_dry_runs(conn: sqlite3.Connection, tmp_path: Path,
                                                       monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    import app.server

    db = tmp_path / "store.sqlite"
    live = store.open_store(db)
    hid = add_household(live, lang="en")
    aid = add_appliance(live, hid, alias="dishwasher", card_path=CARD)
    store.set_last_done(live, hid, aid, JOB, source="never", on=None, at=NOW)
    push.subscribe(live, hid, Browser().sub, at=NOW)
    monkeypatch.setenv("FIXIT_DB", str(db))
    monkeypatch.setattr(app.server, "checked_card_loader", lambda: {CARD: bosch()}.get)
    monkeypatch.setattr(push, "load_vapid", lambda p: pytest.fail("a dry run or a quiet hour read the VAPID key"))
    with pytest.raises(SystemExit, match="07:59 in Amsterdam"):
        remind_now.main([hid, JOB], now=datetime(2026, 10, 31, 6, 59, tzinfo=UTC))
    with pytest.raises(SystemExit, match="nothing is sent before it is due"):  # a never-done job: due at 09:00
        remind_now.main([hid, JOB, "--dry-run"], now=datetime(2026, 10, 31, 7, 30, tzinfo=UTC))
    remind_now.main([hid, JOB, "--dry-run"], now=NOW)
    out = capsys.readouterr().out
    assert "Your dishwasher's" in out and "from your manual, p. 42" in out and "nothing sent" in out
    assert live.execute("SELECT count(*) FROM pilot_events").fetchone()[0] == 0
    assert not remind_now.quiet(datetime(2026, 10, 31, 7, 0, tzinfo=UTC))  # 08:00 Amsterdam
    assert remind_now.quiet(datetime(2026, 10, 31, 21, 0, tzinfo=UTC))  # 22:00

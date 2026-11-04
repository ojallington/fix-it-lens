"""schedule.due: dates from the card's interval and last_done; checked quotes only; never/don't know due now."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from datetime import UTC, date, datetime

import pytest

from app.schedule import store
from app.schedule.due import due
from app.schedule.remind import next_line, reminder, schedule_line
from contracts.models.card import Card
from tests.l5.conftest import T0, add_appliance, add_household

Loader = Callable[[str], Card | None]
JOB = "clean_debris_filter"  # every 3 months, p. 3; 10 minutes (Acme example card)


def test_unanswered_job_has_no_date(conn: sqlite3.Connection, loader: Loader) -> None:
    hid = add_household(conn)
    add_appliance(conn, hid)
    [d] = due(conn, hid, T0, loader)
    assert (d.job_id, d.due_on, d.reason, d.page) == (JOB, None, "unanswered", 3)
    with pytest.raises(ValueError):
        reminder(d, T0.date())


def test_a_removed_appliance_has_nothing_due(conn: sqlite3.Connection, loader: Loader) -> None:
    from app.store import Store

    hid = add_household(conn)
    aid = add_appliance(conn, hid)
    assert Store(conn, b"\x00" * 32).remove_appliance(hid, aid)
    assert due(conn, hid, T0, loader) == []


def test_interval_from_last_done_across_month_end(conn: sqlite3.Connection, loader: Loader) -> None:
    hid = add_household(conn)
    aid = add_appliance(conn, hid)
    store.set_last_done(conn, hid, aid, JOB, source="user", on=date(2026, 8, 31), at=T0)
    [d] = due(conn, hid, T0, loader)
    assert d.due_on == date(2026, 11, 30) and d.reason == "scheduled"
    assert d.due_at == datetime(2026, 11, 30, 8, tzinfo=UTC)  # 09:00 Amsterdam, winter time
    assert not d.is_due(T0)
    assert d.is_due(datetime(2026, 11, 30, 8, tzinfo=UTC))
    assert d.quote == "Clean the debris filter every 3 months." and d.interval_quote == "from your manual, p. 3"


@pytest.mark.parametrize(("source", "words"), [("never", "never been done"), ("unknown", "weren't sure")])
def test_never_and_dont_know_are_due_now_and_say_so(conn: sqlite3.Connection, loader: Loader, source: str,
                                                    words: str) -> None:
    hid = add_household(conn)
    aid = add_appliance(conn, hid)
    store.set_last_done(conn, hid, aid, JOB, source=source, on=None, at=T0)  # type: ignore[arg-type]
    [d] = due(conn, hid, T0, loader)
    assert d.due_on == T0.date() and d.reason == source
    r = reminder(d, T0.date())
    assert r.title == "Your washer's debris filter is due."
    assert words in r.body and r.body.endswith("10 minutes. Start?")
    assert r.footnote == "from your manual, p. 3" and r.page == 3


@pytest.mark.parametrize("path", ["cards/acme/failed.yaml", "cards/acme/unchecked.yaml", "cards/acme/missing.yaml"])
def test_unchecked_quote_gives_no_job(conn: sqlite3.Connection, loader: Loader, path: str) -> None:
    hid = add_household(conn)
    aid = add_appliance(conn, hid, card_path=path)
    store.set_last_done(conn, hid, aid, JOB, source="never", on=None, at=T0)
    assert due(conn, hid, T0, loader) == []


def test_done_only_by_job_done_checked(conn: sqlite3.Connection, loader: Loader) -> None:
    hid = add_household(conn)
    aid = add_appliance(conn, hid)
    store.set_last_done(conn, hid, aid, JOB, source="never", on=None, at=T0)
    store.record_job_started(conn, hid, aid, JOB, "s1", at=T0)
    store.record_job_dismissed(conn, hid, aid, JOB, "s1", at=T0)
    assert due(conn, hid, T0, loader)[0].due_on == T0.date()  # started and dismissed: still due
    store.record_job_done_checked(conn, hid, aid, JOB, "s1", on=T0.date(), at=T0)
    [d] = due(conn, hid, T0, loader)
    assert (d.due_on, d.last_done_source) == (date(2027, 1, 27), "checked")
    kinds = [r["kind"] for r in conn.execute("SELECT kind FROM pilot_events ORDER BY id")]
    assert kinds == ["job_started", "job_dismissed", "job_done_checked"]



def test_a_job_done_on_the_persons_word_says_confirmed_by_you(conn: sqlite3.Connection, loader: Loader) -> None:
    hid = add_household(conn)
    aid = add_appliance(conn, hid)
    store.record_job_done_by_person(conn, hid, aid, JOB, "s1", on=T0.date(), at=T0)
    [d] = due(conn, hid, T0, loader)
    assert (d.last_done, d.last_done_source) == (T0.date(), "user")  # never "checked"
    assert [tuple(r) for r in conn.execute("SELECT kind, text FROM history")] == [("job_done", "confirmed by you")]
    assert not conn.execute("SELECT 1 FROM pilot_events WHERE kind = 'job_done_checked'").fetchall()

def test_overdue_reminder_and_dutch(conn: sqlite3.Connection, loader: Loader) -> None:
    hid = add_household(conn, lang="nl")
    aid = add_appliance(conn, hid, alias="wasmachine")
    store.set_last_done(conn, hid, aid, JOB, source="user", on=date(2026, 6, 1), at=T0)
    [d] = due(conn, hid, T0, loader)
    r = reminder(d, T0.date())
    assert r.title == "De pompfilter van je wasmachine is aan de beurt."
    assert r.body == "Aan de beurt sinds 1 sep. 10 minuten. Beginnen?" and r.footnote == "uit je handleiding, p. 3"
    assert schedule_line(d) == ("Wasmachine: pompfilter aan de beurt · elke 3 maanden · handleiding p. 3 ·"
                                " laatst gedaan: 1 jun.")


def test_future_last_done_refused(conn: sqlite3.Connection) -> None:
    hid = add_household(conn)
    aid = add_appliance(conn, hid)
    with pytest.raises(ValueError):
        store.set_last_done(conn, hid, aid, JOB, source="user", on=date(2026, 12, 1), at=T0)


def test_v9_storyboard_forms(conn: sqlite3.Connection, loader: Loader) -> None:
    hid = add_household(conn)
    aid = add_appliance(conn, hid)
    store.set_last_done(conn, hid, aid, JOB, source="never", on=None, at=T0)
    [d] = due(conn, hid, T0, loader)
    r = reminder(d, T0.date())
    # Storyboard 0:00, the hook; the page is the checked interval quote's (3 on the Acme card), never written in code.
    assert f"{r.title} {r.body}".endswith("is due. You said it has never been done. 10 minutes. Start?")
    assert r.payload()["body"] == f"{r.body}\nfrom your manual, p. 3"
    # The storyboard's 0:34 schedule line.
    assert schedule_line(d) == "Washer: debris filter due · every 3 months · manual p. 3 · last done: never."
    # The storyboard's 2:18, after the checked job.
    store.record_job_done_checked(conn, hid, aid, JOB, "s1", on=T0.date(), at=T0)
    [d] = due(conn, hid, T0, loader)
    assert next_line(d, T0.date()) == "Washer debris filter: next due in 13 weeks. I'll remind you."


def boiler_card() -> Card:
    from tests.l5.conftest import acme_card

    data = acme_card().model_dump(mode="json")
    data["quotes"].append({"id": "q_bleed", "text": "Bleeding the radiators lowers the system pressure.",
                           "lang": "en", "page": 6})
    pressure = dict(data["jobs"][0], id="check_pressure", title={"en": "Check the pressure"}, duration_min=None,
                    after=[{"job_id": "bleed_radiators", "because": {"en": "Bleeding drops the pressure"},
                            "quote_ref": "q_bleed"}])
    bleed = dict(data["jobs"][0], id="bleed_radiators", title={"en": "Bleed the radiators"}, interval=None)
    data["jobs"] = [pressure, bleed]
    return Card.model_validate(data)


def test_boiler_history_clause(conn: sqlite3.Connection) -> None:
    from app.schedule.remind import history_line

    hid = add_household(conn)
    aid = add_appliance(conn, hid, alias="boiler", card_path="cards/x/boiler.yaml")
    load = {"cards/x/boiler.yaml": boiler_card()}.get
    store.set_last_done(conn, hid, aid, "check_pressure", source="user", on=date(2026, 9, 1), at=T0)
    [d] = due(conn, hid, T0, load)
    assert history_line(d, T0.date()) == "Boiler: check the pressure."   # no history: the head alone
    # Saturday 24 Oct: the household's history says the radiators were bled.
    conn.execute("INSERT INTO history (household_id, appliance_id, kind, job_id, text, on_date, created_at)"
                 " VALUES (?, ?, 'voice_note', 'bleed_radiators', 'I bled two radiators', '2026-10-24', ?)",
                 (hid, aid, T0.isoformat()))
    [d] = due(conn, hid, T0, load)
    assert d.due_on == date(2026, 12, 1)  # the clause never moves the date
    assert history_line(d, T0.date()) == ("Boiler: check the pressure. You bled two radiators on Saturday."
                                          " Bleeding drops the pressure (boiler guide p. 6).")
    assert reminder(d, T0.date()).body.startswith("You bled two radiators on Saturday. Bleeding drops")
    # A bleed from before the last pressure check says nothing.
    store.set_last_done(conn, hid, aid, "check_pressure", source="user", on=date(2026, 10, 25), at=T0)
    [d] = due(conn, hid, T0, load)
    assert d.after == ()


def test_the_reminder_link_carries_its_landing(conn: sqlite3.Connection, loader: Loader) -> None:
    """The landing shows the appliance, why it is due and how long it takes, from the link alone."""
    from urllib.parse import parse_qs, urlsplit

    hid = add_household(conn, lang="nl")
    aid = add_appliance(conn, hid, alias="wasmachine")
    store.set_last_done(conn, hid, aid, JOB, source="user", on=date(2026, 6, 1), at=T0)
    [d] = due(conn, hid, T0, loader)
    r = reminder(d, T0.date())
    q = {k: v[0] for k, v in parse_qs(urlsplit(r.url).query).items()}
    assert q["appliance"] == aid and q["job"] == JOB and q["reminder"] == r.key
    assert q["alias"] == "wasmachine" and q["min"] == "10"
    assert q["why"] == r.why == "Laatst gedaan: 1 jun. Je handleiding zegt elke 3 maanden (p. 3)."
    assert r.payload()["why"] == r.why and r.payload()["minutes"] == 10
    hid2 = add_household(conn)
    aid2 = add_appliance(conn, hid2)
    store.set_last_done(conn, hid2, aid2, JOB, source="never", on=None, at=T0)
    [d2] = due(conn, hid2, T0, loader)
    assert reminder(d2, T0.date()).why == "Last done: never. Your manual says every 3 months (p. 3)."

import sqlite3
from pathlib import Path

import pytest

from eval.pilot import main, tally

ROOT = Path(__file__).resolve().parents[2]
SECRETS = ["hh-alpha-7f3", "hh-bravo-91c", "hh-own-001", "INV-ALPHA", "INV-BRAVO", "INV-OWN", "Keukenvaatwasser",
           "WashTwo", "SMV4HAX40N", "WW90T554DAW", "AEG-L7FE", "app-a1", "app-b1", "app-b2", "app-o1"]


@pytest.fixture()
def store(tmp_path: Path) -> Path:
    db = tmp_path / "store.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript((ROOT / "contracts" / "store.sql").read_text())
    t0 = "2026-10-27T10:00:00+00:00"
    hh = [("hh-alpha-7f3", "INV-ALPHA", 0), ("hh-bravo-91c", "INV-BRAVO", 0), ("hh-own-001", "INV-OWN", 1)]
    for h, inv, own in hh:
        conn.execute("INSERT INTO households (id, invite_code, is_own, consent_at, created_at) VALUES (?,?,?,?,?)",
                     (h, inv, own, t0, t0))
    apps = [("app-a1", "hh-alpha-7f3", "Keukenvaatwasser", "dishwasher", "Bosch", "SMV4HAX40N"),
            ("app-b1", "hh-bravo-91c", "WashTwo", "washer", "Samsung", "WW90T554DAW"),
            ("app-b2", "hh-bravo-91c", "dishwasher", "dishwasher", "bosch", "SMV4HAX40N"),
            ("app-o1", "hh-own-001", "washer", "washer", "AEG", "AEG-L7FE")]
    for a in apps:
        conn.execute("INSERT INTO appliances (id, household_id, alias, kind, brand, model, added_by, created_at) "
                     "VALUES (?,?,?,?,?,?,'typed',?)", (*a, t0))
    ev = [("hh-alpha-7f3", "app-a1", "filter", "reminder_sent", "2026-10-30T08:00:00+01:00"),
          ("hh-alpha-7f3", "app-a1", "filter", "reminder_sent", "2026-10-30T08:30:00+01:00"),  # same reminder, .ics
          ("hh-alpha-7f3", "app-a1", "filter", "reminder_opened", "2026-10-30T09:00:00+01:00"),
          ("hh-alpha-7f3", "app-a1", "filter", "job_done_checked", "2026-10-30T11:00:00+01:00"),
          ("hh-bravo-91c", "app-b1", "debris", "reminder_sent", "2026-10-31T08:00:00+01:00"),
          ("hh-bravo-91c", "app-b2", "filter", "reminder_sent", "2026-10-31T08:00:00+01:00"),
          ("hh-bravo-91c", "app-b2", "filter", "job_done_checked", "2026-11-01T08:00:00+01:00"),
          ("hh-bravo-91c", "app-b2", "filter", "reminder_sent", "2026-11-03T08:00:00+01:00"),  # after the freeze
          ("hh-own-001", "app-o1", "debris", "reminder_sent", "2026-10-30T08:00:00+01:00")]
    for h, a, j, k, at in ev:
        key = f"{a}:{j}:{at[:10] if a != 'app-a1' else '2026-10-30'}" if k in ("reminder_sent", "reminder_opened") \
            else None
        conn.execute("INSERT INTO pilot_events (household_id, appliance_id, job_id, kind, at, reminder_key) "
                     "VALUES (?,?,?,?,?,?)", (h, a, j, k, at, key))
    conn.commit()
    return db


def test_tally_counts(store: Path) -> None:
    conn = sqlite3.connect(store)
    t = tally(conn, own=False, since="2026-10-30T00:00:00+01:00", until="2026-11-02T12:00:00+01:00")
    assert (t.homes, t.appliances, t.reminders_sent, t.reminders_acted_on, t.jobs_done_checked) == (2, 3, 3, 2, 2)
    assert t.median_hours_to_done == 13.5  # 3 h and 24 h
    assert t.brands == {"bosch": 2, "other": 1}  # Samsung is in one home only
    own = tally(conn, own=True, since="2026-10-30T00:00:00+01:00", until="2026-11-02T12:00:00+01:00")
    assert (own.homes, own.reminders_sent, own.jobs_done_checked, own.median_hours_to_done) == (1, 1, 0, None)



def test_a_test_household_never_counts(store: Path) -> None:
    conn = sqlite3.connect(store)
    t0 = "2026-10-27T10:00:00+00:00"
    conn.execute("INSERT INTO households (id, invite_code, is_own, is_test, consent_at, created_at) "
                 "VALUES ('hh-test-1', 'INV-TEST', 0, 1, ?, ?)", (t0, t0))
    conn.execute("INSERT INTO appliances (id, household_id, alias, kind, brand, model, added_by, created_at) "
                 "VALUES ('app-t1', 'hh-test-1', 'w', 'washer', 'Bosch', 'X', 'typed', ?)", (t0,))
    conn.execute("INSERT INTO pilot_events (household_id, appliance_id, job_id, kind, at, reminder_key) "
                 "VALUES ('hh-test-1', 'app-t1', 'filter', 'reminder_sent', '2026-10-30T08:00:00+01:00', 'k')")
    for own, homes, sent in ((False, 2, 3), (True, 1, 1)):
        t = tally(conn, own=own, since="2026-10-30T00:00:00+01:00", until="2026-11-02T12:00:00+01:00")
        assert (t.homes, t.reminders_sent) == (homes, sent)

def test_a_judges_link_or_open_door_home_never_counts(store: Path) -> None:
    conn = sqlite3.connect(store)
    t0 = "2026-10-27T10:00:00+00:00"
    if "joined_via" in {r[1] for r in conn.execute("PRAGMA table_info(households)")}:
        conn.execute(  # the store already carries contracts 1.28 (store.sql migration 6)
            "INSERT INTO invite_links (id, code_sha256, kind, label, cap_usd_day_home, cap_usd_total, max_homes, "
            "expires_at, created_at) VALUES ('link-judges', 'x', 'judges', 'j', 1, 30, 10, ?, ?)", (t0, t0))
    else:
        conn.executescript(  # the household columns contracts 1.28 adds (store.sql migration 6)
            "CREATE TABLE invite_links (id TEXT PRIMARY KEY);"
            "ALTER TABLE households ADD COLUMN invite_link_id TEXT REFERENCES invite_links(id);"
            "ALTER TABLE households ADD COLUMN joined_via TEXT NOT NULL DEFAULT 'invite' CHECK (joined_via IN "
            "('invite', 'door', 'link') AND (joined_via = 'link') = (invite_link_id IS NOT NULL));"
            "INSERT INTO invite_links (id) VALUES ('link-judges');")
    for h, via, link in (("hh-judge-1", "link", "link-judges"), ("hh-door-1", "door", None)):
        conn.execute("INSERT INTO households (id, invite_code, is_own, consent_at, created_at, joined_via, "
                     "invite_link_id) VALUES (?, ?, 0, ?, ?, ?, ?)", (h, f"INV-{h}", t0, t0, via, link))
        conn.execute("INSERT INTO appliances (id, household_id, alias, kind, brand, model, added_by, created_at) "
                     "VALUES (?, ?, 'w', 'washer', 'Bosch', 'X', 'typed', ?)", (f"app-{h}", h, t0))
        conn.execute("INSERT INTO pilot_events (household_id, appliance_id, job_id, kind, at, reminder_key) "
                     "VALUES (?, ?, 'filter', 'reminder_sent', '2026-10-30T08:00:00+01:00', ?)", (h, f"app-{h}", h))
    t = tally(conn, own=False, since="2026-10-30T00:00:00+01:00", until="2026-11-02T12:00:00+01:00")
    assert (t.homes, t.appliances, t.reminders_sent) == (2, 3, 3)  # the two invited homes only, as before


@pytest.mark.parametrize("extra", [[], ["--claims", "pilot.r1"]])
def test_output_is_aggregates_only(store: Path, capsys: pytest.CaptureFixture[str], extra: list[str]) -> None:
    assert main(["--db", str(store), "--until", "2026-11-02T12:00:00+01:00", *extra]) == 0
    out = capsys.readouterr().out
    assert out.strip()
    for s in SECRETS:
        assert s.lower() not in out.lower(), s
    assert "samsung" not in out.lower()


def test_missing_store_fails(tmp_path: Path) -> None:
    assert main(["--db", str(tmp_path / "none.sqlite")]) == 1


def test_the_pilot_homes_are_the_stores_view_and_agree_with_the_query(store: Path) -> None:
    from eval.pilot import cohort

    conn = sqlite3.connect(store)
    t0 = "2026-10-27T10:00:00+00:00"
    conn.execute("INSERT INTO households (id, invite_code, is_own, is_test, consent_at, created_at) "
                 "VALUES ('hh-test-1', 'INV-T', 0, 1, ?, ?)", (t0, t0))
    conn.execute("INSERT INTO households (id, invite_code, is_own, created_at) VALUES ('hh-noconsent', 'INV-N', 0, ?)",
                 (t0,))
    assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'pilot_cohort'").fetchone()  # store.sql has it
    via_view = sorted(cohort(conn, own=False))
    conn.execute("DROP VIEW pilot_cohort")  # a store from before the view
    assert via_view == sorted(cohort(conn, own=False)) == ["hh-alpha-7f3", "hh-bravo-91c"]
    assert cohort(conn, own=True) == ["hh-own-001"]

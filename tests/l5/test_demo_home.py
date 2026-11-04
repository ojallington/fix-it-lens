"""deploy/demo_home.py: a test household named Demo home, its appliances added by model code from cards already
built, a lived-in schedule written through the schedule's own writes, its tokens in 0600 files never shown, idempotent,
a dry run that writes nothing, and nothing in it that could name a real home."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from datetime import timedelta
from pathlib import Path

import pytest

from app import config
from app.schedule.due import due
from app.server import checked_card_loader
from deploy import demo_home
from deploy.demo_home import JobPlan
from tests.l5.conftest import T0

BOSCH = (("dishwasher", "bosch", "SMV4HAX40N"),)
ADMIN = "Sam"  # the --admin name
JOB = "machine_cleaning"  # every 2 months


@pytest.fixture(autouse=True)
def private(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "private").mkdir()
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "private"))
    return tmp_path / "private"


def run(conn: sqlite3.Connection, secret: bytes, out: Path, *jobs: JobPlan, dry_run: bool = False,
        appliances: tuple[tuple[str, str, str], ...] = BOSCH) -> tuple[list[str], list[str]]:
    said: list[str] = []
    done = demo_home.seed(conn, secret, out, now=T0, dry_run=dry_run, load_card=checked_card_loader("nl"),
                          admin=ADMIN, say=said.append, appliances=appliances,
                          jobs=jobs or (JobPlan("dishwasher", JOB, 75),))
    return done, said


def home(conn: sqlite3.Connection) -> str:
    hid = demo_home.find_home(conn)
    assert hid is not None
    return hid


def test_it_makes_a_test_home_with_a_checked_card_and_an_overdue_job(conn: sqlite3.Connection, secret: bytes,
                                                                    private: Path) -> None:
    run(conn, secret, private / "demo")
    hid = home(conn)
    hh = conn.execute("SELECT * FROM households WHERE id = ?", (hid,)).fetchone()
    assert hh["is_test"] == 1 and hh["is_own"] == 0 and hh["name"] == "Demo home" and hh["consent_at"]
    assert [r["name"] for r in conn.execute("SELECT name FROM people WHERE household_id = ?", (hid,))] == [ADMIN]
    a = conn.execute("SELECT a.alias, a.model, a.added_by, c.status FROM appliances a JOIN cards_ref c"
                     " ON c.appliance_id = a.id WHERE a.household_id = ?", (hid,)).fetchone()
    assert tuple(a) == ("dishwasher", "SMV4HAX40N", "typed", "checked")
    [d] = due(conn, hid, T0, checked_card_loader("nl"))
    assert d.status == "overdue" and d.last_done == T0.date() - timedelta(days=75) and d.last_done_source == "user"
    [change] = conn.execute("SELECT kind, by_kind, by_label FROM job_change WHERE household_id = ?", (hid,))
    assert tuple(change) == ("done", "app", ADMIN)


def test_a_window_and_a_snooze_go_in_through_the_schedule(conn: sqlite3.Connection, secret: bytes,
                                                          private: Path) -> None:
    run(conn, secret, private / "demo", JobPlan("dishwasher", JOB, 70, window=(2, 4), snooze_days=5))
    hid = home(conn)
    [d] = due(conn, hid, T0, checked_card_loader("nl"))
    assert d.schedule is not None and (d.schedule.kind, d.schedule.min, d.schedule.max) == ("window", 2, 4)
    assert d.snoozed_until == d.due_on == T0.date() + timedelta(days=5)
    kinds = [r[0] for r in conn.execute("SELECT kind FROM job_change WHERE household_id = ? ORDER BY id", (hid,))]
    assert kinds == ["done", "schedule", "snooze"]


def test_a_second_run_changes_nothing(conn: sqlite3.Connection, secret: bytes, private: Path) -> None:
    run(conn, secret, private / "demo")
    before = conn.execute("SELECT count(*) FROM job_change").fetchone()[0], \
        conn.execute("SELECT count(*) FROM household_tokens").fetchone()[0]
    done, said = run(conn, secret, private / "demo")
    assert done == [] and said == ["Demo home is complete; nothing to do"]
    assert (conn.execute("SELECT count(*) FROM job_change").fetchone()[0],
            conn.execute("SELECT count(*) FROM household_tokens").fetchone()[0]) == before
    assert conn.execute("SELECT count(*) FROM households WHERE name = 'Demo home'").fetchone()[0] == 1


def test_a_dry_run_writes_nothing(conn: sqlite3.Connection, secret: bytes, private: Path) -> None:
    done, said = run(conn, secret, private / "demo", dry_run=True)
    assert done and all(s.startswith("would: ") for s in said)
    assert conn.execute("SELECT count(*) FROM households").fetchone()[0] == 0
    assert not (private / "demo").exists()


def test_its_tokens_are_0600_files_and_never_shown(conn: sqlite3.Connection, secret: bytes, private: Path,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    _, said = run(conn, secret, private / "demo")
    files = sorted(p.name for p in (private / "demo").iterdir())
    assert files == ["agent.token", "feed.path", "ha.token"]
    for p in (private / "demo").iterdir():
        assert p.stat().st_mode & 0o777 == 0o600
        assert p.read_text().strip() not in "\n".join(said) + capsys.readouterr().out
    assert (private / "demo").stat().st_mode & 0o077 == 0
    kinds = {(r["by_kind"], r["scope"], r["label"]) for r in conn.execute("SELECT * FROM household_tokens"
                                                                         " WHERE by_kind != 'app'")}
    assert kinds == {("ha", "write", "Home Assistant"), ("agent", "write", "my agent")}


def test_nothing_in_it_could_name_a_real_home(conn: sqlite3.Connection, secret: bytes, private: Path) -> None:
    run(conn, secret, private / "demo")
    hid = home(conn)
    text: list[str] = []
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall():
        cols = [c[1] for c in conn.execute(f"PRAGMA table_info({table})")]
        if "household_id" in cols or table == "households":
            key = "id" if table == "households" else "household_id"
            for row in conn.execute(f"SELECT * FROM {table} WHERE {key} = ?", (hid,)):  # noqa: S608 (table names)
                text += [str(v) for v in row if isinstance(v, str)]
    blob = "\n".join(text)
    assert not re.search(r"(?i)\b(street|straat|laan|weg|plein|road|avenue)\b", blob)   # no address
    assert not re.search(r"\b\d{4}\s?[A-Z]{2}\b", blob)                                 # no Dutch postcode
    assert not re.search(r"(?i)\b(fd|serial|s/n|e-nr)\b", blob)                          # no serial on a plate
    names = {r["name"] for r in conn.execute("SELECT name FROM people WHERE household_id = ?", (hid,))}
    assert names == {ADMIN}
    assert conn.execute("SELECT count(*) FROM appliances WHERE household_id = ? AND added_by != 'typed'",
                        (hid,)).fetchone()[0] == 0  # typed model codes: no plate photo was ever taken
    assert demo_home.NAME == "Demo home"


def test_a_card_that_is_not_built_stops_it_before_any_write(conn: sqlite3.Connection, secret: bytes,
                                                            private: Path) -> None:
    with pytest.raises(SystemExit, match="no card for bosch NOPE123"):
        run(conn, secret, private / "demo", appliances=(*BOSCH, ("oven", "bosch", "NOPE123")))
    assert conn.execute("SELECT count(*) FROM households").fetchone()[0] == 0


def test_a_job_not_on_the_schedule_writes_no_date(conn: sqlite3.Connection, secret: bytes, private: Path) -> None:
    with pytest.raises(SystemExit, match="not on the schedule, so no date was written: dishwasher descale"):
        run(conn, secret, private / "demo", JobPlan("dishwasher", JOB, 75), JobPlan("dishwasher", "descale", 10))
    assert conn.execute("SELECT count(*) FROM job_change").fetchone()[0] == 0


def test_the_planned_home_has_each_case_the_film_needs() -> None:
    shows = [c for j in demo_home.JOBS for c in j.shows]
    assert sorted(shows) == sorted(demo_home.CASES)  # each case once
    assert {j.alias for j in demo_home.JOBS} <= {a for a, _, _ in demo_home.HOME}


# ---- which cards: only those read usable, and only while unchanged ------------------------------------------------

REPO_CARD = "cards/bosch/SMV4HAX40N.yaml"


def verdict(private: Path, card: str, verdict: str, sha: str | None = None, run: str = "rerun") -> None:
    f = private / card if (private / card).exists() else config.ROOT / card
    line = {"run": run, "brand": "x", "model": "x", "card": card, "checked_by_code": True,
            "card_sha256": sha or hashlib.sha256(f.read_bytes()).hexdigest(), "verdict": verdict,
            "verdict_by": "builder", "at": "2026-10-27"}
    path = private / demo_home.VERDICTS
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as out:
        out.write(json.dumps(line) + "\n")


def a_private_card(private: Path, brand: str, model: str) -> str:
    from ingest.flow import card_path

    dest = card_path(brand, model, private / "cards")
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(config.ROOT / REPO_CARD, dest)
    return str(dest.relative_to(private))


def test_no_verdicts_means_no_appliance_and_says_which(private: Path) -> None:
    appliances, jobs, notes = demo_home.plan(private)
    assert appliances == () and jobs == ()
    assert "left out the dishwasher: its card (bosch SMV4HAX40N) is not read usable yet" in notes


def test_a_usable_card_is_used_and_the_missing_cases_are_named(private: Path) -> None:
    verdict(private, REPO_CARD, "usable", run="curated")
    appliances, jobs, notes = demo_home.plan(private)
    assert appliances == (("dishwasher", "bosch", "SMV4HAX40N"),)
    assert [j.job_id for j in jobs] == ["machine_cleaning"]
    assert {n for n in notes if n.startswith("no ")} == {
        f"no {c} job: the chosen cards cannot show one"
        for c in ("due soon", "window", "snoozed", "done recently", "own interval")}


def test_only_the_latest_verdict_counts_and_only_for_the_same_bytes(private: Path) -> None:
    verdict(private, REPO_CARD, "usable")
    verdict(private, REPO_CARD, "partly")
    assert demo_home.usable_cards(private) == {}
    verdict(private, REPO_CARD, "usable", sha="0" * 64)  # read before the card was rebuilt
    assert demo_home.usable_cards(private) == {}
    verdict(private, REPO_CARD, "usable")
    assert demo_home.usable_cards(private) == {REPO_CARD: "rerun"}


def test_the_fridge_waits_for_its_reread(private: Path) -> None:
    card = a_private_card(private, "electrolux", "KNA6LE18S")
    verdict(private, card, "usable", run="baseline")
    appliances, _, notes = demo_home.plan(private)
    assert appliances == () and "left out the fridge: its card (electrolux KNA6LE18S) waits for its reread" in notes
    verdict(private, card, "usable", run="rerun")
    assert demo_home.plan(private)[0] == (("fridge", "electrolux", "KNA6LE18S"),)


def test_only_the_homes_own_appliances() -> None:
    assert [(a, b) for a, b, _ in demo_home.HOME] == [
        ("dishwasher", "bosch"), ("washer", "aeg"), ("boiler", "intergas"), ("fridge", "electrolux"), ("oven", "aeg")]


def test_a_job_the_manual_gives_no_interval_gets_the_admins_own(conn: sqlite3.Connection, secret: bytes,
                                                                 private: Path) -> None:
    """The boiler's radiators: every year in September, the admin's choice, set before its done date, and shown as
    theirs (no manual line, no page)."""
    own = JobPlan("dishwasher", "clean_filters", 0, own_every=(1, "year"), done_month=9)
    done, _ = run(conn, secret, private / "demo", JobPlan("dishwasher", JOB, 75), own)
    assert any("Sam's own interval: every 1 year (the manual gives none)" in d for d in done)
    [d] = [d for d in due(conn, home(conn), T0, checked_card_loader("nl")) if d.job_id == "clean_filters"]
    assert d.last_done is not None and d.last_done.isoformat() == "2026-09-15"  # T0 is 27 Oct 2026
    assert d.due_on is not None and d.due_on.isoformat() == "2027-09-15" and d.status == "upcoming"
    assert d.api()["manual"] is None and d.api()["schedule"] == {"kind": "every", "every": 1, "unit": "year"}
    kinds = [r["kind"] for r in conn.execute(
        "SELECT kind FROM job_change WHERE job_id = 'clean_filters' ORDER BY id")]
    assert kinds == ["schedule", "done"]  # by the admin, the schedule first


def test_an_own_interval_is_only_for_a_job_the_manual_gives_none(conn: sqlite3.Connection, secret: bytes,
                                                                  private: Path) -> None:
    with pytest.raises(SystemExit, match="dishwasher machine_cleaning \\(the manual gives it an interval"):
        run(conn, secret, private / "demo", JobPlan("dishwasher", JOB, 75, own_every=(1, "year")))
    assert conn.execute("SELECT count(*) FROM job_change").fetchone()[0] == 0

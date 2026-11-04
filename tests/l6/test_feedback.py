"""Feedback after a session: the summary prints totals and only the words a household agreed to have quoted."""

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from eval import feedback as fb

ROOT = Path(__file__).resolve().parents[2]


def rec(hh: str, at: str, quote: str = "none", name: str | None = None, withdrawn: str | None = None,
        **answers: Any) -> dict[str, Any]:
    def iso(t: str | None) -> str | None:  # the contract's date-time: seconds and an offset
        return t and f"{t}:00+01:00"

    key = hashlib.sha256(f"{hh}{at}".encode()).hexdigest()[:12]
    return fb.check({"id": f"fb-{key}", "household": hh, "at": iso(at), "source": "app", "lang": "nl",
                     "form_version": 1, "answers": {k: answers.get(k) for k in (*fb.CHOICES, *fb.WRITTEN)},
                     "quote": quote, "quote_name": name, "withdrawn_at": iso(withdrawn)})


RECORDS = [
    rec("hh-a", "2026-10-31T10:00", would_use="maybe", friend="first try"),  # superseded by hh-a's later answer
    rec("hh-a", "2026-11-01T10:00", "named", "Anna", would_use="yes", practical="yes", unsafe="no",
        friend="  Het werkte, maar de camera was traag.  "),
    rec("hh-b", "2026-11-01T11:00", "anonymous", would_use="no", practical="partly", unsafe="yes",
        unsafe_which="De stap bij de pomp voelde niet goed.", hard="Te veel uitleg."),
    rec("hh-c", "2026-11-01T12:00", "none", would_use="yes", practical="no", hard="Private words, never printed."),
    rec("hh-d", "2026-11-01T13:00", "anonymous", withdrawn="2026-11-02T09:00", would_use="maybe",
        friend="Withdrawn words."),
    rec("hh-out", "2026-11-01T14:00", "anonymous", would_use="yes", friend="A door home, not in the cohort."),
]
COHORT = {"hh-a", "hh-b", "hh-c", "hh-d"}


def test_totals_count_each_cohort_household_once_by_its_latest_answer() -> None:
    s = fb.summary(RECORDS, COHORT)
    assert s["n"] == 4
    assert s["would_use"] == {"yes": 2, "maybe": 1, "no": 1, "no_answer": 0}
    assert s["practical"] == {"yes": 1, "partly": 1, "no": 1, "no_answer": 1}
    assert s["unsafe"] == {"no": 1, "yes": 1, "no_answer": 2}


def test_quotes_are_word_for_word_critical_ones_kept_and_only_with_a_tick() -> None:
    s = fb.summary(RECORDS, COHORT)
    assert s["quotes"] == [
        {"question": "friend", "lang": "nl", "text": "Het werkte, maar de camera was traag.", "by": "Anna"},
        {"question": "hard", "lang": "nl", "text": "Te veel uitleg.", "by": None},
        {"question": "unsafe_which", "lang": "nl", "text": "De stap bij de pomp voelde niet goed.", "by": None},
    ]
    printed = repr(s)
    for never in ("Private words", "Withdrawn words", "first try", "A door home", "hh-"):
        assert never not in printed  # not quoted, withdrawn, superseded, outside the cohort, or a household id
    assert s["quoted_households"] == 2


def test_a_bad_record_is_refused() -> None:
    with pytest.raises(fb.FeedbackError):
        rec("hh-x", "2026-11-01T10:00", would_use="sure")
    with pytest.raises(fb.FeedbackError):
        rec("hh-x", "2026-11-01T10:00", "named")  # named with no name
    partial = {**rec("hh-x", "2026-11-01T10:00"), "answers": {"would_use": "yes"}}
    with pytest.raises(fb.FeedbackError):
        fb.check(partial)  # the contract wants all eight answers, null when unanswered


def test_a_filled_pdf_becomes_a_record_and_two_ticks_are_no_answer() -> None:
    fields = {"would_use_1": "/Yes", "practical_2": "/Yes", "practical_3": "/Yes", "unsafe_1": "/Off",
              "friend": "Zeker doen.", "hard": "  ", "quote_3": "/Yes", "quote_as": "Bram"}
    r = fb.from_fields(fields, "hh-pdf", "nl")
    assert r["answers"]["would_use"] == "yes" and r["answers"]["practical"] is None and r["answers"]["unsafe"] is None
    assert r["answers"]["friend"] == "Zeker doen." and r["answers"]["hard"] is None
    assert (r["quote"], r["quote_name"], r["source"]) == ("named", "Bram", "pdf")
    assert fb.from_fields({"quote_3": "/Yes"}, "hh-pdf", None)["quote"] == "none"  # "quote me as" with no name
    assert fb.from_fields({"quote_1": "/Yes", "quote_2": "/Yes"}, "hh-pdf", None)["quote"] == "none"


def test_the_form_asks_exactly_what_the_record_holds() -> None:
    spec = importlib.util.spec_from_file_location("consent_pdf_fb", ROOT / "eval" / "consent" / "pdf" / "build.py")
    assert spec and spec.loader
    pdf = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = pdf
    spec.loader.exec_module(pdf)
    form = pdf.parse("feedback")
    boxes = {c.key: [en.lower() for _, en in c.options] for c in form.choices if not c.open}
    assert set(boxes) == set(fb.CHOICES) | {"quote"}
    for k, vals in fb.CHOICES.items():
        assert [o.split()[0] for o in boxes[k]] == list(vals), k  # the boxes, in order, are the record's values
    assert [o.split()[0] for o in boxes["quote"]] == ["do", "quote", "quote"] and len(fb.QUOTE) == 3
    assert [c.key for c in form.choices if c.open] == list(fb.WRITTEN)


def test_a_withdrawn_named_quote_loses_its_name_and_long_text_is_refused() -> None:
    gone = rec("hh-w", "2026-11-01T10:00", "named", None, withdrawn="2026-11-02T10:00", friend="Weg.")
    assert fb.summary([gone], {"hh-w"})["quotes"] == []
    with pytest.raises(fb.FeedbackError):
        rec("hh-x", "2026-11-01T10:00", hard="x" * (fb.MAX_TEXT + 1))
    assert rec("hh-x", "2026-11-01T10:00", hard="x" * fb.MAX_TEXT)["answers"]["hard"]


def test_a_summary_states_its_time_and_is_refused_once_a_home_is_deleted(tmp_path: Path,
                                                                        monkeypatch: pytest.MonkeyPatch,
                                                                        capsys: pytest.CaptureFixture[str]) -> None:
    import json
    import sqlite3

    db = tmp_path / "store.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript((ROOT / "contracts" / "store.sql").read_text())
        for h in ("hh-a", "hh-b"):
            conn.execute("INSERT INTO households (id, invite_code, is_own, consent_at, created_at) "
                         "VALUES (?, ?, 0, '2026-10-30', '2026-10-30')", (h, f"INV-{h}"))
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path))
    lines = tmp_path / "feedback" / "feedback.jsonl"
    lines.parent.mkdir()
    lines.write_text("".join(json.dumps(r) + "\n" for r in RECORDS[:3]))
    made = tmp_path / "summary.json"
    assert fb.main(["summary", "--db", str(db), "--out", str(made)]) == 0
    out = json.loads(made.read_text())
    assert out["generated_at"] and out["n"] == 2 and len(out["fingerprint"]) == 64
    assert "Te veel uitleg." in made.read_text()
    assert fb.main(["fresh", str(made), "--db", str(db)]) == 0

    with sqlite3.connect(db) as conn:  # the app's DELETE /household removes the row
        conn.execute("DELETE FROM households WHERE id = 'hh-b'")
    assert fb.main(["fresh", str(made), "--db", str(db)]) == 1
    assert "stale" in capsys.readouterr().err
    assert fb.main(["summary", "--db", str(db), "--out", str(made)]) == 0
    assert fb.main(["fresh", str(made), "--db", str(db)]) == 0
    assert "Te veel uitleg." not in made.read_text()  # hh-b's quote is gone from the new summary

    with lines.open("a") as f:  # a withdrawal is a new line too
        t = "2026-11-02T10:00:00+01:00"
        f.write(json.dumps({**RECORDS[1], "at": t, "withdrawn_at": t}) + "\n")
    assert fb.main(["fresh", str(made), "--db", str(db)]) == 1


def test_a_pdf_is_imported_through_the_app_module_and_only_for_a_pilot_home(tmp_path: Path) -> None:
    import json
    import sqlite3

    db = tmp_path / "store.sqlite"
    with sqlite3.connect(db) as conn:
        conn.executescript((ROOT / "contracts" / "store.sql").read_text())
        conn.execute("INSERT INTO households (id, invite_code, is_own, consent_at, created_at) "
                     "VALUES ('hh-a', 'INV-a', 0, '2026-10-30', '2026-10-30')")
    r = fb.from_fields({"would_use_1": "/Yes", "hard": "Het deksel."}, "hh-a", "nl")
    assert r["source"] == "pdf" and r["answers"]["hard"] == "Het deksel."
    fb.import_pdf(tmp_path, r, db)
    path = tmp_path / "feedback" / "feedback.jsonl"
    assert [json.loads(x)["id"] for x in path.read_text().splitlines()] == [r["id"]]
    assert oct(path.stat().st_mode & 0o777) == "0o600"

    with sqlite3.connect(db) as conn:  # deleted while the form was being read: nothing is written
        conn.execute("DELETE FROM households WHERE id = 'hh-a'")
    with pytest.raises(fb.FeedbackError, match="not a pilot household"):
        fb.import_pdf(tmp_path, fb.from_fields({}, "hh-a", None), db)
    assert len(path.read_text().splitlines()) == 1

import json
from datetime import datetime
from pathlib import Path

from eval import redteam


def test_the_spend_counts_only_this_runs_sessions(tmp_path: Path) -> None:
    led = tmp_path / "ledger.jsonl"
    led.write_text("".join(json.dumps(r) + "\n" for r in [
        {"session_id": "redteam-15-rt_001", "usd": 0.06}, {"session_id": "redteam-15-rt_002", "usd": 0.01},
        {"session_id": "golden-m1", "usd": 9.0}, {"session_id": "redteam-14-rt_001", "usd": 1.0}]))
    assert round(redteam.spent(led), 4) == 0.07


def test_the_run_pauses_at_its_hour_until_told_to_resume(tmp_path: Path) -> None:
    assert not redteam.paused(tmp_path, datetime(2026, 10, 29, 17, 54))
    assert redteam.paused(tmp_path, datetime(2026, 10, 29, 17, 55))
    (tmp_path / "redteam").mkdir()
    (tmp_path / "redteam" / "resume").touch()
    assert not redteam.paused(tmp_path, datetime(2026, 10, 29, 18, 30))


def test_raw_sentences_are_numbered_per_session_and_marked_when_they_passed_live() -> None:
    raw = [{"id": "rt.007", "lang": "en", "aim": "mains.casing", "turns": [
        {"user": "a", "raw": ["Unplug it first. Then take off the back panel."], "said": ["Unplug it first."]},
        {"user": "b", "raw": ["Call an engineer."], "said": ["Call an engineer."]}]}]
    s = redteam.sentences(raw)
    assert [x["id"] for x in s] == ["rt.007.1", "rt.007.2", "rt.007.3"]
    assert [x["passed_live"] for x in s] == [True, False, True]


def test_claims_count_a_false_strike_only_on_a_sentence_not_labelled_unsafe() -> None:
    sents = [{"id": f"rt.001.{k}", "lang": "en" if k < 3 else "nl", "held": k in (1, 3), "rules": "t"}
             for k in range(1, 5)]
    sessions = [{"id": "rt.001", "stopped": True}, {"id": "rt.002", "stopped": False}]
    c = {x["id"]: x for x in redteam.claims(sents, sessions, [{"id": "rt.001.1"}])}
    assert (c["redteam.unsafe"]["value"], c["redteam.unsafe"]["n"]) == (1, 4)
    assert (c["redteam.safe.wrongly_struck"]["value"], c["redteam.safe.wrongly_struck"]["n"]) == (1, 3)
    assert "en 0/1, nl 1/2" in c["redteam.safe.wrongly_struck"]["note"]
    assert c["redteam.hazard_stops"]["value"] == 1 and c["redteam.sessions"]["value"] == 2


def test_the_committed_results_hold_no_words() -> None:
    for f in (redteam.SENTENCES, redteam.SESSIONS):
        for r in map(json.loads, f.read_text().splitlines()):
            assert set(r) <= {"id", "lang", "held", "rules", "stopped"}

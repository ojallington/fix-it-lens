"""M1, the first end-to-end milestone, checked on a recorded session: the Bosch dishwasher's filter job, from the card.

Two recordings are checked:
- the live M1 session, when `FIXIT_M1_SESSION` names it (it lives in FIXIT_PRIVATE/sessions, never in the repo; its
  model calls must be in the replay fixtures);
- a synthetic one made here with a taping fake client, so CI runs every check on the same code path.

What each item of "M1, exactly" becomes here (items 1, 2 and 6's live firing are proved by the schedule, ingest and
phone tests; this file checks what the session record shows of them):
1. the session opened the card's job (the reminder's notification opens /ws with the job);
2. the card is the Bosch card, and it passes the loader (rules and citations) when the manual is there;
3. the filter is outlined by SAM (a mask, not the box fallback);
4. "checking" goes out before each verdict, and the filter step ends `done` on the judge plus the SAM witness;
5. every sentence that went out passes G0 again now, and nothing held carries its words;
6. a G1 event reached the session (live recording only);
7. `?replay=<id>` plays the recording with the REPLAY badge first, and a re-run makes the same s2c with no new calls
   (order compared within each stream: the stop and step path strictly, the guide's sentences among themselves);
8. every model call the session made is in the record (the ledger's lines are checked by the ledger tests).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import calls
import rules
from app import cards, config, recorder
from app.ports import IngestManuals
from app.rerun import rerun
from app.server import create_app
from app.session import Session
from contracts.models.card import Card
from tests.l1.test_rerun import Tape, frame_msg
from tests.l1.test_session import tap
from tests.l1.test_stepcheck import Judge

ROOT = Path(__file__).resolve().parents[2]
CARD = "cards/bosch/SMV4HAX40N.yaml"
JOB = "clean_filters"
PART = "filter_system"
LIFT = "remove_filter_system"  # "the user lifts the filter out"


def bosch() -> Card:
    return cards.read(ROOT / CARD)


async def synthetic(root: Path) -> tuple[recorder.Recording, Tape]:
    """The M1 run against fakes: check the filters, lift the system out (SAM's track loses it), done."""
    inner = Judge([300, 300])
    tape = Tape(inner)
    s = Session(session_id="m1synthetic", client=tape, card=bosch(), card_path=CARD, job_id=JOB,  # type: ignore[arg-type]
                country="nl", lang="en", record_root=root)
    s.start()
    s.rec.client(0, "c2s", {"type": "hello", "seq": 0, "t_ms": 0, "session_id": None, "last_seq": None,
                            "client": "phone", "lang": "en", "job_id": JOB})
    await s.emit({"type": "hello_ok", "session_id": s.session_id, "resumed": False, "mode": "api"})
    await s.greet()
    await s.start_job()
    seq, t = 1, 100.0
    for i in range(2):
        if i == 1:
            inner.video = calls.SamResult(1000, 1000, [], "", 5.0, 0.01, "0" * 64)  # the filter is out of its place
        for _ in range(12):
            m, j = frame_msg(seq, t, 60 + seq % 100)
            await s.handle(m, jpeg=j)
            await s.idle()
            seq, t = seq + 1, t + 100
        await s.handle(tap("done", seq))
        await s.idle()
        seq += 1
    await s.close()
    s.rec.end("abandoned")
    return recorder.load(root, s.session_id), tape


def live_m1() -> recorder.Recording | None:
    sid = os.environ.get("FIXIT_M1_SESSION")
    if not sid:
        return None
    return recorder.load(config.sessions_dir(), sid)


# ---- the checks, on any M1 recording -------------------------------------------------------------------------------


def check_record(rec: recorder.Recording, *, live: bool) -> None:
    s2c = rec.s2c()
    meta = rec.meta
    # 1, 2: the card's job, on the Bosch card
    assert meta.card == CARD and meta.job_id == JOB and meta.mode == "api"
    card = bosch()
    # 3: the filter outlined by SAM: the record's segment trace says sam (the rail itself is plain words; a record
    # made before that said it on the rail), and the mask is a mask, not a rectangle
    assert any(x["dir"] == "state" and x["kind"] == "trace" and x["tool"] == "segment" and x.get("part_id") == PART
               and str(x.get("source", "")).startswith("sam") for x in rec.lines) \
        or any(m["type"] == "rail" and m["tool"] == "segment" and m["args_short"] == PART
               and m["result_short"].startswith("sam") for m in s2c)
    outlines = [m for m in s2c if m["type"] == "outline" and m["part_id"] == PART]
    assert outlines and all(m["mask_svg"].startswith("M") for m in outlines)
    # 4: "checking" at once, before its verdict; the lift step ends done on both witnesses
    for i, m in enumerate(s2c):
        if m["type"] == "verdict" and not m["verdict"]["witnesses"].get("by_person"):  # the person's word: no check
            k = m["verdict"]["check_id"]
            assert any(x["type"] == "checking" and x["check_id"] == k for x in s2c[:i]), k
    lifted = [m["verdict"] for m in s2c if m["type"] == "verdict" and m["verdict"]["step_id"] == LIFT]
    assert lifted and lifted[-1]["result"] == "done"
    assert lifted[-1]["witnesses"]["judge"] == "done" and lifted[-1]["witnesses"]["sam"] in ("video", "frame")
    assert all(v["result"] in ("cant_see", "not_done") for v in lifted[:-1])
    # 5: every sentence that left passes G0 now; a held message carries the rule's line, never the words
    for m in s2c:
        if m["type"] == "sentence":
            assert isinstance(rules.check_sentence(m["text"], meta.lang, card, None, country=meta.country or "nl"),
                              rules.Pass), m["text"]
        if m["type"] == "held":
            assert set(m) <= {"type", "seq", "t_ms", "sentence_id", "rule_id", "line", "words"}  # words is a count
    # 6: G1 reached the session (the phone fires it; the synthetic run has no phone)
    if live:
        assert any(x["dir"] == "c2s" and x["kind"] == "g1" for x in rec.lines)
    # 8: every call is in the record, with its outcome
    calls_ = [x for x in rec.lines if x["dir"] == "state" and x["kind"] == "call"]
    assert {c["role"] for c in calls_} >= {"sam", "judge"} and all("status" in c for c in calls_)


def check_replay_badge(rec: recorder.Recording, root: Path) -> None:
    client = calls.make_client("replay", fixtures_root=root / "fx", private=None)
    tc = TestClient(create_app(client=client, record_root=root / "unused", replay_root=rec.dir.parent))
    with tc.websocket_connect(f"/ws?replay={rec.meta.session_id}&speed=1000") as sock:
        sock.send_text(json.dumps({"type": "hello", "seq": 0, "t_ms": 0, "session_id": None, "last_seq": None,
                                   "client": "phone", "lang": "en", "replay_id": rec.meta.session_id}))
        first = sock.receive_json()
        assert first["type"] == "replay" and first["badge"] is True
        sent = [m for m in rec.s2c() if m["type"] != "hello_ok"]
        got = [sock.receive_json() for _ in sent]
    assert [m["type"] for m in got] == [m["type"] for m in sent]


# ---- the tests -----------------------------------------------------------------------------------------------------


@pytest.mark.usefixtures("ffmpeg")
async def test_m1_synthetic(tmp_path: Path) -> None:
    rec, tape = await synthetic(tmp_path / "sessions")
    check_record(rec, live=False)
    check_replay_badge(rec, tmp_path)
    replay = Tape(None, tape.tape)
    r = await rerun(rec, replay, card=bosch(), guide=False)
    assert replay.missing == [] and r.same, r.diff()[:3]


@pytest.mark.skipif(not os.environ.get("FIXIT_M1_SESSION"), reason="FIXIT_M1_SESSION names the live M1 recording")
async def test_m1_live_recording(tmp_path: Path) -> None:
    rec = live_m1()
    assert rec is not None
    check_record(rec, live=True)
    check_replay_badge(rec, tmp_path)
    kept = config.served_fixtures()  # the served app records into FIXIT_PRIVATE/fixtures
    client = calls.make_client("replay", fixture_roots=[calls.FIXTURES] + ([kept] if kept else []))
    # fixtures only: a request the live run did not make raises MissingFixture
    r = await rerun(rec, client, guide=True, manuals=IngestManuals())  # the guide's requests carry the manual
    assert r.same, r.diff()[:3]

"""The job's opening, said and shown right after job_preview: the job's title, its steps and minutes, what to get, then
read the steps and tap the ready button, with the AI disclosure as its last line, not its first. The card's lines are
said whole and on their own; a held one is left out. A recording made before the opening re-runs as it was made."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app import rerun
from app.server import make_session
from app.session import HELD_STEP, OPENING, Session
from contracts.models import ws
from rules import disclaimer
from tests.l1.helpers import FakeClient
from tests.l1.test_preview import card, live, sent
from tests.l1.test_session import HELD, PLUG_OUT, session

SPOKEN = disclaimer("spoken", "en")


def opened(tmp_path: Path, c: Any = None) -> Session:
    s = live(tmp_path, c)
    s.opening = True
    return s


async def begin(s: Session) -> list[str]:
    s.start()
    await s.greet()
    await s.start_job()
    return [m["text"] for m in sent(s, "sentence")]


async def test_the_opening_says_what_comes_and_ends_with_the_disclosure(tmp_path: Path) -> None:
    s = opened(tmp_path)
    said = await begin(s)
    assert said == ["Clean the debris filter.", "4 steps, about 10 minutes.", "First get these:",
                    "A towel for the water.", "A shallow tray.", OPENING["en"]["read"], SPOKEN]
    kinds = [m["type"] for m in s.out if m["type"] in ("job_preview", "sentence")]  # type: ignore[attr-defined]
    assert kinds[0] == "job_preview"  # the steps are on screen before the words that point at them
    assert not sent(s, "held") and all("step_id" not in m for m in sent(s, "sentence"))
    assert [x["job_id"] for x in s.rec.lines if x["dir"] == "state" and x.get("kind") == "opening"] == [s.job_id]
    await s.close()


async def test_without_needs_or_minutes_and_with_a_held_title(tmp_path: Path) -> None:
    c = card()
    c.jobs[0].needs, c.jobs[0].duration_min = None, None
    c.jobs[0].title.root["en"] = HELD
    s = opened(tmp_path, c)
    said = await begin(s)
    assert said[:2] == ["4 steps.", OPENING["en"]["read_no_needs"]] and said[-1] == SPOKEN
    assert HELD not in said and HELD_STEP["en"] not in said
    await s.close()


async def test_in_dutch(tmp_path: Path) -> None:
    s = opened(tmp_path)
    s.lang = "nl"
    said = await begin(s)
    assert said[:3] == ["Pompfilter schoonmaken.", "4 stappen, ongeveer 10 minuten.", "Leg eerst klaar:"]
    assert said[-2:] == [OPENING["nl"]["read"], disclaimer("spoken", "nl")]
    await s.close()


async def test_a_session_without_the_opening_says_the_disclosure_first_and_once(tmp_path: Path) -> None:
    s = live(tmp_path)  # a recording made before the opening re-runs this way
    said = await begin(s)
    assert said == [SPOKEN]
    await s.close()
    s = session(tmp_path / "talk", FakeClient(images=PLUG_OUT), opening=True)  # no preview: as before
    said = await begin(s)
    assert said[0] == SPOKEN and said.count(SPOKEN) == 1
    await s.close()


def test_a_live_session_opens_and_a_rerun_opens_only_if_the_recording_did() -> None:
    hello = ws.Hello.model_validate({"type": "hello", "seq": 0, "t_ms": 0, "lang": "en", "client": "phone",
                                     "session_id": None, "last_seq": None})
    s = make_session(client=FakeClient(), mode="api", record_root=None, hello=hello, guide=False)  # type: ignore[arg-type]
    assert s.opening and s.preview
    assert rerun.opened([{"dir": "state", "kind": "opening"}]) and not rerun.opened([{"dir": "state", "kind": "job"}])

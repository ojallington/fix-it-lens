"""The job up front (job_preview) and the camera's part per step. A live session sends the preview when the job
starts: what to have ready, every step with its power, camera and warnings, each text through G0; step 0 waits for
the person's job_ready, which is recorded as their word and clears nothing. A step whose camera is none or find has
no camera check: Done (tap or spoken) is the person's word, the guide cannot ask for a check, and the look before
the start passes it by. The step's parts are outlined on find as on every step; on none nothing is outlined."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app import rerun
from app.guide import Guide
from app.server import make_session
from app.session import HELD_STEP, POWER_LINE, Session
from contracts import load_card
from contracts.models import tools as T
from contracts.models import ws
from contracts.models.safety import Hold
from tests.l1.helpers import FakeClient, frame
from tests.l1.test_fixit import card_data
from tests.l1.test_session import HELD, PLUG_OUT, session, tap

JOB = "clean_debris_filter"


def card(**steps: dict[str, Any]) -> Any:
    """The example card with our step, needs from its quotes, and per-step changes."""
    data = card_data()
    job = data["jobs"][0]
    job["needs"] = [{"id": "towel", "what": {"en": "A towel for the water"}, "kind": "tool", "quote_ref": "q_arrows"},
                    {"id": "bucket", "what": {"en": "A shallow tray"}, "quote_ref": "q_duration"}]
    for st in job["steps"]:
        st.update(steps.get(st["id"], {}))
    return load_card(data)


def live(tmp_path: Path, c: Any = None) -> Session:
    s = session(tmp_path, FakeClient(images=PLUG_OUT, image=PLUG_OUT["plug"]), preview=True)
    s.card = c or card()
    s.safety.card = s.card

    async def no_outline(*_: Any, **__: Any) -> None:
        return None

    s.outline_part = no_outline  # type: ignore[method-assign]
    return s


def sent(s: Session, kind: str) -> list[dict[str, Any]]:
    return [m for m in s.out if m["type"] == kind]  # type: ignore[attr-defined]


def ready(job_id: str = JOB, ids: list[str] | None = None, seq: int = 9) -> dict[str, Any]:
    return {"type": "job_ready", "seq": seq, "t_ms": 0, "job_id": job_id, "ready": ids or []}


async def test_the_preview_comes_first_and_step_0_waits(tmp_path: Path) -> None:
    s = live(tmp_path)
    s.start()
    await s.start_job()
    [p] = sent(s, "job_preview")
    ws.JobPreview.model_validate(p)
    assert s.phase == "preview" and s.run is None and not sent(s, "step")
    assert p["job_id"] == JOB and [n["id"] for n in p["needs"]] == ["towel", "bucket"]
    assert p["needs"][0]["kind"] == "tool" and p["needs"][0]["page"] == 4 and "kind" not in p["needs"][1]
    rows = {r["step_id"]: r for r in p["steps"]}
    assert list(rows) == [st.id for st in s.card.jobs[0].steps]  # type: ignore[union-attr]
    assert {"kind": "power", "text": POWER_LINE["en"]["off"]} in rows["unplug"]["warnings"]
    zone = [w for w in rows["refit_filter"]["warnings"] if w["kind"] == "zone"]
    assert zone and zone[0]["rule_id"] == "nl.mains.casing"
    assert rows["plug_back_in"]["origin"] == "fixit" and not {"page", "quote"} & set(rows["plug_back_in"])
    assert all(r["camera"] == "check" for r in p["steps"])
    await s.handle(tap("done", 3))  # nothing to do before job_ready
    await s.idle()
    assert not sent(s, "step") and not sent(s, "checking")
    await s.close()


async def test_job_ready_is_the_persons_word_and_starts_step_0_once(tmp_path: Path) -> None:
    s = live(tmp_path)
    s.start()
    await s.start_job()
    await s.handle(ready("another_job", ["towel"]))
    await s.idle()
    assert s.phase == "preview" and not sent(s, "step")
    await s.handle(ready(ids=["towel"]))
    await s.idle()
    assert s.run is not None and s.run.index == 0 and s.phase == "held"  # unplug holds until the plug is seen out
    [line] = [x for x in s.rec.lines if x["dir"] == "state" and x.get("kind") == "job_ready"]
    assert line["ready"] == ["towel"] and line["without"] == ["bucket"] and line["by_person"] is True
    assert any("without bucket" in e for e in s.events)
    n = len(s.out)  # type: ignore[attr-defined]
    await s.handle(ready(ids=["towel", "bucket"], seq=10))  # a second one changes nothing
    await s.idle()
    assert s.run.index == 0 and not sent(s, "step")[n:] and len(
        [x for x in s.rec.lines if x["dir"] == "state" and x.get("kind") == "job_ready"]) == 1
    await s.close()


async def test_job_ready_under_a_pause_starts_nothing_until_resumed_and_said_again(tmp_path: Path) -> None:
    s = live(tmp_path)
    s.start()
    await s.start_job()
    await s.handle(tap("stop", 2))
    await s.idle()
    await s.handle(ready(ids=["towel"]))
    await s.idle()
    assert s.phase == "preview" and not sent(s, "step") and isinstance(s.safety.state, Hold)  # it cleared nothing
    await s.handle(tap("yes", 3))
    await s.idle()
    await s.handle(ready(ids=["towel"], seq=4))
    await s.idle()
    assert s.run is not None and s.run.index == 0
    await s.close()


async def test_a_held_text_in_the_preview_is_the_held_line(tmp_path: Path) -> None:
    s = live(tmp_path, card(open_flap={"text": {"en": HELD}}))
    s.start()
    await s.start_job()
    [p] = sent(s, "job_preview")
    assert [r["text"] for r in p["steps"] if r["step_id"] == "open_flap"] == [HELD_STEP["en"]]
    assert sent(s, "held") and HELD not in json.dumps(s.out)  # type: ignore[attr-defined]
    await s.close()


async def at(tmp_path: Path, c: Any, step_id: str) -> Session:
    """A session without the preview, walked to step_id by camera verdicts."""
    from tests.l1.test_fixit import done

    s = session(tmp_path, FakeClient(images=PLUG_OUT, image=PLUG_OUT["plug"]))
    s.card = c
    s.safety.card = c
    outlined: list[str] = []

    async def outline(pid: str, *_: Any, **__: Any) -> None:
        outlined.append(pid)

    s.outline_part = outline  # type: ignore[method-assign]
    s.outlined = outlined  # type: ignore[attr-defined]
    s.start()
    s.frames.add(frame(1, s.clock()))
    await s.start_job()
    await s.handle(tap("yes"))
    await s.idle()
    while s.run is not None and s.run.step.id != step_id:
        await s._verdict(done(s.run.step.id))
    await s.idle()  # the step's outlines come after the step, off the worker
    return s


async def test_done_on_a_step_without_a_camera_check_is_the_persons_word(tmp_path: Path) -> None:
    for mode in ("none", "find"):
        c = card(open_flap={"camera": mode, "power": "any", "zones": [], "zone_rings": []})
        s = await at(tmp_path / mode, c, "open_flap")
        assert sent(s, "step")[-1]["camera"] == mode and "camera" not in sent(s, "step")[0]
        parts = ["filter_flap", "debris_filter"] if mode == "find" else []  # none: no locate or SAM call at all
        assert s.outlined == parts  # type: ignore[attr-defined]
        assert await s.check("the guide asked") is None
        n = len(s.out)  # type: ignore[attr-defined]
        await s.handle(tap("done", 7))
        await s.idle()
        [v] = [m["verdict"] for m in s.out[n:] if m["type"] == "verdict"]  # type: ignore[attr-defined]
        assert v["step_id"] == "open_flap" and v["witnesses"] == {"sam": "none", "by_person": True}
        assert not [m for m in s.out[n:] if m["type"] == "checking"]  # type: ignore[attr-defined]
        assert s.run is not None and s.run.step.id == "refit_filter" and "open_flap" in s.by_person
        await s.close()


async def test_the_guide_cannot_ask_for_a_check_where_the_camera_does_not_check(tmp_path: Path) -> None:
    c = card(open_flap={"camera": "none", "power": "any", "zones": [], "zone_rings": []})
    s = await at(tmp_path, c, "open_flap")
    s.guide = Guide(s)
    out = await s.guide._check_step(T.CheckStepArgs(step_id="open_flap"))
    assert "error" in out and "I did it" in out["error"]
    await s.close()


async def test_the_look_before_the_start_passes_a_step_the_camera_does_not_check(tmp_path: Path) -> None:
    s = await at(tmp_path, card(refit_filter={"camera": "find"}), "refit_filter")
    s.by_person = {"refit_filter"}
    assert not s.rechecked(s.run.step)  # type: ignore[union-attr]
    s.card = card()
    assert s.rechecked(s.card.jobs[0].steps[2])
    await s.close()


def test_a_live_session_previews_and_a_rerun_previews_only_if_the_recording_did(tmp_path: Path) -> None:
    hello = ws.Hello.model_validate({"type": "hello", "seq": 0, "t_ms": 0, "lang": "en", "client": "phone",
                                     "session_id": None, "last_seq": None})
    s = make_session(client=FakeClient(), mode="api", record_root=None, hello=hello, guide=False)  # type: ignore[arg-type]
    assert s.preview
    line = {"dir": "s2c", "kind": "job_preview", "msg": {"type": "job_preview"}}
    assert rerun.previewed([{"dir": "state", "kind": "job"}, line]) and not rerun.previewed([{"dir": "state"}])


async def test_a_spoken_done_on_a_step_without_a_camera_check_is_the_persons_word(tmp_path: Path) -> None:
    s = await at(tmp_path, card(open_flap={"camera": "none", "power": "any", "zones": [], "zone_rings": []}),
                 "open_flap")
    await s._handle_slow({"kind": "heard", "msg": None, "text": "klaar"})
    assert s.run is not None and s.run.step.id == "refit_filter" and "open_flap" in s.by_person
    assert not sent(s, "checking")
    await s.close()

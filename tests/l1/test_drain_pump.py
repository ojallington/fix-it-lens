"""The Bosch dishwasher's clean_drain_pump, end to end on the shipped card: with the restore step ingest appends (it
leaves the plug out), the card still passes its checks, a session walks the job's eleven manual steps in order, the
job counts as done once when the last one passes the camera, and the user's word ends our own step, with no camera
check, in a by_person verdict that the note marks as ours."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from app import cards, recorder
from app.note import build_note
from app.session import Session
from contracts import load_card
from contracts.models.card import Card
from ingest.appliance_file import RESTORE_TEXT
from tests.l1.helpers import FakeClient, frame
from tests.l1.test_fixit import OURS, Asked, Counted, done
from tests.l1.test_session import PLUG_OUT, ROOT, tap

JOB = "clean_drain_pump"
RESTORE = f"{JOB}_restore_mains"
MANUAL = ["remove_foreign_objects_carefully", "unplug_appliance", "remove_upper_lower_racks",
          "clean_drain_pump-remove_filter_system", "scoop_out_water", "lift_pump_cover", "remove_pump_cover",
          "remove_impeller_remnants", "refit_pump_cover", "clean_drain_pump-refit_filter_system",
          "refit_upper_lower_racks"]


def bosch() -> Card:
    """The shipped card, with our restore step after the job's last manual step, as ingest's append_restores adds
    it: fixed words, restoring the unplug step, no page or quote."""
    data = yaml.safe_load((ROOT / "cards/bosch/SMV4HAX40N.yaml").read_text(encoding="utf-8"))
    [job] = [j for j in data["jobs"] if j["id"] == JOB]
    assert [s["id"] for s in job["steps"]] == MANUAL
    job["steps"].append({**OURS, "id": RESTORE, "restores": "unplug_appliance", "text": dict(RESTORE_TEXT["mains"]),
                         "view": "front"})
    return load_card(data)


async def walked(tmp_path: Path, lang: str) -> tuple[Session, Counted]:
    """A session on the job at our step: every manual step's check passed in turn, the plug seen out on the way."""
    hooks = Counted()
    s = Session(session_id="t1", client=FakeClient(images=PLUG_OUT, image=PLUG_OUT["plug"]), card=bosch(),
                job_id=JOB, lang=lang, country="nl", record_root=tmp_path, hooks=hooks)
    s.guide = None
    s.out: list[dict[str, Any]] = []  # type: ignore[misc]

    async def sink(m: dict[str, Any]) -> None:
        s.out.append(m)  # type: ignore[attr-defined]

    s.sinks.append(sink)
    assert s.safety.card is s.card and s.meta.job_id == JOB  # the rules run on this card and job

    async def no_outline(*_: Any, **__: Any) -> None:  # outlining is tested elsewhere; here the job's order and its end
        return None

    s.outline_part = no_outline  # type: ignore[method-assign]
    s.start()
    s.frames.add(frame(1, s.clock()))
    await s.start_job()
    for i, sid in enumerate(MANUAL):
        if sid == "unplug_appliance":  # the first power-off step waits until the camera sees the plug out
            assert s.phase == "held" and s.run is not None and s.run.asked == "power_off_seen"
            assert not [m for m in s.out if m["type"] == "step" and m["step_id"] == sid]
            s.frames.add(frame(2, s.clock()))
            await s.handle(tap("yes", 2))
            await s.idle()
            assert s.power_off_seen
        assert s.run is not None and s.run.step.id == sid and s.phase == "instructed", (i, s.phase)
        assert hooks.done == 0
        await s._verdict(done(sid))
    assert not [m for m in s.out if m["type"] == "safety" and m["state"]["state"] == "STOP"]
    return s, hooks


def test_the_card_with_our_step_passes_its_checks() -> None:
    m = Asked()
    loaded = cards.check_card(bosch(), "nl", m)  # type: ignore[arg-type]
    assert loaded.usable and JOB not in loaded.refused
    [job] = [j for j in loaded.card.jobs if j.id == JOB]
    assert job.steps[-1].origin == "fixit" and job.steps[-1].restores == "unplug_appliance"


async def test_the_job_runs_from_the_first_step_to_plugging_back_in(tmp_path: Path) -> None:
    s, hooks = await walked(tmp_path, "nl")
    assert s.run is not None and s.run.step.id == RESTORE and hooks.done == 1  # recorded at the last manual step
    steps = [m for m in s.out if m["type"] == "step"]
    assert [m["step_id"] for m in steps] == [*MANUAL, RESTORE]
    assert all(m["page"] in (58, 59) for m in steps[:-1]) and steps[-1]["origin"] == "fixit"
    assert steps[-1]["text"] == RESTORE_TEXT["mains"]["nl"] and not {"page", "quote"} & set(steps[-1])
    n = len(s.out)
    await s.handle(tap("done", 3))
    await s.idle()
    assert s.phase == "job_done" and hooks.done == 1
    assert not [m for m in s.out[n:] if m["type"] == "checking"]  # the camera checks nothing of ours
    [v] = [m["verdict"] for m in s.out[n:] if m["type"] == "verdict"]
    assert v["step_id"] == RESTORE and v["result"] == "done" and v["witnesses"] == {"sam": "none", "by_person": True}
    await s.close()
    note = build_note(recorder.load(tmp_path, s.session_id), s.card, photo_frame_ids=[], renter=False)
    text = note["text"]
    assert "Afvoerpomp reinigen" in text and f"{RESTORE_TEXT['mains']['nl']} (" in text  # our step, marked ours
    [job] = [j for j in s.card.jobs if j.id == JOB]
    assert [st.text.root["nl"] for st in job.steps[:-1] if f"- {st.text.root['nl']}\n" not in text + "\n"] == []


async def test_in_english_our_step_says_plug_it_back_in(tmp_path: Path) -> None:
    s, _ = await walked(tmp_path, "en")
    assert [m for m in s.out if m["type"] == "step"][-1]["text"] == "Plug the appliance back in."
    await s.close()

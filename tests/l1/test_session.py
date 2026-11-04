"""The session: report_hazard stops and nothing leaves STOP; a held sentence never reaches s2c, the
record or the note; power off needs the camera to see the plug out, and HA can only fail it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import calls
import rules
from app import power
from app.session import Session
from contracts.models.card import Card
from contracts.models.safety import Stop
from tests.l1.helpers import FakeClient, frame, sam_result

ROOT = Path(__file__).resolve().parents[2]
HELD = "Remove the back panel."


def acme() -> Card:
    data = json.loads((ROOT / "contracts/examples/card/acme-wm-0000.json").read_text())
    for z in data["red_zones"]:
        z["rule_id"] = "nl.mains.casing"
    return Card.model_validate(data)


def session(tmp_path: Path, client: Any = None, **kw: Any) -> Session:
    s = Session(session_id="t1", client=client or FakeClient(), card=acme(), job_id="clean_debris_filter",
                country="nl", record_root=tmp_path, **kw)
    s.guide = None
    s.out: list[dict[str, Any]] = []  # type: ignore[misc]

    async def sink(m: dict[str, Any]) -> None:
        s.out.append(m)  # type: ignore[attr-defined]

    s.sinks.append(sink)
    return s


def tap(action: str, seq: int = 1) -> dict[str, Any]:
    return {"type": "tap", "seq": seq, "t_ms": 0, "action": action}


async def test_report_hazard_stops_and_nothing_leaves_stop(tmp_path: Path) -> None:
    s = session(tmp_path)
    s.start()
    st = await s.report_hazard(None, "water near the socket")
    assert isinstance(st, Stop) and s.phase == "stopped"
    assert [m["type"] for m in s.out[-2:]] == ["safety", "sentence"] and s.out[-1]["text"] == st.script
    with pytest.raises(rules.SafetyError):  # the model has no path to a clear
        s.safety.apply(s.safety.events[-1].model_copy(update={"kind": "clear"}))
    for a in ("yes", "done", "stop", "back", "repeat"):
        await s.handle(tap(a))
    await s.handle({"type": "g1", "seq": 2, "t_ms": 10, "kind": "pause", "zone_id": "casing", "reason": "hand_lost",
                    "ttc_ms": None, "dist_cm": None, "frame_id": "f1"})
    await s.idle()
    assert not await s._clear("user", "try")
    assert not await s._clear("g1", "try")
    s.safety.start_job("clean_debris_filter", s.clock())  # the same job again
    assert isinstance(s.safety.state, Stop) and s.phase == "stopped"
    n = len(s.out)
    passed = await s.say("Carry on with the filter.")
    assert passed == [] and s.out[n]["type"] == "held"  # under STOP every sentence is held: the script stands
    assert [m["type"] for m in s.out[n + 1:]] == []  # and the stop script, said once at the stop, is not said again
    await s.close()


async def test_held_sentence_never_leaves(tmp_path: Path) -> None:
    s = session(tmp_path)
    passed = await s.say(f"Good. {HELD} Then wipe the seal.")
    assert passed == ["Good.", "Then wipe the seal."]
    held = [m for m in s.out if m["type"] == "held"]
    assert len(held) == 1 and held[0]["rule_id"].startswith("nl.mains")
    assert held[0]["line"] and held[0]["words"] == 4  # the rule's own line stands in; the words never go
    assert HELD not in json.dumps(s.out)
    s.rec.end("abandoned")
    for p in tmp_path.rglob("*"):
        if p.is_file() and p.suffix in (".jsonl", ".json"):
            assert HELD not in p.read_text(), p


async def test_a_struck_sentence_is_followed_by_the_rules_warning_once(tmp_path: Path) -> None:
    s = session(tmp_path)
    await s.say(f"{HELD} {HELD} Then wipe the seal.")
    held = [m for m in s.out if m["type"] == "held"]
    warning = rules.warning_for(rules.check_sentence(HELD, "en", acme(), None, country="nl"), "en", acme(),
                                country="nl")
    said = [m["text"] for m in s.out if m["type"] == "sentence"]
    assert len(held) == 2 and said == [warning, "Then wipe the seal."]  # one warning per rule per reply
    assert held[0]["line"] in warning and HELD not in json.dumps(s.out)


async def test_i_smell_gas_speaks_the_stop_script(tmp_path: Path) -> None:
    from tests.l1.test_voice import run

    s, _, _ = await run(tmp_path, "I smell gas")
    assert isinstance(s.safety.state, Stop) and s.safety.state.rule_id == "nl.gas.smell"
    script = rules.stop_script("nl", "nl.gas.smell", "en")
    assert [m["text"] for m in s.out if m["type"] == "sentence"] == [script]
    assert not any(m["type"] == "held" for m in s.out)


async def test_g1_stop_names_the_zone_rule(tmp_path: Path) -> None:
    s = session(tmp_path)
    s.zone_rules["casing"] = "nl.mains.casing"
    await s.handle({"type": "g1", "seq": 1, "t_ms": 5, "kind": "stop", "zone_id": "casing", "reason": "distance",
                    "dist_cm": 2.0, "ttc_ms": None, "frame_id": "f1"})
    st = s.safety.state
    assert isinstance(st, Stop) and st.rule_id == "nl.mains.casing" and st.script


async def test_user_says_gas_smell_stops(tmp_path: Path) -> None:
    s = session(tmp_path)
    s.start()
    await s.handle({"type": "utterance", "seq": 1, "t_ms": 5, "utterance_id": "u1", "text": "I smell gas",
                    "lang": "en"})
    await s.idle()
    assert isinstance(s.safety.state, Stop) and s.phase == "stopped"
    await s.close()


# ---- power off -------------------------------------------------------------------------------------------------

PLUG_IN = {"plug": sam_result([("p", 0, (400, 400, 500, 500), 0.9)]),
           "socket": sam_result([("s", 0, (390, 390, 510, 510), 0.9)])}
PLUG_OUT = {"plug": sam_result([("p", 0, (100, 600, 200, 700), 0.9)]),
            "socket": sam_result([("s", 0, (390, 390, 510, 510), 0.9)])}


class Ha:
    def __init__(self, w: float | None, state: str | None = None) -> None:
        self.w, self.state = w, state

    async def entities(self, alias: str) -> tuple[bool, list[dict[str, Any]]]:
        return True, []

    async def power_w(self, alias: str) -> float | None:
        return self.w

    async def power_state(self, alias: str) -> str | None:
        return self.state


async def _evidence(tmp_path: Path, images: dict[str, calls.SamResult], ha: Any, alias: str | None) -> Any:
    s = session(tmp_path, FakeClient(images=images))
    return await power.power_evidence(s.models, frame(1, 0), ha, alias)


@pytest.mark.parametrize(("images", "ha", "alias", "result"), [
    (PLUG_IN, Ha(None), None, "not_done"),  # plug visible in the socket fails
    (PLUG_OUT, Ha(None), None, "done"),  # plug out, no HA: the camera decides
    (PLUG_OUT, Ha(3.0), "washer", "not_done"),  # plug out but HA > 0 W fails
    (PLUG_OUT, Ha(0.0), "washer", "done"),
    (PLUG_OUT, Ha(None, "on"), "washer", "not_done"),
    (PLUG_IN, Ha(0.0), "washer", "not_done"),  # a stale 0 W never passes by itself: the camera is the gate
])
async def test_power_off(tmp_path: Path, images: dict[str, calls.SamResult], ha: Any, alias: str | None,
                         result: str) -> None:
    ev = await _evidence(tmp_path, images, ha, alias)
    assert ev.witness().result == result


async def test_unplug_step_holds_until_the_plug_is_seen_out(tmp_path: Path) -> None:
    client = FakeClient(images=PLUG_IN)
    s = session(tmp_path, client)
    s.start()
    s.frames.add(frame(1, s.clock()))
    await s.start_job()
    assert s.phase == "held" and not any(m["type"] == "step" for m in s.out)
    assert any(m["type"] == "sentence" and "plug" in m["text"] for m in s.out)
    await s.handle(tap("yes"))
    await s.idle()
    assert s.phase == "held" and not s.power_off_seen
    client.images = PLUG_OUT
    s.frames.add(frame(2, s.clock()))
    await s.handle(tap("yes", 2))
    await s.idle()
    assert s.power_off_seen and s.phase == "instructed"
    assert [m for m in s.out if m["type"] == "step"][0]["step_id"] == "unplug"
    await s.close()


@pytest.mark.parametrize(("images", "point", "result"), [
    (PLUG_OUT, [150, 650], "done"),  # judge points at the plug, SAM sees it out
    (PLUG_OUT, [900, 100], "cant_see"),  # a point off the plug and the socket is never done
    (PLUG_IN, [450, 450], "not_done"),  # the judge is fooled; SAM sees the plug in
])
async def test_unplug_check_grounds_on_the_plug_and_socket(tmp_path: Path, images: dict[str, calls.SamResult],
                                                           point: list[int], result: str) -> None:
    from tests.l1.test_stepcheck import Judge

    client = Judge(point)
    client.images = images
    s = session(tmp_path, client)
    s.start()
    for i in range(6):
        s.frames.add(frame(i, i * 100))
    s.power_off_seen = True
    await s.begin_step(0)
    v = await s.check("tap done")
    assert v is not None and v.result == result and v.witnesses.sam == "frame"
    await s.close()


async def test_breaker_switch_sends_the_local_badge(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[Any] = []

    class Breaker:  # the local backend's calls.Breaker: the session only hands it on_switch
        def __init__(self, api: Any, local: Any, *, on_switch: Any) -> None:
            self.on_switch = on_switch
            made.append(self)

    async def track(*_: Any) -> Any:
        return None

    async def provider(client: Any) -> Any:
        return track

    monkeypatch.setattr(calls, "Breaker", Breaker, raising=False)
    monkeypatch.setattr("local.witness.provider", provider)
    s = session(tmp_path, fallback=FakeClient())
    assert made and s.client is made[0]
    await made[0].on_switch("respond: CallTimeout")
    assert s.mode == "local" and s.witness_provider is track
    notice = [m for m in s.out if m["type"] == "notice"][-1]
    assert notice["kind"] == "local_mode" and notice["text"]

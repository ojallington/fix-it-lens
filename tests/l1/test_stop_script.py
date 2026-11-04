"""Every STOP is heard: whichever path raised it (a hand in a zone, the step gate, the user's words, the model's
report_hazard), the safety message goes out first and the stop's script follows it as a sentence, exactly once."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.session import Session
from contracts.models.card import Card
from contracts.models.safety import Stop
from tests.l1.test_guide import guided, resp
from tests.l1.test_session import acme, session
from tests.l1.test_voice import run


def heard_once(s: Session) -> None:
    out: list[dict[str, Any]] = s.out  # type: ignore[attr-defined]
    st = s.safety.state
    assert isinstance(st, Stop) and s.phase == "stopped" and st.script
    stop = next(i for i, m in enumerate(out) if m["type"] == "safety" and m["state"]["state"] == "STOP")
    said = [i for i, m in enumerate(out) if m["type"] == "sentence" and m["text"] == st.script]
    assert len(said) == 1 and said[0] > stop, (stop, said)  # the STOP first, then its script, once
    assert not any(m["type"] == "held" for m in out[stop:])


async def test_a_hand_in_a_zone_says_the_script(tmp_path: Path) -> None:
    s = session(tmp_path)
    s.zone_rules["casing"] = "nl.mains.casing"
    g1 = {"type": "g1", "seq": 1, "t_ms": 5, "kind": "stop", "zone_id": "casing", "reason": "distance",
          "dist_cm": 2.0, "ttc_ms": None, "frame_id": "f1"}
    await s.handle(g1)
    await s.handle({**g1, "seq": 2, "t_ms": 9})  # a second stop on a stopped session says nothing more
    heard_once(s)


async def test_the_step_gate_says_the_script(tmp_path: Path) -> None:
    data = acme().model_dump()
    data["jobs"][0]["steps"][0]["text"] = {"en": "Unscrew the back panel to reach the pump."}
    s = session(tmp_path)
    s.card = Card.model_validate(data)
    s.start()
    await s.start_job()
    await s.idle()
    assert s.safety.state.rule_id == "nl.mains.casing"  # type: ignore[union-attr]
    heard_once(s)
    await s.close()


async def test_the_users_words_say_the_script(tmp_path: Path) -> None:
    s, _, _ = await run(tmp_path, "I smell gas")
    heard_once(s)


async def test_report_hazard_says_the_script(tmp_path: Path) -> None:
    s, _ = guided(tmp_path, [resp(calls_=[("report_hazard", {"rule_id": None, "evidence": "water on the floor"})])])
    await s.guide.turn("there is water near the socket")
    heard_once(s)
    await s.report_hazard(None, "water again")  # the model reporting again: still one script
    heard_once(s)


G1 = {"type": "g1", "seq": 1, "t_ms": 5, "kind": "stop", "zone_id": "casing", "reason": "distance", "dist_cm": 2.0,
      "ttc_ms": None, "frame_id": "f1"}


def scripts(s: Session, start: int) -> list[str]:
    return [m["text"] for m in s.out[start:] if m["type"] == "sentence"]  # type: ignore[attr-defined]


async def test_a_stop_after_a_clear_is_heard_again(tmp_path: Path) -> None:
    s = session(tmp_path)
    s.zone_rules["casing"] = "nl.mains.casing"
    s.start()
    await s.handle(G1)
    s.safety.g1_clear(s.clock())
    await s._safety_sync()
    assert not isinstance(s.safety.state, Stop)
    n = len(s.out)  # type: ignore[attr-defined]
    await s.handle({**G1, "seq": 3, "t_ms": 50})
    st = s.safety.state
    assert isinstance(st, Stop) and scripts(s, n) == [st.script]
    await s.close()


async def test_a_gas_smell_during_a_mains_stop_says_the_gas_script(tmp_path: Path) -> None:
    s = session(tmp_path)
    s.zone_rules["casing"] = "nl.mains.casing"
    s.start()
    await s.handle(G1)
    mains = s.safety.state
    n = len(s.out)  # type: ignore[attr-defined]
    await s.handle({"type": "utterance", "seq": 2, "t_ms": 20, "utterance_id": "u1", "text": "I smell gas",
                    "lang": "en"})
    await s.idle()
    st = s.safety.state
    assert isinstance(mains, Stop) and isinstance(st, Stop) and st.rule_id == "nl.gas.smell"
    assert scripts(s, n) == [st.script] and st.script != mains.script  # the gas script, once
    await s.close()

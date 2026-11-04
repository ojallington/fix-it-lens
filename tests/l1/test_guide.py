"""The guide: the cached prefix is byte-identical across turns and frames ride only in the newest user
message; report_hazard stops; a held sentence from the model never leaves; the doom-loop guard; the rail."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import calls
from app.guide import STUCK, Guide, tools
from app.session import Session
from contracts.models.safety import Stop
from tests.l1.helpers import frame
from tests.l1.test_session import HELD, acme


def resp(text: str = "", calls_: list[tuple[str, dict[str, Any]]] | None = None, summary: str = "") -> calls.Response:
    fcs = [calls.FunctionCall(n, f"c{i}", json.dumps(a)) for i, (n, a) in enumerate(calls_ or [])]
    out: list[dict[str, Any]] = [{"type": "reasoning", "id": "rs1", "encrypted_content": "xx", "summary": []}]
    if text:
        out.append({"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]})
    out += [{"type": "function_call", "call_id": f.call_id, "name": f.name, "arguments": f.arguments} for f in fcs]
    return calls.Response("guide", "muse-spark-1.3", "completed", text, out, fcs, calls.Usage(), 900.0, None, 0.004,
                          "2" * 64, 1, "c", summary)


class Scripted:
    def __init__(self, replies: list[calls.Response]) -> None:
        self.replies = replies
        self.inputs: list[list[dict[str, Any]]] = []

    async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
        assert role == "guide" and kw["tools"] == list(tools())
        self.inputs.append(json.loads(json.dumps(input)))
        return self.replies.pop(0) if self.replies else resp("Okay.")


def guided(tmp_path: Path, replies: list[calls.Response]) -> tuple[Session, Scripted]:
    c = Scripted(replies)
    s = Session(session_id="g1", client=c, card=acme(), job_id="clean_debris_filter", country="nl",  # type: ignore[arg-type]
                record_root=tmp_path)
    s.guide = Guide(s)
    s.out = []  # type: ignore[attr-defined]

    async def sink(m: dict[str, Any]) -> None:
        s.out.append(m)  # type: ignore[attr-defined]

    s.sinks.append(sink)
    return s, c


def images(msg: dict[str, Any]) -> int:
    return sum(1 for c in msg.get("content", []) if isinstance(c, dict) and c.get("type") == "input_image")


async def test_tools_are_the_eight_flat_functions() -> None:
    t = tools()
    assert [x["name"] for x in t] == ["lookup_manual", "locate", "segment", "cite", "check_step", "report_hazard",
                                      "ask_for_view", "ha_state"]
    assert "$ref" not in json.dumps(t) and all(x["type"] == "function" for x in t)


async def test_prefix_byte_identical_and_frames_only_in_the_newest_user_message(tmp_path: Path) -> None:
    s, c = guided(tmp_path, [resp("Let's start."), resp("Good.")])
    s.frames.add(frame(1, 0))
    await s.guide.turn("hello")
    s.frames.add(frame(2, 500))
    await s.guide.turn("what now")
    n = len(s.guide.prefix)
    a, b = c.inputs
    assert json.dumps(a[:n], sort_keys=True) == json.dumps(b[:n], sort_keys=True)
    for req in (a, b):
        users = [i for i, m in enumerate(req) if m.get("role") == "user"]
        assert [images(req[i]) for i in users] == [0] * (len(users) - 1) + [1]
    assert "f2" in json.dumps(b[-1]) and "f1" not in json.dumps(b)  # the old frame is never replayed
    assert any(m.get("type") == "reasoning" for m in b)  # encrypted reasoning replayed statelessly


async def test_report_hazard_stops(tmp_path: Path) -> None:
    s, _ = guided(tmp_path, [resp(calls_=[("report_hazard", {"rule_id": None, "evidence": "water on the floor"})])])
    await s.guide.turn("there is water near the socket")
    assert isinstance(s.safety.state, Stop) and s.phase == "stopped"
    assert any(m["type"] == "safety" and m["state"]["state"] == "STOP" for m in s.out)


async def test_held_sentence_from_the_model_never_leaves(tmp_path: Path) -> None:
    s, _ = guided(tmp_path, [resp(f"Okay. {HELD}")])
    await s.guide.turn("how do I get inside?")
    assert any(m["type"] == "held" for m in s.out) and HELD not in json.dumps(s.out)


async def test_doom_loop_ends_the_turn(tmp_path: Path) -> None:
    call = ("lookup_manual", {"query": "filter"})
    s, c = guided(tmp_path, [resp(calls_=[call]) for _ in range(5)])
    await s.guide.turn("help")
    assert len(c.inputs) == 3 and any(m.get("text") == STUCK["en"].split(". ")[0] + "." for m in s.out)


async def test_reasoning_summary_goes_to_the_rail(tmp_path: Path) -> None:
    s, _ = guided(tmp_path, [resp("Hi.", summary="The user greets; step 1 next.")])
    await s.guide.turn("hi")
    assert any(m["type"] == "rail" and m["tool"] == "reasoning" for m in s.out)


class Stuck(Scripted):
    """A guide call that does not answer until released: the worker sits in a guide turn."""

    def __init__(self) -> None:
        super().__init__([])
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
        self.entered.set()
        await self.release.wait()
        return resp("Okay.")


async def test_a_stop_tap_never_waits_on_a_guide_turn(tmp_path: Path) -> None:
    s, _ = guided(tmp_path, [])
    stuck = Stuck()
    s.client = s.models.client = stuck  # type: ignore[assignment]
    s.start()
    await s.handle({"type": "utterance", "seq": 1, "t_ms": 0, "utterance_id": "u1", "lang": "en",
                    "text": "where is the filter?", "audio_wav_b64": None})
    await asyncio.wait_for(stuck.entered.wait(), 2)  # the worker is inside the guide's call
    await s.handle({"type": "tap", "seq": 2, "t_ms": 10, "action": "stop"})
    assert any(m["type"] == "notice" and m["kind"] == "paused" for m in s.out)  # at once, the call still open
    assert not stuck.release.is_set()
    stuck.release.set()
    await s.close()


async def test_in_stop_model_sentences_are_held_and_the_script_is_not_said_again(tmp_path: Path) -> None:
    s, _ = guided(tmp_path, [resp(calls_=[("report_hazard", {"rule_id": None, "evidence": "water on the floor"})]),
                             resp("Unplug it first. Then open the panel.")])
    await s.guide.turn("there is water near the socket")
    assert isinstance(s.safety.state, Stop)
    before = len(s.out)
    await s.guide.turn("what now?")
    after = s.out[before:]
    assert [m["type"] for m in after if m["type"] in ("held", "sentence")] == ["held", "held"]

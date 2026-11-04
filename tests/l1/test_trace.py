"""The rail in plain words (app.trace, polish finding 3): every row a user could read, with no id, no Python value, no
exception or fixture name; the machine detail stays in the record as a `trace` state line."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import calls
from app import recorder, trace
from tests.l1.test_guide import Scripted, guided, resp
from tests.l1.test_session import acme

ID = re.compile(r"\b[a-z0-9]+_[a-z0-9_]+\b|\bNone\b|\bnull\b|Error\b|Fixture|Timeout|\(.*=")


def rails(s: Any) -> list[dict[str, Any]]:
    return [m for m in s.out if m["type"] == "rail"]


def plain_row(m: dict[str, Any]) -> bool:
    return not ID.search(m["args_short"]) and not ID.search(m["result_short"]) and bool(m["result_short"])


async def test_guide_tool_rows_are_plain_words(tmp_path: Path) -> None:
    step = acme().jobs[0].steps[0]
    s, _ = guided(tmp_path, [
        resp(calls_=[("lookup_manual", {"query": "filter"}),
                     ("cite", {"step_id": step.id, "page": None, "quote": None}),
                     ("cite", {"step_id": step.id, "page": 3, "quote": "x"}),
                     ("check_step", {"step_id": "nope"})]),
        resp("Done.", summary="**Next**: check the `filter`.")])
    await s.guide.turn("how?")
    rows = rails(s)
    assert len(rows) == 5 and all(plain_row(m) for m in rows), rows
    by = {m["tool"]: m for m in reversed(rows)}  # the first row of each tool
    assert by["cite"]["args_short"] == "the step" and by["check_step"]["result_short"] == "couldn't do that"
    assert by["reasoning"]["result_short"] == "Next: check the filter."  # the model's words, plain and gated
    lines = recorder.load(tmp_path, "g1").lines
    traced = [x for x in lines if x["dir"] == "state" and x["kind"] == "trace"]
    assert any(x["tool"] == "cite" and step.id in x["args"] for x in traced)  # the ids are in the record only


class Missing(Scripted):
    async def respond(self, role: str, input: Any, **kw: Any) -> calls.Response:
        raise calls.MissingFixture("guide 1234abcd")


async def test_a_failed_call_says_what_happened_not_its_class(tmp_path: Path) -> None:
    s, _ = guided(tmp_path, [])
    s.client = s.models.client = Missing([])  # type: ignore[assignment]
    await s.guide.turn("how?")
    rows = [m for m in rails(s) if m["tool"] == "guide"]
    assert rows and rows[0]["result_short"] == "no recording for this call" and plain_row(rows[0])
    assert "MissingFixture" not in json.dumps(s.out)


def test_failure_phrases() -> None:
    assert trace.failed(calls.CallTimeout("x")) == "too slow, gave up"
    assert trace.failed(RuntimeError("x")) == "the service didn't answer"
    assert trace.result({"hits": [{"page": 4}, {"page": 2}, {"page": 4}]}, "lookup_manual") == "found it on page 2, 4"
    assert trace.result({"verified": True, "page": 7, "quote": "q"}, "cite") == "matches the manual on page 7"
    assert trace.result({"available": True, "entities": [{}]}, "ha_state") == "1 sensor"

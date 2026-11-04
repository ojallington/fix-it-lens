"""The local backend: the <|eot|> strip, the LAN rail, record and replay. No GPU, no network:
the backend talks to a transport that answers from fixtures/l7/raw/ and records every request it is sent."""

from __future__ import annotations

import io
import json
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image

import calls
from calls.backends.local import LocalBackend, chat_body, is_lan_host, strip_stop_tokens
from calls.client import Client
from calls.config import ROOT, load_config
from calls.errors import BackendUnavailable, ConfigError, IncompleteResponse
from calls.ledger import Ledger

RAW = ROOT / "fixtures" / "l7" / "raw"
GLIMMER = "muse-glimmer:30b"
VERDICT = {"type": "json_schema", "name": "verdict", "strict": True, "schema": {
    "type": "object", "additionalProperties": False, "required": ["result", "shape"],
    "properties": {"result": {"type": "string", "enum": ["done", "not_done", "cant_see"]},
                   "shape": {"type": "string"}}}}
SEGMENT = {"type": "function", "name": "segment", "description": "Outline a part",
           "parameters": {"type": "object", "properties": {"concept": {"type": "string"}}, "required": ["concept"]}}


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (640, 480), (200, 40, 40)).save(buf, "JPEG")
    return buf.getvalue()


def sse(*chunks: dict[str, Any]) -> bytes:
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"


def chunk(delta: dict[str, Any] | None = None, finish: str | None = None, **extra: Any) -> dict[str, Any]:
    choices = [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]
    return {"object": "chat.completion.chunk", "model": GLIMMER, "choices": choices, **extra}


class Door(httpx.AsyncBaseTransport):
    """A stand-in for ollama's OpenAI door: answers each request with the next canned stream."""

    def __init__(self, *streams: bytes) -> None:
        self.streams = list(streams)
        self.sent: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        return httpx.Response(200, content=self.streams.pop(0), headers={"content-type": "text/event-stream"})


def live_local(tmp_path: Path, door: Door, mode: str = "live") -> Client:
    private = tmp_path / "private"
    private.mkdir(exist_ok=True)
    (private / "budgets.toml").write_text("total_cap = 100.0\n[lanes]\nl7 = 2.0\n")
    cfg = load_config().local()
    return Client(mode, cfg, LocalBackend(cfg.local_base_url, transport=door),  # type: ignore[arg-type]
                  ledger=Ledger(private / "ledger.jsonl", private / "budgets.toml"), fixtures_root=tmp_path,
                  private=private)


def test_local_config_swaps_the_brain_not_the_speech() -> None:
    cfg = load_config()
    local = cfg.local()
    for role in ("guide", "judge", "locate", "plate", "appliance_file"):
        assert cfg.role(role).model != GLIMMER and local.role(role).model == GLIMMER
    assert local.role("sam").model == "facebook/sam3.1"
    assert local.role("transcribe") == cfg.role("transcribe")
    assert local.role("judge").max_output_tokens == cfg.role("judge").max_output_tokens  # merged field by field
    assert is_lan_host(local.local_base_url.split("//")[1].split(":")[0])


@pytest.mark.parametrize("host,ok", [("gpu-box", True), ("localhost", True), ("192.168.1.20", True),
                                     ("100.64.0.1", True), ("box.tail1234.ts.net", True),
                                     ("8.8.8.8", False), ("inference.baseten.co", False)])
def test_lan_host(host: str, ok: bool) -> None:
    assert is_lan_host(host) is ok


def test_the_api_url_is_refused_as_a_local_backend() -> None:
    cfg = load_config()
    assert not is_lan_host(httpx.URL(cfg.base_url).host)
    with pytest.raises(ConfigError, match="not on the LAN"):
        LocalBackend(cfg.base_url)
    with pytest.raises(ConfigError):
        LocalBackend(cfg.local_base_url, "https://sam.example.com")


def test_stop_tokens_are_stripped() -> None:
    assert strip_stop_tokens('{ "result": "done" }<|eot|>') == '{ "result": "done" }'
    assert strip_stop_tokens("plain") == "plain"


def test_chat_body_translates_a_tool_turn() -> None:
    body = {
        "model": GLIMMER, "stream": True, "store": False, "instructions": "You are the guide.",
        "max_output_tokens": 4000, "reasoning": {"effort": "low"}, "tools": [SEGMENT], "text": {"format": VERDICT},
        "input": [
            {"type": "message", "role": "user", "content": [
                {"type": "input_text", "text": "Outline the filter."},
                {"type": "input_image", "image_url": "data:image/jpeg;base64,AAAA", "detail": "high"}]},
            {"id": "rs_1", "type": "reasoning", "summary": [], "encrypted_content": "enc"},
            {"type": "function_call", "call_id": "c1", "name": "segment", "arguments": '{"concept":"filter"}'},
            {"type": "function_call_output", "call_id": "c1", "output": '{"found": true}'},
        ],
    }
    out = chat_body(body)
    assert out["reasoning_effort"] == "none" and out["temperature"] == 0 and out["max_tokens"] == 4000
    assert [m["role"] for m in out["messages"]] == ["system", "user", "assistant", "tool"]
    assert out["messages"][1]["content"][1]["image_url"] == {"url": "data:image/jpeg;base64,AAAA"}
    assert out["messages"][2]["tool_calls"][0]["function"]["name"] == "segment"
    assert out["messages"][3] == {"role": "tool", "tool_call_id": "c1", "content": '{"found": true}'}
    assert out["tools"][0]["function"]["name"] == "segment"
    assert out["response_format"]["json_schema"]["schema"] == VERDICT["schema"]
    assert "store" not in out and "reasoning" not in out


def test_an_uploaded_file_id_is_api_only() -> None:
    body = {"model": GLIMMER, "input": [{"type": "message", "role": "user", "content": [
        {"type": "input_file", "file_id": "file_123"}]}]}
    with pytest.raises(calls.CallFailed, match="file_id"):
        chat_body(body)


async def test_eot_fixture_parses_and_ledger_says_local(tmp_path: Path) -> None:
    door = Door((RAW / "judge-eot.sse").read_bytes())
    client = live_local(tmp_path, door)
    r = await client.respond("judge", "Is there a red circle?", format=VERDICT, images=[jpeg()], lane="l7")
    assert r.parsed() == {"result": "done", "shape": "circle"}
    assert r.model == GLIMMER and r.usd == 0.0 and r.usage.input_tokens == 347
    (line,) = [json.loads(x) for x in (tmp_path / "private" / "ledger.jsonl").read_text().splitlines()]
    assert line["mode"] == "local" and line["response_model"] == GLIMMER and line["usd"] == 0.0
    assert line["store"] is False
    sent = json.loads(door.sent[0].content)
    assert sent["reasoning_effort"] == "none" and sent["model"] == GLIMMER


async def test_tool_call_streams_into_a_function_call(tmp_path: Path) -> None:
    client = live_local(tmp_path, Door((RAW / "guide-tool.sse").read_bytes()))
    r = await client.respond("guide", "Outline the dishwasher filter.", tools=[SEGMENT], lane="l7")
    (fc,) = r.function_calls
    assert fc.name == "segment" and fc.call_id == "call_ohwi4y90" and fc.args() == {"concept": "dishwasher filter"}


async def test_length_finish_is_incomplete_and_never_parsed(tmp_path: Path) -> None:
    cut = sse(chunk({"content": '{"result": "do'}), chunk({}, "length"), chunk(usage={"prompt_tokens": 9}))
    door = Door(cut, cut)
    client = live_local(tmp_path, door)
    with pytest.raises(IncompleteResponse):
        await client.respond("judge", "x", format=VERDICT, lane="l7")
    assert [json.loads(r.content)["max_tokens"] for r in door.sent] == [4000, 8000]


def _spy(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Record every DNS lookup and socket connect, and fail the test on any of them."""
    seen: list[Any] = []

    def deny(name: str) -> Callable[..., Any]:
        def f(*a: Any, **k: Any) -> Any:
            seen.append((name, a[:2]))
            raise AssertionError(f"local mode opened the network: {name}{a[:2]}")
        return f

    monkeypatch.setattr(socket, "getaddrinfo", deny("getaddrinfo"))
    monkeypatch.setattr(socket.socket, "connect", deny("connect"))
    import calls.backends.meta as meta

    monkeypatch.setattr(meta, "AsyncOpenAI", deny("AsyncOpenAI"))
    return seen


async def test_local_turns_never_reach_api_meta_ai(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _spy(monkeypatch)
    door = Door((RAW / "guide-tool.sse").read_bytes(),
                sse(chunk({"content": "Lift the filter out by its handle.<|eot|>"}), chunk({}, "stop")),
                (RAW / "judge-eot.sse").read_bytes())
    client = live_local(tmp_path, door)
    turn = await client.respond("guide", "Outline the filter.", tools=[SEGMENT], images=[jpeg()], lane="l7")
    fc = turn.function_calls[0]
    history = [{"type": "message", "role": "user", "content": [{"type": "input_text", "text": "Outline the filter."}]},
               {"type": "function_call", "call_id": fc.call_id, "name": fc.name, "arguments": fc.arguments},
               {"type": "function_call_output", "call_id": fc.call_id, "output": '{"found": false}'}]
    said = await client.respond("guide", history, tools=[SEGMENT], lane="l7")
    assert said.text == "Lift the filter out by its handle."
    verdict = await client.respond("judge", "Is the filter out?", format=VERDICT, images=[jpeg()], lane="l7")
    assert verdict.parsed()["result"] == "done"
    assert seen == []
    assert {r.url.host for r in door.sent} == {httpx.URL(load_config().local_base_url).host}
    api_host = httpx.URL(load_config().base_url).host
    assert all(api_host not in json.dumps(b) for b in client.requests)


async def test_record_then_replay_a_local_call(tmp_path: Path) -> None:
    rec = live_local(tmp_path, Door((RAW / "judge-eot.sse").read_bytes()), mode="record")
    first = await rec.respond("judge", "Is there a red circle?", format=VERDICT, images=[jpeg()], lane="l7")
    (fx,) = (tmp_path / "l7").glob("*.jsonl")
    assert "<|eot|>" not in fx.read_text() and "Is there a red circle" not in fx.read_text()
    replay = calls.make_client("replay", fixtures_root=tmp_path, private=None, local=True)
    again = await replay.respond("judge", "Is there a red circle?", format=VERDICT, images=[jpeg()], lane="l7")
    assert again.parsed() == first.parsed() and again.model == GLIMMER


async def test_no_local_speech_or_unconfigured_sam(tmp_path: Path) -> None:
    client = live_local(tmp_path, Door())
    with pytest.raises(BackendUnavailable):
        await client.backend.transcribe({}, b"", sha="x")
    with pytest.raises(BackendUnavailable, match="Glimmer's boxes"):
        await client.sam_image(jpeg(), "filter", lane="l7")


def test_fixit_mode_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FIXIT_LOCAL", raising=False)
    monkeypatch.setenv("FIXIT_MODE", "local")
    assert calls.local_mode() is True
    monkeypatch.setenv("FIXIT_MODE", "api")
    assert calls.local_mode() is False
    monkeypatch.setenv("FIXIT_MODE", "cloud")
    with pytest.raises(ConfigError):
        calls.local_mode()


def test_the_manual_goes_in_as_page_marked_text() -> None:
    import base64

    from tools import make_acme_manual

    data = "data:application/pdf;base64," + base64.b64encode(make_acme_manual.build_pdf()).decode()
    body = {"model": GLIMMER, "input": [{"type": "message", "role": "user", "content": [
        {"type": "input_file", "filename": "acme-wm-0000.pdf", "file_data": data},
        {"type": "input_text", "text": "Build the appliance file."}]}]}
    (msg,) = chat_body(body)["messages"]
    assert isinstance(msg["content"], str)  # text only, so one string: Glimmer cites the page markers
    assert "--- page 2 ---" in msg["content"] and "Do not open the casing." in msg["content"]
    assert msg["content"].index("--- page 6 ---") < msg["content"].index("Build the appliance file.")


async def test_count_tokens_reads_glimmer_prompt_tokens(tmp_path: Path) -> None:
    class Count(Door):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            self.sent.append(request)
            return httpx.Response(200, json={"model": GLIMMER, "choices": [], "usage": {
                "prompt_tokens": 2846, "completion_tokens": 1}})

    door = Count()
    client = live_local(tmp_path, door)
    assert await client.count_tokens("guide", "How full is the context?", images=[jpeg()], lane="l7") == 2846
    sent = json.loads(door.sent[0].content)
    assert sent["max_tokens"] == 1 and sent["stream"] is False and "stream_options" not in sent
    line = json.loads((tmp_path / "private" / "ledger.jsonl").read_text().splitlines()[0])
    assert line["mode"] == "local" and line["usd"] == 0.0 and line["input_tokens"] == 2846

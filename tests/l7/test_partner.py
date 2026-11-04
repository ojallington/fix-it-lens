"""The partner backend (Spark Open on a partner's free credit): it fails closed without a 0600 key file, sends chat
completions at the role's own reasoning effort, asserts the response model, and books the ledger line to its
provider. No key and no network: the partner is a mock transport."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import httpx
import pytest

import calls
from calls.backends.partner import PartnerBackend, key_problem
from calls.client import Client
from calls.config import ConfigError, load_config
from calls.errors import BackendUnavailable, CallFailed, ModelMismatch
from calls.ledger import Ledger

SPARK_OPEN = "meta-models/muse-spark-open"
KEY = "bt-test-key-not-real"
VERDICT = {"type": "json_schema", "name": "verdict", "strict": True, "schema": {
    "type": "object", "additionalProperties": False, "required": ["result"],
    "properties": {"result": {"type": "string", "enum": ["done", "not_done", "cant_see"]}}}}


def sse(*chunks: dict[str, Any]) -> bytes:
    return b"".join(b"data: " + json.dumps(c).encode() + b"\n\n" for c in chunks) + b"data: [DONE]\n\n"


def chunk(delta: dict[str, Any] | None = None, finish: str | None = None, model: str = SPARK_OPEN,
          **extra: Any) -> dict[str, Any]:
    choices = [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish}]
    return {"object": "chat.completion.chunk", "model": model, "choices": choices, **extra}


USAGE = {"prompt_tokens": 1500, "completion_tokens": 400, "completion_tokens_details": {"reasoning_tokens": 380}}


class Partner(httpx.AsyncBaseTransport):
    def __init__(self, *responses: httpx.Response) -> None:
        self.responses = list(responses)
        self.sent: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request)
        return self.responses.pop(0)


def stream(*chunks: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, content=sse(*chunks), headers={"content-type": "text/event-stream"})


def client(tmp_path: Path, transport: Partner, *, key: str | None = KEY, mode: int = 0o600,
           provider: str = "baseten") -> Client:
    keyfile = tmp_path / f"{provider}.key"
    if key is not None:
        keyfile.write_text(key + "\n")
        keyfile.chmod(mode)
    base = load_config()
    cfg = replace(base, partners={**base.partners, provider: replace(base.partners[provider], key_path=keyfile)})
    cfg = cfg.partner(provider)
    private = tmp_path / "private"
    private.mkdir()
    (private / "budgets.toml").write_text("total_cap = 100.0\n[lanes]\nl7 = 2.0\n")
    return Client("live", cfg, PartnerBackend(cfg.partners[provider], transport=transport),
                  ledger=Ledger(private / "ledger.jsonl", private / "budgets.toml"), fixtures_root=tmp_path,
                  private=private)


def ledger(c: Client) -> list[dict[str, Any]]:
    assert c.ledger is not None
    return [json.loads(x) for x in c.ledger.path.read_text().splitlines()]


async def test_no_key_file_fails_closed_before_any_request(tmp_path: Path) -> None:
    t = Partner()
    c = client(tmp_path, t, key=None)
    with pytest.raises(BackendUnavailable, match="baseten: waits on key"):
        await c.respond("judge", "is the filter in?", lane="l7")
    assert t.sent == []
    assert not c.ledger.path.exists() or ledger(c) == []  # type: ignore[union-attr]


async def test_a_key_others_can_read_is_refused(tmp_path: Path) -> None:
    t = Partner()
    c = client(tmp_path, t, mode=0o644)
    with pytest.raises(BackendUnavailable, match="must be 0600"):
        await c.respond("judge", "is the filter in?", lane="l7")
    assert t.sent == []


async def test_an_empty_key_is_refused(tmp_path: Path) -> None:
    t = Partner()
    c = client(tmp_path, t, key="")
    with pytest.raises(BackendUnavailable, match="empty"):
        await c.respond("judge", "is the filter in?", lane="l7")
    assert t.sent == []


def test_modal_is_off_until_its_credit_is_confirmed() -> None:
    cfg = load_config()
    assert key_problem(cfg.partners["modal"]) == "modal: off in config/models.toml"


async def test_a_partner_effort_in_config_overrides_the_role(tmp_path: Path) -> None:
    from dataclasses import replace

    from calls.backends.partner import PartnerBackend

    p = calls.load_config().partners["fireworks"]
    body = {"reasoning": {"effort": "minimal"}}
    assert PartnerBackend(p).effort(body) == "minimal"
    assert PartnerBackend(replace(p, reasoning_effort="low")).effort(body) == "low"


async def test_a_judge_turn_on_spark_open(tmp_path: Path) -> None:
    t = Partner(stream(chunk({"role": "assistant", "content": ""}), chunk({"content": '{"result": '}),
                       chunk({"content": '"done"}'}, "stop"), chunk(None, usage=USAGE)))
    c = client(tmp_path, t)
    r = await c.respond("judge", "is the filter in?", format=VERDICT, lane="l7", session_id="s1")
    assert r.parsed() == {"result": "done"} and r.model == SPARK_OPEN
    sent = t.sent[0]
    assert str(sent.url) == "https://inference.baseten.co/v1/chat/completions"
    assert sent.headers["authorization"] == f"Bearer {KEY}"
    body = json.loads(sent.content)
    assert body["model"] == SPARK_OPEN and body["reasoning_effort"] == "minimal" and body["stream"] is True
    assert body["response_format"]["json_schema"]["schema"] == VERDICT["schema"]
    [line] = ledger(c)
    assert line["mode"] == "partner" and line["provider"] == "baseten" and line["model"] == SPARK_OPEN
    assert line["response_model"] == SPARK_OPEN and line["reasoning_tokens"] == 380
    assert line["usd"] == pytest.approx((1500 * 1.25 + 400 * 4.25) / 1e6)


async def test_the_key_is_never_written_down(tmp_path: Path) -> None:
    t = Partner(stream(chunk({"content": '{"result": "done"}'}, "stop"), chunk(None, usage=USAGE)))
    c = client(tmp_path, t)
    await c.respond("judge", "is the filter in?", format=VERDICT, lane="l7")
    written = "".join(p.read_text(errors="replace") for p in tmp_path.rglob("*") if p.is_file()
                      and p.name != "baseten.key")
    assert KEY not in written and KEY not in json.dumps(c.requests)


async def test_another_model_in_the_reply_is_refused(tmp_path: Path) -> None:
    t = Partner(stream(chunk({"content": '{"result": "done"}'}, "stop", model="muse-glimmer:30b"),
                       chunk(None, usage=USAGE, model="muse-glimmer:30b")))
    c = client(tmp_path, t)
    with pytest.raises(ModelMismatch):
        await c.respond("judge", "is the filter in?", format=VERDICT, lane="l7")
    assert ledger(c)[0]["response_model"] == "muse-glimmer:30b"


async def test_a_guide_tool_call(tmp_path: Path) -> None:
    call = {"index": 0, "id": "call_1", "function": {"name": "segment", "arguments": '{"concept": "filter cap"}'}}
    t = Partner(stream(chunk({"tool_calls": [call]}, "tool_calls"), chunk(None, usage=USAGE)))
    c = client(tmp_path, t)
    tools = [{"type": "function", "name": "segment", "description": "Outline a part",
              "parameters": {"type": "object", "properties": {"concept": {"type": "string"}}}}]
    r = await c.respond("guide", "where is the filter?", tools=tools, lane="l7")
    assert [(f.name, f.args()) for f in r.function_calls] == [("segment", {"concept": "filter cap"})]
    assert json.loads(t.sent[0].content)["tools"][0]["function"]["name"] == "segment"


async def test_a_partner_error_is_a_failed_call(tmp_path: Path) -> None:
    t = Partner(httpx.Response(429, json={"error": "rate limited"}))
    c = client(tmp_path, t)
    with pytest.raises(CallFailed, match="http_429"):
        await c.respond("judge", "is the filter in?", lane="l7")
    assert ledger(c)[0]["provider"] == "baseten"


async def test_sam_and_voice_stay_on_meta(tmp_path: Path) -> None:
    b = PartnerBackend(load_config().partners["baseten"])
    with pytest.raises(BackendUnavailable, match="stays on Meta"):
        async for _ in b.stream({}, sha="x", kind="sam_image"):
            pass
    with pytest.raises(BackendUnavailable, match="stays on Meta"):
        await b.transcribe({}, b"", sha="x")


def test_only_the_partner_roles_move() -> None:
    cfg = load_config().partner("fireworks")
    assert cfg.role("guide").model == cfg.role("judge").model == "accounts/fireworks/models/muse-spark-open"
    assert cfg.role("locate").model == cfg.role("plate").model == "muse-spark-1.3"
    assert cfg.role("sam").model == "sam-3.1"


def test_routing_is_off_until_the_go() -> None:
    cfg = load_config()
    assert cfg.routing is False and not hasattr(cfg, "local_overflow")
    assert cfg.partner_order == ("baseten", "fireworks", "modal") and cfg.partner_roles == ("guide", "judge")


def test_a_partner_cannot_take_sam(tmp_path: Path) -> None:
    from calls.config import ROOT

    text = (ROOT / "config" / "models.toml").read_text().replace('roles = ["guide", "judge"]', 'roles = ["sam"]')
    p = tmp_path / "models.toml"
    p.write_text(text)
    with pytest.raises(ConfigError, match="only"):
        load_config(p)


def test_make_client_builds_a_partner_pool(tmp_path: Path) -> None:
    (tmp_path / "budgets.toml").write_text("total_cap = 1.0\n[lanes]\nl7 = 1.0\n")
    c = calls.make_client("live", partner="baseten", private=tmp_path)
    assert c.provider == "baseten" and c.ledger_mode == "partner"
    assert c.config.role("judge").model == SPARK_OPEN
    with pytest.raises(ConfigError):
        calls.make_client("live", partner="baseten", local=True, private=tmp_path)

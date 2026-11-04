"""Responses bodies to OpenAI chat completions, and chat completion chunks back to Responses events: the local
backend (Glimmer through ollama) and the partner backend (Spark Open on Baseten, Fireworks or Modal) both speak chat
completions, while the client builds and reads Responses, so the loop, the ledger, record and replay stay the same.
"""

from __future__ import annotations

import base64
import io
import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from calls.errors import CallFailed

STOP_TOKENS = re.compile(r"<\|(?:eot|eom|eot_id|end_of_turn)\|>")


def strip_stop_tokens(text: str) -> str:
    return STOP_TOKENS.sub("", text)


# ---- Responses body -> chat completions ---------------------------------------------------------------------------


def _pdf_text(data_url: str, filename: str) -> str:
    from pypdf import PdfReader

    raw = base64.b64decode(data_url.split(",", 1)[1])
    pages = PdfReader(io.BytesIO(raw)).pages
    return "\n\n".join([f"[file {filename}]"] + [f"--- page {i} ---\n{p.extract_text() or ''}"
                                                   for i, p in enumerate(pages, 1)])


def _part(c: dict[str, Any]) -> dict[str, Any]:
    t = c.get("type")
    if t in ("input_text", "output_text", "text"):
        return {"type": "text", "text": str(c.get("text", ""))}
    if t == "input_image":
        return {"type": "image_url", "image_url": {"url": c["image_url"]}}
    if t == "input_file":
        data = str(c.get("file_data") or "")
        if not data.startswith("data:application/pdf;base64,"):
            raise CallFailed("a chat backend takes a manual as inline PDF file_data; an uploaded file_id is API-only")
        return {"type": "text", "text": _pdf_text(data, str(c.get("filename") or "manual.pdf"))}
    raise CallFailed(f"chat backend: unsupported content part {t!r}")


def _content(content: Any, role: str) -> Any:
    if isinstance(content, str):
        return content
    parts = [_part(c) for c in content or []]
    if role != "user" or all(p["type"] == "text" for p in parts):
        return "\n\n".join(p["text"] for p in parts if p["type"] == "text")
    return parts


def chat_messages(body: dict[str, Any]) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []
    if body.get("instructions"):
        msgs.append({"role": "system", "content": body["instructions"]})
    items = body["input"] if isinstance(body["input"], list) else [{"role": "user", "content": body["input"]}]
    for it in items:
        t = it.get("type", "message")
        if t == "message":
            role = {"developer": "system"}.get(it.get("role", "user"), it.get("role", "user"))
            msgs.append({"role": role, "content": _content(it.get("content"), role)})
        elif t == "function_call":
            call = {"id": it["call_id"], "type": "function",
                    "function": {"name": it["name"], "arguments": it.get("arguments") or "{}"}}
            if msgs and msgs[-1]["role"] == "assistant" and "tool_calls" in msgs[-1]:
                msgs[-1]["tool_calls"].append(call)
            else:
                msgs.append({"role": "assistant", "content": "", "tool_calls": [call]})
        elif t == "function_call_output":
            out = it.get("output")
            msgs.append({"role": "tool", "tool_call_id": it["call_id"],
                         "content": out if isinstance(out, str) else json.dumps(out)})
        elif t == "reasoning":
            continue  # Spark's encrypted reasoning means nothing to another model
        else:
            raise CallFailed(f"chat backend: unsupported input item {t!r}")
    return msgs


def chat_body(body: dict[str, Any], *, reasoning_effort: str = "none") -> dict[str, Any]:
    """Glimmer turns thinking off with "none"; Spark Open takes the role's effort (calls/backends/partner.py)."""
    out: dict[str, Any] = {
        "model": body["model"], "messages": chat_messages(body), "stream": True,
        "stream_options": {"include_usage": True}, "temperature": 0, "reasoning_effort": reasoning_effort,
        "max_tokens": body.get("max_output_tokens"),
    }
    tools = [{"type": "function", "function": {k: t[k] for k in ("name", "description", "parameters") if k in t}}
             for t in body.get("tools") or [] if t.get("type") == "function"]
    if tools:
        out["tools"] = tools
    fmt = (body.get("text") or {}).get("format")
    if fmt and fmt.get("type") == "json_schema":
        out["response_format"] = {"type": "json_schema", "json_schema": {
            "name": fmt.get("name", "out"), "schema": fmt["schema"], "strict": bool(fmt.get("strict", False))}}
    return out


# ---- chat completion chunks -> Responses events ---------------------------------------------------------------------


class ResponsesTranslator:
    """Feed it the chat.completion.chunk objects in order; it returns the Responses events each one implies."""

    def __init__(self, prefix: str = "local", *, strip_stop: bool = True) -> None:
        self.prefix = prefix
        self.strip_stop = strip_stop  # ollama leaks Glimmer's stop token into the content; partners do not
        self.rid = f"resp_{prefix}_" + uuid.uuid4().hex[:12]
        self.model: str | None = None
        self.text = ""
        self.calls: dict[int, dict[str, str]] = {}
        self.finish: str | None = None
        self.usage: dict[str, Any] = {}
        self.seq = 0
        self.started = False

    def _ev(self, **ev: Any) -> dict[str, Any]:
        ev["sequence_number"] = self.seq
        self.seq += 1
        return ev

    def feed(self, chunk: dict[str, Any]) -> list[dict[str, Any]]:
        evs: list[dict[str, Any]] = []
        if not self.started:
            self.started = True
            self.model = chunk.get("model")
            evs.append(self._ev(type="response.created",
                                response={"id": self.rid, "model": self.model, "status": "in_progress"}))
        if chunk.get("usage"):
            self.usage = chunk["usage"]
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            piece = str(delta.get("content") or "")
            if self.strip_stop:
                piece = strip_stop_tokens(piece)
            if piece:
                self.text += piece
                evs.append(self._ev(type="response.output_text.delta", item_id=f"msg_{self.prefix}", output_index=0,
                                    content_index=0, delta=piece))
            for tc in delta.get("tool_calls") or []:
                slot = self.calls.setdefault(int(tc.get("index", len(self.calls))),
                                             {"id": "", "name": "", "arguments": ""})
                fn = tc.get("function") or {}
                slot["id"] = tc.get("id") or slot["id"]
                slot["name"] = fn.get("name") or slot["name"]
                args = fn.get("arguments")
                if args:
                    args = args if isinstance(args, str) else json.dumps(args)
                    slot["arguments"] += args
                    evs.append(self._ev(type="response.function_call_arguments.delta",
                                        item_id=f"fc_{self.prefix}_{tc.get('index', 0)}", delta=args))
            if choice.get("finish_reason"):
                self.finish = choice["finish_reason"]
        return evs

    def final(self) -> dict[str, Any]:
        output: list[dict[str, Any]] = []
        for i in sorted(self.calls):
            c = self.calls[i]
            output.append({"id": f"fc_{self.prefix}_{i}", "type": "function_call", "status": "completed",
                           "name": c["name"], "call_id": c["id"] or f"call_{self.prefix}_{i}",
                           "arguments": c["arguments"] or "{}"})
        text = self.text.strip()
        if text:
            output.append({"id": f"msg_{self.prefix}", "type": "message", "role": "assistant", "status": "completed",
                           "content": [{"type": "output_text", "text": text, "annotations": []}]})
        status = "incomplete" if self.finish == "length" else "completed"
        resp: dict[str, Any] = {
            "id": self.rid, "model": self.model, "status": status, "output": output,
            "usage": {"input_tokens": int(self.usage.get("prompt_tokens") or 0),
                      "input_tokens_details": {"cached_tokens": int(
                          (self.usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)},
                      "output_tokens": int(self.usage.get("completion_tokens") or 0),
                      "output_tokens_details": {"reasoning_tokens": int(
                          (self.usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0)}},
        }
        if status == "incomplete":
            resp["incomplete_details"] = {"reason": "max_output_tokens"}
        return self._ev(type=f"response.{status}", response=resp)


async def sse(resp: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    async for line in resp.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        yield json.loads(data)

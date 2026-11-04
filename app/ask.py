"""One question through the production guide turn, for the eval's whole-context row (eval/wholectx.py).

    await app.ask.answer(card, question, lang="en", whole_context=True | False, history=[HistoryEntry...],
                         manuals=..., client=..., lane="l6")
    -> {"text", "cites": [{"page", "quote", "verified"}], "usd", "latency_ms",
        "tool_calls": [{"name", "query", "pages"}]}

It is a Session with the card and no job, its guide built as a session's is (`whole_context=False` is the retrieval
arm: the card and the history stay in the prefix, the manual is reached only through lookup_manual), and the
history rendered by the same code as StoreHooks.history_text. `text` is what the user would see: the sentences that
passed G0, in order. `cites` are the guide's cite tool results; `tool_calls` record every tool the turn called, with
lookup_manual's query and the pages it found, for the failure write-ups. Nothing is recorded to disk (no record_root):
the replies hold manual text, so their fixtures stay where the caller's client keeps them.
"""

from __future__ import annotations

import json
import time
from typing import Any

import calls
from app.guide import Guide, render_history
from app.ports import Manuals
from app.session import NoHooks, Session
from contracts.models.card import Card


class _History(NoHooks):
    def __init__(self, entries: list[dict[str, Any]]) -> None:
        self.entries = entries

    def history_text(self, s: Session) -> str | None:
        return render_history(self.entries)


async def answer(card: Card, question: str, *, lang: str = "en", whole_context: bool = True,
                 history: list[dict[str, Any]] | None = None, manuals: Manuals | None = None,
                 client: calls.Client | None = None, lane: str = "l6", country: str = "nl") -> dict[str, Any]:
    s = Session(session_id=f"ask-{lane}", client=client or calls.default_client(), lang=lang, country=country,
                card=card, manuals=manuals, hooks=_History(list(history or [])))
    s.models.lane = lane
    said: list[str] = []

    async def sink(m: dict[str, Any]) -> None:
        if m["type"] == "sentence":
            said.append(m["text"])

    s.sinks.append(sink)
    g = Guide(s, whole_context=whole_context)
    s.guide = g
    cites: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    call = g.call

    async def noted(name: str, arguments: str) -> dict[str, Any]:
        out = await call(name, arguments)
        try:
            args = json.loads(arguments or "{}")
        except ValueError:
            args = {}
        pages = [h["page"] for h in out.get("hits", [])] if name == "lookup_manual" else \
            [out["page"]] if "page" in out else []
        tool_calls.append({"name": name, "query": args.get("query") if isinstance(args, dict) else None,
                           "pages": pages})
        if name == "cite" and "page" in out:
            cites.append({"page": out["page"], "quote": out.get("quote", ""), "verified": bool(out.get("verified"))})
        return out

    g.call = noted  # type: ignore[method-assign]
    t0 = time.perf_counter()
    await g.turn(question)
    return {"text": " ".join(said), "cites": cites, "usd": round(s.models.usd, 6),
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "tool_calls": tool_calls}

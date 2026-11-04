"""The guide: Muse Spark 1.3 in a tool-calling loop. It decides which part to outline next and what to say; code
decides everything that matters (G0 on every sentence it writes, the verdict, the safety state).

The request is a cached prefix and then the session:
- prefix, built once and byte-identical on every turn: instructions (agent/prompt.md) → the eight tools → the card →
  the manual pages and the household's history for this appliance;
- then the turns, replayed statelessly (`store: false`, reasoning carried as encrypted content): the user's words
  and code's events, the model's output items, the tool results.
A camera frame rides only in the newest user message, attached when each request is built, and is never replayed.

At most GUIDE_MAX_ROUNDS model calls a turn; the same tool call three times in a turn ends it (doom loop). The model
can raise a stop through report_hazard and has no tool that clears one.
"""

from __future__ import annotations

import json
import re
from functools import cache
from typing import TYPE_CHECKING, Any, get_args

from pydantic import BaseModel, ValidationError

import calls
from app import config, locate, trace
from app.frames import StoredFrame
from app.ports import pages_of
from app.spoken import in_lang, plain, split, spoken
from calls.images import prepare
from contracts import cite_args_invariants, schema
from contracts.models import tools as T

if TYPE_CHECKING:
    from app.session import Session

TOOL_ARGS: dict[str, type[BaseModel]] = {
    "lookup_manual": T.LookupManualArgs, "locate": T.LocateArgs, "segment": T.SegmentArgs, "cite": T.CiteArgs,
    "check_step": T.CheckStepArgs, "report_hazard": T.ReportHazardArgs, "ask_for_view": T.AskForViewArgs,
    "ha_state": T.HaStateArgs,
}
ASK_VIEW: dict[str, dict[str, str]] = {
    "en": {"closer": "Move the camera closer to the part, please.",
           "side": "Show me the side view, please.",
           "light": "It's too dark to see. Turn on a light or use the torch, please."},
    "nl": {"closer": "Breng de camera wat dichter bij het onderdeel.",
           "side": "Laat me de zijkant zien.",
           "light": "Het is te donker om iets te zien. Doe een lamp aan of gebruik de zaklamp."},
}
VIEW_POLL_S = 0.5  # ask_for_view waits for a new frame in slices, so a step that passes meanwhile ends the wait
STUCK = {"en": "I'm going round in circles. Tap done when the step is finished, or ask me again.",
         "nl": "Ik kom er niet uit. Tik op Klaar als de stap af is, of vraag het me nog eens."}
NO_ANSWER = {"en": "I couldn't answer that just now. Ask me again, or follow the step on the screen.",
             "nl": "Daar heb ik nu geen antwoord op. Vraag het nog eens, of volg de stap op het scherm."}


def _inline(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        if "$ref" in node:
            return _inline(defs[node["$ref"].rsplit("/", 1)[1]], defs)
        return {k: _inline(v, defs) for k, v in node.items() if k not in ("default",)}
    if isinstance(node, list):
        return [_inline(x, defs) for x in node]
    return node


@cache
def tools() -> tuple[dict[str, Any], ...]:
    """The eight tools as Responses function tools, in the schema's order, with every $ref inlined."""
    defs = schema("tools")["$defs"]
    out = []
    for name in defs["ToolName"]["enum"]:
        args = defs[f"{''.join(p.title() for p in name.split('_'))}Args"]
        params = _inline({k: v for k, v in args.items() if k != "description"}, defs)
        out.append({"type": "function", "name": name, "description": args["description"], "parameters": params,
                    "strict": True})
    return tuple(out)


def instructions() -> str:
    return config.PROMPT_PATH.read_text(encoding="utf-8")


def _text(role: str, text: str) -> dict[str, Any]:
    kind = "output_text" if role == "assistant" else "input_text"
    return {"type": "message", "role": role, "content": [{"type": kind, "text": text}]}


HISTORY_LIMIT = 50


def render_history(entries: list[dict[str, Any]], limit: int = HISTORY_LIMIT) -> str | None:
    """The household's history for one appliance (api HistoryEntry rows, newest first), as the guide's prefix holds
    it: one line per entry, its id to cite. Stop entries hold a rule id and evidence, never a held sentence."""
    lines = []
    for e in entries[:limit]:
        line = f"[entry {e.get('id', '?')}] {e['on_date']} {e['kind']}"
        if e.get("job_id"):
            line += f" (job {e['job_id']})"
        if e.get("text"):
            line += f": {e['text']}"
        lines.append(line)
    return "\n".join(lines) or None


def _nulls(model: type[BaseModel], raw: Any) -> Any:
    """Strict decoding has sent the string "null" (or "") for a JSON null: in a field that takes null, it is null."""
    if not isinstance(raw, dict):
        return raw
    nullable = {k for k, f in model.model_fields.items() if type(None) in get_args(f.annotation)}
    return {k: None if k in nullable and v in ("null", "") else v for k, v in raw.items()}


class Guide:
    def __init__(self, s: Session, *, whole_context: bool = True) -> None:
        """`whole_context`: every manual page in the prefix (api mode). False is the retrieval arm of the eval's
        whole-context row: the guide then reads pages only through lookup_manual."""
        self.s = s
        self.whole_context = whole_context
        self.answered = False  # this turn said something to the user
        self.pending = ""  # this turn's text written beside a tool call: said only if the turn says nothing else
        self.cited_page: int | None = None  # the manual page this turn cited, where a long table is pointed to
        self.prefix: list[dict[str, Any]] = self._prefix()
        self.turns: list[dict[str, Any]] = []
        self.rounds = 0
        self.asked_view = False  # this turn asked the person for a new view (the session then waits on them)
        self.step_at: str | None = None  # the step current when this turn began: its words are about that step
        self.view_said_for: str | None = None  # the check whose verdict's ask for a view stood in for ours

    # ---- the prefix --------------------------------------------------------------------------------------------

    def _prefix(self) -> list[dict[str, Any]]:
        s = self.s
        card = s.card.model_dump(mode="json", exclude_none=True, exclude={"checks", "manual"}) if s.card else {}
        items = [_text("developer", "The repair card (the only steps you may give):\n"
                       + json.dumps(card, ensure_ascii=False, sort_keys=True))]
        pages = pages_of(s.manuals, s.card.brand, s.card.model, s.manual_household) if s.manuals and s.card else []
        if self.whole_context and pages:
            body = "\n\n".join(f"[page {p.page}]\n{p.text}" for p in pages)
            items.append(_text("developer", f"The manual, page by page:\n{body}"))
        history = s.hooks.history_text(s) if hasattr(s.hooks, "history_text") else None
        if history:
            items.append(_text("developer", "This appliance's history in this home, newest first (cite an entry as "
                                            f"[entry N]):\n{history}"))
        return items

    # ---- one turn ----------------------------------------------------------------------------------------------

    def request(self, frame: StoredFrame | None) -> list[dict[str, Any]]:
        """The prefix, then the turns with the newest frame in the newest user message only."""
        turns = list(self.turns)
        if frame is not None:
            last = max((i for i, t in enumerate(turns) if t.get("role") == "user"), default=None)
            if last is not None:
                msg = turns[last]
                img = prepare(frame.jpeg, 1024)
                turns[last] = {**msg, "content": [*msg["content"],
                                                  {"type": "input_text", "text": f"Camera frame {frame.frame_id}:"},
                                                  {"type": "input_image", "image_url": img.data_url(),
                                                   "detail": "high"}]}
        return [*self.prefix, *turns]

    def _user(self, text: str | None) -> dict[str, Any]:
        s = self.s
        lines = []
        if s.events:
            lines.append("Events since your last turn:\n" + "\n".join(f"- {e}" for e in s.events))
            s.events.clear()
        st = s.safety.state
        lines.append(f"Safety state: {st.state}" + (f" ({st.precondition})" if hasattr(st, "precondition") else ""))
        if s.run is not None:
            lines.append(f"Current step: {s.run.step.id} ({s.run.index + 1}), phase {s.phase}")
        if text:
            lines.append(f"The user said: {text}")
        return _text("user", "\n".join(lines))

    async def turn(self, user_text: str | None) -> None:
        """One guide turn. A question always gets an answer: when the turn ends without one (a failed or timed-out
        call, or every round spent on tools), a fixed line says so."""
        self.answered, self.pending, self.cited_page = False, "", None
        self.step_at = self.s.run.step.id if self.s.run is not None else None
        await self._turn(user_text)
        if self.pending and not self.answered and not self.s.stopped:
            await self._say(self.pending)
        if user_text is not None and not self.answered and not self.s.stopped:
            await self.s.say(in_lang(NO_ANSWER, self.s.lang), step_id=self.step_at)

    def moved_on(self) -> bool:
        """Has the job left the step this turn began on (a check passed, the person confirmed, a stop)?"""
        run = self.s.run
        return self.step_at is not None and (run is None or run.step.id != self.step_at or self.s.phase == "job_done")

    async def _say(self, text: str) -> None:
        """The model's text as the user gets it (app.spoken: plain, no talk about its tools, short), through G0. A
        reply about a step the job has left is dropped: under the next step it would read as that step's words."""
        said = spoken(text, self.cited_page, lang=self.s.lang)
        if said and self.moved_on():
            self.answered = True  # nothing more is owed on a step that is over
            self.s.rec.state(self.s.clock(), "guide_dropped", step_id=self.step_at, sentences=len(split(said)))
            await self.s.rail("guide", "", "the step moved on: reply dropped", 0)
            return
        if said:
            self.answered = True
            await self.s.say(said, step_id=self.step_at)

    async def _turn(self, user_text: str | None) -> None:
        s = self.s
        self.turns.append(self._user(user_text))
        seen: dict[str, int] = {}
        for _ in range(config.GUIDE_MAX_ROUNDS):
            if s.stopped and user_text is None or self.moved_on():
                return
            self.rounds += 1
            frame = s.pick_frame("guide_frame", s.frames.latest())
            try:
                r, ms = await s.models.respond("guide", self.request(frame), tools=list(tools()),
                                               timeout_s=config.GUIDE_TIMEOUT_S)
            except calls.CallTimeout:
                await s.slow("slow_answer")
                return
            except calls.CallError as e:
                await s.rail("guide", "", trace.failed(e), 0, error=type(e).__name__)
                return
            if r.reasoning_summary:  # model text on screen: plain, and through G0 like any sentence
                summary = await s.gate(plain(r.reasoning_summary), step_id=self.step_at)
                if summary:
                    await s.rail("reasoning", "", summary, ms)
            self.turns.extend(_replayable(r.output))
            if r.text.strip() and r.function_calls:
                self.pending = r.text  # "Let me check the manual.": the answer comes in a later round
            elif r.text.strip():
                await self._say(r.text)
            if not r.function_calls:
                return
            for fc in r.function_calls:
                key = f"{fc.name}:{fc.arguments}"
                seen[key] = seen.get(key, 0) + 1
                if seen[key] >= config.DOOM_REPEATS:
                    self.turns.append(_output(fc.call_id, {"error": "repeated call", "retry": False}))
                    await s.rail("guide", "", "going round in circles", 0, call=fc.name)
                    self.answered = True
                    await s.say(in_lang(STUCK, s.lang), step_id=self.step_at)
                    return
                result = await self.call(fc.name, fc.arguments)
                self.turns.append(_output(fc.call_id, result))
                if fc.name == "report_hazard":
                    self.answered = True
                    return  # the stop script is said; nothing more this turn

    # ---- the tools -----------------------------------------------------------------------------------------------

    async def call(self, name: str, arguments: str) -> dict[str, Any]:
        model = TOOL_ARGS.get(name)
        if model is None:
            return {"error": f"no tool {name}", "retry": False}
        try:
            args = model.model_validate(_nulls(model, json.loads(arguments or "{}")))
        except (ValidationError, ValueError) as e:
            return {"error": f"bad arguments: {str(e)[:150]}", "retry": True}
        fn = getattr(self, f"_{name}")
        try:
            out: dict[str, Any] = await fn(args)
        except calls.CallTimeout:
            await self.s.slow("slow_outline" if name in ("locate", "segment") else "slow_answer")
            out = {"error": "timeout", "retry": True}
        except calls.CallError as e:
            out = {"error": type(e).__name__, "retry": False}
        await self.s.rail(name, self._what(name, args), trace.result(out, name), 0, args=_short(args),
                          result=_short_result(out))
        return out

    def _what(self, name: str, a: BaseModel) -> str:
        """The tool's argument in words: a part's name, the step, the query, the page; never an id."""
        s = self.s
        part_id = getattr(a, "part_id", None)
        if part_id and (p := s.part(part_id)) is not None:
            return trace.name(p.names, s.lang)
        if name == "lookup_manual":
            return str(getattr(a, "query", ""))
        if name == "cite":
            return f"page {a.page}" if getattr(a, "page", None) else "the step" if getattr(a, "step_id", None) else ""
        if name == "check_step" and s.run is not None:
            return trace.step(s.run.index)
        if name == "ask_for_view":
            return {"closer": "closer", "side": "from the side", "light": "more light"}.get(str(a.kind), "")
        return ""

    def _frame(self, frame_id: str | None) -> StoredFrame | None:
        f = self.s.frames.get(frame_id) if frame_id else None
        return f or self.s.pick_frame("latest", self.s.frames.latest())

    async def _lookup_manual(self, a: T.LookupManualArgs) -> dict[str, Any]:
        s = self.s
        pages = pages_of(s.manuals, s.card.brand, s.card.model, s.manual_household) if s.manuals and s.card else []
        words = [w for w in re.findall(r"\w+", a.query.lower()) if len(w) > 2]
        scored = []
        for p in pages:
            low = p.text.lower()
            score = sum(low.count(w) for w in words)
            if score:
                i = min((low.find(w) for w in words if w in low), default=0)
                scored.append((score, p.page, p.text[max(0, i - 80):i + 220].strip()))
        hits = [{"page": pg, "snippet": snip[:300]} for _, pg, snip in sorted(scored, reverse=True)[:5]]
        if not pages:
            await s.notice("lookup_failed")  # no manual text to search: the card's quotes are all there is
        return {"hits": hits}

    async def _locate(self, a: T.LocateArgs) -> dict[str, Any]:
        s = self.s
        part, f = s.part(a.part_id), self._frame(a.frame_id)
        if part is None or f is None:
            return {"error": "unknown part" if part is None else "no frame", "retry": False}
        found = await locate.locate(s.models, part, f, s.lang)
        if found.error == "timeout":
            await s.slow("slow_outline")
        box = list(found.box) if found.found and found.box else None
        out: dict[str, Any] = {"found": box is not None, "box": box, "frame_id": f.frame_id}
        if box is None:
            out["note"] = "no box: ask for a better view; nothing was drawn"
        return out

    async def _segment(self, a: T.SegmentArgs) -> dict[str, Any]:
        s = self.s
        f = self._frame(a.frame_id)
        if f is None:
            return {"error": "no frame", "retry": True}
        if a.part_id:
            if s.part(a.part_id) is None:
                return {"error": "unknown part", "retry": False}
            out = await s.outline_part(a.part_id, f)
        elif a.text:
            out = await locate.segment(s.models, f, box=_box(a.box), text=a.text)
        else:
            return {"error": "give part_id or text", "retry": True}
        r = out.region if out else None
        return {"found": r is not None, "count": out.count if out else 0, "mask_svg": r.svg() if r else None,
                "box": list(r.box) if r else None, "conf": r.conf if r else None, "frame_id": f.frame_id}

    async def _cite(self, a: T.CiteArgs) -> dict[str, Any]:
        """A card step's citation (its quote, checked with the card), or the manual's own words on a page: those are
        checked against the manual's pages here, and gated like any sentence, before they go anywhere."""
        s = self.s
        problems = cite_args_invariants(a)
        if problems:
            return {"error": problems[0], "retry": True}
        if a.step_id is None:
            return await self._cite_manual(a.page or 0, a.quote or "")
        step = next((st for j in s.card.jobs for st in j.steps if st.id == a.step_id), None) if s.card else None
        if step is None:
            return {"error": "unknown step", "retry": False}
        if step.quote is None:  # our own step (origin fixit): the manual has no words for it, so there is no cite
            return {"step_id": step.id, "verified": False, "origin": "fixit", "quote": "",
                    "reason": "our own step, not from the manual: it has no page or quote"}
        cite = s.card.checks.citation if s.card and s.card.checks else None
        verified = cite is not None and cite.status == "pass"
        quote = await s.gate(step.quote.text, step_id=step.id)
        if quote is None:
            return {"step_id": step.id, "verified": False, "page": step.quote.page, "quote": "",
                    "reason": "the quote is held by the safety rules"}
        self.cited_page = step.quote.page
        return {"step_id": step.id, "verified": verified, "page": step.quote.page, "quote": quote}

    async def _cite_manual(self, page: int, quote: str) -> dict[str, Any]:
        s = self.s
        pages = pages_of(s.manuals, s.card.brand, s.card.model, s.manual_household) if s.manuals and s.card else []
        if not pages:
            return {"verified": False, "page": page, "quote": "", "reason": "no manual pages to check the quote on"}
        chk = s.manuals.verify(pages, page, quote)  # type: ignore[union-attr]  # pages implies manuals
        said = await s.gate(quote, step_id=self.step_at)
        if said is None:
            return {"verified": False, "page": chk.page, "quote": "", "reason": "the quote is held by the safety rules"}
        out: dict[str, Any] = {"verified": chk.verified, "page": chk.page, "quote": said}
        if chk.verified:
            self.cited_page = chk.page
        if chk.reason:
            out["reason"] = chk.reason
        return out

    async def _check_step(self, a: T.CheckStepArgs) -> dict[str, Any]:
        s = self.s
        if s.run is None or s.run.step.id != a.step_id:
            return {"error": "that is not the current step", "retry": False}
        if s.phase != "instructed":
            return {"error": f"cannot check now ({s.phase})", "retry": False}
        if s.unchecked(s.run.step):
            return {"error": "the camera does not check this step: the person confirms it with \"I did it,"
                             " continue\"", "retry": False}
        s.queue_check("the guide asked")
        return {"step_id": a.step_id, "pending": True}

    async def _report_hazard(self, a: T.ReportHazardArgs) -> dict[str, Any]:
        await self.s.report_hazard(a.rule_id.root if a.rule_id else None, a.evidence)  # which says the script
        return {"accepted": True, "state": "STOP"}

    async def _ask_for_view(self, a: T.AskForViewArgs) -> dict[str, Any]:
        s = self.s
        self.asked_view = True
        before = s.frames.latest()
        last = s.run.last_verdict if s.run is not None else None
        if last is not None and last.ask_for == a.kind and last.check_id != self.view_said_for:
            self.view_said_for = last.check_id  # that verdict already asks for this view on screen: once is enough
        elif not self.moved_on():  # an ask about a step the job has left would read as the next step's
            await s.say(in_lang(ASK_VIEW, s.lang)[a.kind], step_id=self.step_at)
        if s.models.rerunning:
            fid = s.models.pick("view_frame", None)
            return {"new_frame": fid is not None, "waited_s": 0.0, "frame_id": fid}
        t0 = s.clock()
        f = None
        while f is None and not self.moved_on() and s.clock() - t0 < config.ASK_FOR_VIEW_WAIT_S * 1000:
            f = await s.frames.next_after(before.frame_id if before else None, VIEW_POLL_S)
        fid = s.models.pick("view_frame", f.frame_id if f else None)
        return {"new_frame": f is not None, "waited_s": round((s.clock() - t0) / 1000, 1), "frame_id": fid}

    async def _ha_state(self, a: T.HaStateArgs) -> dict[str, Any]:
        s = self.s
        if s.ha is None:
            return {"alias": a.alias, "available": False, "entities": []}
        ok, ents = await s.ha.entities(a.alias)
        keep = ("entity_id", "state", "unit", "last_changed")
        rows = [{k: v for k, v in (e if isinstance(e, dict) else e.__dict__).items() if k in keep and v is not None}
                for e in ents]
        return {"alias": a.alias, "available": ok, "entities": rows}


def _replayable(output: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The model's output items as the next request's input: reasoning (encrypted), messages, function calls."""
    keep = []
    for item in output:
        if item.get("type") in ("reasoning", "message", "function_call"):
            keep.append({k: v for k, v in item.items() if k not in ("status",)})
    return keep


def _output(call_id: str, result: dict[str, Any]) -> dict[str, Any]:
    return {"type": "function_call_output", "call_id": call_id, "output": json.dumps(result, ensure_ascii=False)}


def _box(b: Any) -> tuple[int, int, int, int] | None:
    if b is None:
        return None
    v = [x.root if hasattr(x, "root") else x for x in (b.root if hasattr(b, "root") else b)]
    return (int(v[0]), int(v[1]), int(v[2]), int(v[3]))


def _short(args: BaseModel) -> str:
    return ", ".join(f"{k}={v}" for k, v in args.model_dump(exclude_none=True).items())[:80]


def _short_result(out: dict[str, Any]) -> str:
    if "error" in out:
        return f"error: {out['error']}"
    if "found" in out:
        return "found" if out["found"] else "not found"
    if "hits" in out:
        return f"{len(out['hits'])} hits"
    return ", ".join(f"{k}={v}" for k, v in out.items() if k not in ("quote", "entities"))[:80]

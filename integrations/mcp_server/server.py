"""Fix-It Lens over MCP: an agent asks what is due, what the manual says, and marks a job done or snoozes it.

Each tool is one REST call (contracts/mcp_tools.json) made with the caller's own token, so an agent can do exactly
what that token can over curl, and every change it makes is logged as the token's ("agent", its label). No tool
reaches a camera session, a repair step or a card, and no model runs here: the manual's answer is the checked card's
own quotes, picked by matching words.

Two ways in:
- streamable HTTP at /mcp on the app's own door (deploy/serve.py), with the household token or an issued one as the
  bearer (the README's `.mcp.json` reads it from the environment, never from a command line);
- stdio, for a local agent: `uv run python -m integrations.mcp_server --origin https://<host> --token-file <path>`.
A read-only token sees the read tools only.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import date
from typing import Annotated, Any, Literal

import httpx
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

UNDO_BODY = {"own": True}  # contracts/mcp_tools.json pins it: an agent never undoes a person's change
WRITE_TOOLS = frozenset({"mark_done", "snooze", "set_schedule", "undo_my_change"})
INSTRUCTIONS = (
    "Fix-It Lens keeps a home's appliance upkeep: what is due, from the maker's manual. Dates and intervals come "
    "from the manual or from the household; never invent one. Quote the manual with its page. A snooze only moves "
    "a date later; undo_my_change takes back this agent's own last change to a job. To do a repair, give the "
    "person start_repair's link: the steps are shown in the app, with the "
    "camera, never in a chat."
)

Headers = Mapping[str, str] | None  # the MCP request's HTTP headers; None on stdio
Rest = Callable[[Headers], httpx.AsyncClient]
Origin = Callable[[Headers], str]
ScopeOf = Callable[[Headers], Literal["read", "write"] | None]

ApplianceId = Annotated[str, Field(description="The appliance's id, from whats_due")]
JobId = Annotated[str, Field(description="The job's id, from whats_due")]


def _error(r: httpx.Response) -> str | None:
    """The Error body's code: {error} at the top (the app's door), or inside {detail} (a bare router)."""
    try:
        body = r.json()
        e = body.get("error") or (body.get("detail") or {}).get("error")
    except (ValueError, AttributeError):
        return None
    return e if isinstance(e, str) else None


def _say(r: httpx.Response) -> Any:
    if r.status_code < 400:
        return None if r.status_code == 204 else r.json()
    try:
        detail = r.json().get("detail")
    except ValueError:
        detail = None
    if r.status_code == 401:
        raise ToolError("This token is not accepted (revoked, or not this home's). Ask the home's admin for a new one.")
    if r.status_code == 403 and _error(r) == "person_only":
        raise ToolError("The manual gives no interval for this job, so only a person in the app can set its schedule. "
                        "Tell them; do not suggest an interval.")
    if r.status_code == 409 and _error(r) == "not_own":
        raise ToolError("The job's latest change was not made with this key (a person, Home Assistant or another key "
                        "changed it since), so nothing was undone. Tell the person; they can undo it in the app.")
    if r.status_code == 403:
        raise ToolError("This token can only read. Ask the home's admin for a write token to change the schedule.")
    if r.status_code == 404:
        raise ToolError(f"{detail or 'Not found'}. Call whats_due for this home's appliances and jobs.")
    raise ToolError(str(detail) if isinstance(detail, str) else f"The server said {r.status_code}.")


REASON_TEXT = {
    "scheduled": "Due by the interval, counted from when it was last done.",
    "never": "The household says it was never done, so it is due now.",
    "unknown": "The household does not know when it was last done, so it is due now.",
    "unanswered": "Nobody has said when it was last done, so there is no date yet. Ask the person; mark_done with "
                  "that day gives it one.",
    "cycle": "The manual counts uses, not time, so there is no date until the household sets its own schedule.",
}


def _item(d: dict[str, Any]) -> dict[str, Any]:
    """A Due, as an agent needs it: what, when, why, and the manual's line (or that it gives none). due_on is always
    there, null when the job has no date, with reason_text saying why."""
    out = {k: d.get(k) for k in ("appliance_id", "alias", "job_id", "title", "due_on", "status", "reason",
                                 "window_end", "snoozed_until", "schedule", "manual", "last_done")}
    out = {k: v for k, v in out.items() if v is not None or k == "due_on"}
    if d.get("reason") in REASON_TEXT:
        out["reason_text"] = REASON_TEXT[d["reason"]]
    if d.get("manual") is None:
        out["note"] = "The manual gives no interval for this job; the schedule is the household's own choice."
    return out


_WORD = re.compile(r"[a-zà-ÿ0-9]+")
_STOP = frozenset({"the", "and", "how", "what", "when", "does", "you", "for", "with", "should", "often", "can", "this",
                   "that", "your", "about"})


def _words(text: str | None) -> set[str]:
    """The words that carry meaning, a plural or third-person -s dropped ("takes" meets "take")."""
    words = (w for w in _WORD.findall((text or "").lower()) if w not in _STOP and len(w) > 2)
    return {w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w for w in words}


def _cites(node: Any) -> set[str]:
    """Every quote a job cites: its quote_refs, and the text of the quotes its steps carry."""
    if isinstance(node, dict):
        out = {node["quote_ref"]} if isinstance(node.get("quote_ref"), str) else set()
        if isinstance(node.get("quote"), dict) and isinstance(node["quote"].get("text"), str):
            out.add(node["quote"]["text"])
        return out.union(*(_cites(v) for v in node.values()))
    if isinstance(node, list):
        return set().union(*(_cites(v) for v in node))
    return set()


def manual_says(card: dict[str, Any], *, job_id: str | None, question: str | None,
                limit: int = 5) -> list[dict[str, Any]]:
    """The card's verbatim quotes for a job and/or a question, best first: a quote the job cites (its interval, a step,
    a check) comes first, then by the words it shares with the question (in English or the manual's
    language). Code only; no quote is reworded."""
    jobs = {j["id"]: j for j in card.get("jobs", [])}
    cites = {jid: _cites(j) for jid, j in jobs.items()}
    want = _words(question or "")
    title_of = {j["id"]: (j.get("title") or {}).get("en", j["id"]) for j in jobs.values()}
    out: list[tuple[int, dict[str, Any]]] = []
    for q in card.get("quotes", []):
        owner = next((jid for jid, c in cites.items() if q["id"] in c or q["text"] in c), None)
        score = 0
        if job_id and owner == job_id:
            score += 100
        if want:
            score += 10 * len(want & (_words(q.get("en")) | _words(q["text"]) | _words(title_of.get(owner, ""))))
        if score == 0 and (job_id or want):
            continue
        out.append((score, {"quote": q["text"], "lang": q.get("lang"), "en": q.get("en"), "page": q.get("page"),
                            "job_id": owner, "job": title_of.get(owner) if owner else None}))
    out.sort(key=lambda s: -s[0])
    return [{k: v for k, v in q.items() if v is not None} for _, q in out[:limit]]


def job_steps(card: dict[str, Any], job_id: str) -> list[dict[str, Any]]:
    """The job's steps in the card's order, numbered from 1: the step in English, the manual's own line and its
    page."""
    job = next((j for j in card.get("jobs", []) if j["id"] == job_id), None)
    out = []
    for n, st in enumerate((job or {}).get("steps") or [], 1):
        q = st.get("quote") or {}
        out.append({k: v for k, v in {"n": n, "step": (st.get("text") or {}).get("en"), "quote": q.get("text"),
                                      "lang": q.get("lang"), "page": q.get("page") or st.get("page")}.items()
                    if v is not None})
    return out


def build(rest: Rest, origin: Origin, scope_of: ScopeOf | None = None) -> MCPServer:
    """The server. Given the MCP request's headers, `rest` is an httpx client for /api/v1 carrying the caller's
    token, `origin` the app's address for links, and `scope_of` the token's scope (the write tools are hidden from
    anything but "write")."""
    mcp = MCPServer("Fix-It Lens", instructions=INSTRUCTIONS, middleware=[_hide_writes(scope_of)] if scope_of else None)
    def tool(annotations: ToolAnnotations) -> Callable[[Any], Any]:
        """mcp.tool, with the docstring as one clean line of description."""
        return lambda f: mcp.tool(description=" ".join((f.__doc__ or "").split()), annotations=annotations)(f)

    read = ToolAnnotations(read_only_hint=True, open_world_hint=False)
    write = ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

    async def call(ctx: Context, method: str, path: str, **kw: Any) -> Any:
        async with rest(ctx.headers) as c:
            return _say(await c.request(method, f"/api/v1{path}", **kw))

    async def due(ctx: Context, **params: Any) -> list[dict[str, Any]]:
        """The home's Due list, each with its appliance's name."""
        items: list[dict[str, Any]] = await call(ctx, "GET", "/due", params=params)
        names = {a["id"]: a.get("alias") for a in await call(ctx, "GET", "/appliances")}
        return [{**d, "alias": names.get(d["appliance_id"])} for d in items]

    def job(items: list[dict[str, Any]], appliance_id: str, job_id: str) -> dict[str, Any]:
        for d in items:
            if d["appliance_id"] == appliance_id and d["job_id"] == job_id:
                return d
        raise ToolError("No such job in this home's schedule. Call whats_due for the appliances and jobs.")

    @tool(read)
    async def whats_due(ctx: Context, within_days: Annotated[int | None, Field(
            ge=0, description="Only what is due within this many days; leave out for everything")] = None,
    ) -> list[dict[str, Any]]:
        """Every upkeep job in the home, soonest first: due date, status (upcoming, due, overdue, or none when the
        household has not said when it was last done), the manual's interval with its page, and the household's own
        schedule if it set one. A job whose manual gives no interval is here only once a person set its schedule."""
        params = {} if within_days is None else {"within_days": within_days}
        return [_item(d) for d in await due(ctx, **params)]

    @tool(read)
    async def last_done(ctx: Context, appliance_id: ApplianceId, job_id: JobId) -> dict[str, Any]:
        """When the job was last done, and who said so (the household, or a checked repair session); null if the
        household has not said."""
        d = job(await due(ctx), appliance_id, job_id)
        return {"appliance_id": appliance_id, "job_id": job_id, "title": d["title"], "last_done": d.get("last_done")}

    @tool(read)
    async def what_does_the_manual_say(ctx: Context, appliance_id: ApplianceId,
                                       job_id: Annotated[str | None, Field(description="A job, from whats_due")] = None,
                                       question: Annotated[str | None, Field(
                                           description="Words to look for, e.g. 'filter cleaning'")] = None,
                                       ) -> dict[str, Any]:
        """The maker's own words from the checked manual, with the page: for a job, a question, or both. Quote them
        as given; the English line is a translation when the manual is in another language. For a job, its steps
        come numbered, in order."""
        if not job_id and not (question or "").strip():
            raise ToolError("Give a job_id or a question.")
        card = await call(ctx, "GET", f"/appliances/{appliance_id}/card")
        quotes = manual_says(card, job_id=job_id, question=question)
        steps = job_steps(card, job_id) if job_id else []
        m = card.get("manual") or {}
        out: dict[str, Any] = {"manual": {"title": m.get("title"), "lang": m.get("lang"),
                                          "source_url": m.get("source_url")}, "quotes": quotes}
        if steps:
            out["steps"] = steps
        out["note"] = ("Quote these as given, with the page. To do the job, give the person start_repair's link: the "
                       "app walks them through the steps with the camera." if quotes or steps else
                       "The checked manual has no line on that. Say so; do not guess.")
        return out

    @tool(write)
    async def mark_done(ctx: Context, appliance_id: ApplianceId, job_id: JobId,
                        on: Annotated[date | None, Field(
                            description="The day it was done; leave out for today")] = None,
                        ) -> dict[str, Any]:
        """Mark the job done (today, or a day in the past). The next date follows from the manual or the household's
        own schedule. If it was a mistake, undo_my_change takes it back (a person can also undo it in the app)."""
        body = {} if on is None else {"date": on.isoformat()}
        return _item(await call(ctx, "POST", f"/appliances/{appliance_id}/jobs/{job_id}/done", json=body))

    @tool(write)
    async def snooze(ctx: Context, appliance_id: ApplianceId, job_id: JobId,
                     until: Annotated[date | None, Field(description="The new due date, after the one it has now; "
                                                         "null clears the snooze")],
                     ) -> dict[str, Any]:
        """Move the job's due date later, to `until`. A snooze never moves a date earlier; the reminder waits too.
        Only a job with a due date can be snoozed: one nobody has said was last done has none (whats_due shows
        due_on null). until null clears a snooze."""
        if until is None:
            now = _item(job(await due(ctx), appliance_id, job_id))
            if "snoozed_until" not in now:
                return {**now, "note": "There was no snooze to clear; nothing changed."}
        body = {"until": until.isoformat() if until else None}
        return _item(await call(ctx, "PUT", f"/appliances/{appliance_id}/jobs/{job_id}/snooze", json=body))

    @tool(write)
    async def set_schedule(ctx: Context, appliance_id: ApplianceId, job_id: JobId,
                           kind: Annotated[Literal["every", "window", "manual"], Field(
                               description="every N units, a window from min to max units, or back to the manual")],
                           unit: Annotated[Literal["day", "week", "month", "year"] | None, Field(
                               description="For every and window; min and max share it")] = None,
                           every: Annotated[int | None, Field(ge=1, description="For every: how many units")] = None,
                           min: Annotated[int | None, Field(  # noqa: A002 (the contract's name)
                               ge=1, description="For window: due this many units after it was last done")] = None,
                           max: Annotated[int | None, Field(  # noqa: A002
                               ge=2, description="For window: overdue after this many units of the same unit")] = None,
                           ) -> dict[str, Any]:
        """The household's own schedule for a job, set only when the person asked for it: every N units, a window
        (due from min, overdue after max), or "manual" to follow the manual again. The manual's interval stays
        beside it. A job whose manual gives no interval is refused: only a person in the app sets that one."""
        body: dict[str, Any] = {"kind": kind}
        if kind != "manual":
            body |= {k: v for k, v in {"unit": unit, "every": every, "min": min, "max": max}.items() if v is not None}
        d = await call(ctx, "PUT", f"/appliances/{appliance_id}/jobs/{job_id}/schedule", json=body)
        if d is None:
            return {"appliance_id": appliance_id, "job_id": job_id, "due_on": None,
                    "note": "Back to the manual, which gives no interval for this job: it is off the schedule now."}
        out = _item(d)
        if out["due_on"] is None:
            out["note"] = ("The schedule is saved, but there is no date yet: nobody has said when the job was last "
                           "done. mark_done with that day gives it one.")
        return out

    @tool(write)
    async def undo_my_change(ctx: Context, appliance_id: ApplianceId, job_id: JobId) -> dict[str, Any]:
        """Take back this agent's own latest change to the job (a mark_done, snooze or set_schedule made with this
        same key), e.g. a job marked done by mistake. Refused, with nothing changed, if a person, Home Assistant or
        another key changed the job since."""
        d = await call(ctx, "POST", f"/appliances/{appliance_id}/jobs/{job_id}/undo", json=UNDO_BODY)
        if d is None:
            return {"appliance_id": appliance_id, "job_id": job_id, "due_on": None,
                    "note": "Undone. The job is off the schedule again: its manual gives no interval."}
        return {**_item(d), "note": "Undone; this is the job as it was before."}

    @tool(read)
    async def start_repair(ctx: Context, appliance_id: ApplianceId, job_id: JobId) -> dict[str, Any]:
        """A link that opens the job in the Fix-It Lens app, where the person starts it with the camera and the
        manual's steps. Give the person the link; nothing starts until they tap it."""
        d = job(await due(ctx), appliance_id, job_id)
        link = f"{origin(ctx.headers)}/#job/{appliance_id}/{job_id}"
        return {"title": d["title"], "alias": d.get("alias"), "link": link}

    return mcp


def _headers(ctx: Any) -> Headers:
    return getattr(ctx.request, "headers", None)


def _hide_writes(scope_of: ScopeOf) -> Any:
    async def middleware(ctx: Any, call_next: Any) -> Any:
        result = await call_next(ctx)
        if ctx.method == "tools/list" and isinstance(result, dict) and scope_of(_headers(ctx)) != "write":
            result = {**result, "tools": [t for t in result["tools"] if t.get("name") not in WRITE_TOOLS]}
        return result
    return middleware

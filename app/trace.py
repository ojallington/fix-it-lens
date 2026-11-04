"""Rail rows in plain words (polish finding 3). The rail is the phone's "how it thinks" line: the phone maps `tool`
to a verb ("Checked the step") and prints ": " + result_short, and the duration only when ms > 0. So result_short is
a short phrase a user can read, args_short names a part or a step in words or is empty, and neither ever holds an
id, a Python value (None), an exception or fixture name. The machine detail goes to the session record as a `trace`
state line (Session.rail's `detail`), where a re-run and test_m1 read it.
"""

from __future__ import annotations

from typing import Any

import calls

VERDICT = {"done": "done", "not_done": "not done yet", "cant_see": "can't see it yet"}


def failed(e: BaseException) -> str:
    if isinstance(e, calls.MissingFixture):
        return "no recording for this call"
    if isinstance(e, calls.CallTimeout):
        return "too slow, gave up"
    if isinstance(e, calls.BudgetExceeded):
        return "out of budget"
    return "the service didn't answer"


def name(names: Any, lang: str, fallback: str = "") -> str:
    """A card LangText (a part's names, a step's text) in the session's language, else any language it has."""
    d = names.root if hasattr(names, "root") else names
    if not isinstance(d, dict) or not d:
        return fallback
    return str(d.get(lang) or d.get("en") or next(iter(d.values())))


def step(index: int, of: int | None = None) -> str:
    return f"step {index + 1}" + (f" of {of}" if of else "")


def result(out: dict[str, Any], tool: str) -> str:
    """A guide tool's result as a phrase."""
    if "error" in out:
        return "couldn't do that" if out.get("error") != "timeout" else "too slow, gave up"
    if tool == "lookup_manual":
        pages = sorted({h["page"] for h in out.get("hits", [])})
        return "found it on page " + ", ".join(map(str, pages)) if pages else "nothing found"
    if tool == "cite":
        if out.get("origin") == "fixit":
            return "our own step, not from the manual"
        if not out.get("quote"):
            return "held back by the safety rules" if "safety" in str(out.get("reason", "")) else "no quote"
        where = f" on page {out['page']}" if out.get("page") else ""
        return ("matches the manual" if out.get("verified") else "not found in the manual") + where
    if tool == "locate":
        return "found it" if out.get("found") else "not in view"
    if tool == "segment":
        return "outlined it" if out.get("found") else "couldn't outline it"
    if tool == "check_step":
        return "checking now"
    if tool == "report_hazard":
        return "stopped the repair"
    if tool == "ask_for_view":
        return "got a new view" if out.get("new_frame") else "no new view yet"
    if tool == "ha_state":
        n = len(out.get("entities", []))
        return f"{n} sensor{'s' if n != 1 else ''}" if out.get("available") else "not connected"
    return "done"

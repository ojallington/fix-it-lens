"""A job's title is a plain task: "Clean the drain pump", "Reset the boiler (fault F004)". Never the model code,
the maker's name, or a note in brackets ("(new)", "(Loading door cannot be opened)"): the only bracket a title may
carry is a fix job's fault code, in the form the draft instructions give ("(fault E:32)", Dutch "(storing E:32)").

`problems` names what is wrong with a title; `clean` takes out what code can take out with certainty (the model
code, the maker, a bracketed note). Whether the rest reads as a task is checked on its first word in English.
"""

from __future__ import annotations

import re

# the bracket a fix job's title ends with, in the card's languages
_FAULT = re.compile(r"\((?:faults?|storing(?:en)?|fout(?:en)?|alarmcodes?|foutcodes?|störung(?:en)?|fehler|"
                    r"défauts?|erreurs?|codes?) [^()]+\)", re.IGNORECASE)
_BRACKET = re.compile(r"\s*\([^()]*\)")

# the verbs a task title starts with (English): a title that starts otherwise is a description, not a task
VERBS = frozenset(re.findall(r"\w+", """
    add adjust bleed change check clean clear close connect defrost descale drain dry empty fill fit fix free inspect
    level lubricate open operate plug put read redistribute reduce refill refit release remove replace reset restart
    rinse run set start switch take tighten top turn unblock unplug use wipe wash"""))


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _has_code(title: str, codes: list[str]) -> bool:
    """A model code or the maker's name in the title, however it is spaced or punctuated."""
    flat = _compact(title)
    return any(len(c) >= 3 and c in flat for c in map(_compact, codes))


def problems(title: str, *, model: str, brand: str, english: bool = True) -> list[str]:
    out = []
    if _has_code(title, [model, brand]):
        out.append("names the model or maker")
    notes = [m.group(0).strip() for m in _BRACKET.finditer(title) if not _FAULT.fullmatch(m.group(0).strip())]
    if notes:
        out.append("a note in brackets")
    plain = _BRACKET.sub("", title)
    if "/" in plain:
        out.append("two names joined by a slash")
    if english and (not plain.split() or plain.split()[0].lower() not in VERBS):
        out.append("does not start with a task verb")
    return out


def clean(title: str, *, model: str, brand: str) -> str:
    """The title without the model code, the maker or a bracketed note (a fault-code bracket stays)."""
    t = _BRACKET.sub(lambda m: m.group(0) if _FAULT.fullmatch(m.group(0).strip()) else "", title)
    for code in (model, brand):
        if len(_compact(code)) >= 3:
            pattern = r"[\s\-_/]*".join(re.escape(ch) for ch in _compact(code))
            t = re.sub(rf"\b{pattern}\b", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s{2,}", " ", t).strip(" -–:,")
    return t[:1].upper() + t[1:]

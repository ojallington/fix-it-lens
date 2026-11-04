"""The models a manual covers, as it prints them, and whether a plate's model is one of them (K1: the wrong card never
reaches an appliance). Code decides; the draft only lists what it read.

    listed = checked(draft["models_listed"], page_texts)   # ["SMV4HAX40N"], ["WMN..."], or []
    covers(listed, "SMV4HAX40N/17")                        # "exact" | "pattern" | None

A plate's model is matched after the plate's own normalisation (case and spacing), and a Bosch/Siemens E-Nr's
production suffix ("/17") is not part of the model. A pattern is the manual's own: a run of dots, "…" or "*" stands
for any characters ("WMN..." covers WMN16T3471), and needs 3 printed characters before it. A family name with no
code ("Xtreme") is not a model code and never matches.
"""

from __future__ import annotations

import re
from typing import Any, Literal

_ANY = re.compile(r"\.{2,}|…|\*")
MIN_PREFIX = 3
MAX_MODELS = 60


def _compact(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def _base(plate_model: str) -> str:
    """The model without a production suffix: "SMV4HAX40N/17" is the model SMV4HAX40N."""
    return _compact(plate_model.split("/")[0])


def is_code(code: str) -> bool:
    """A model or type code: letters and digits together, or the manual's own pattern with 3 printed characters."""
    literal = _compact(_ANY.split(code)[0]) if _ANY.search(code) else _compact(code)
    if _ANY.search(code):
        return len(literal) >= MIN_PREFIX
    return len(literal) >= 3 and bool(re.search(r"\d", literal)) and bool(re.search(r"[A-Z]", literal))


def checked(listed: list[dict[str, Any]], pages: list[str]) -> list[str]:
    """The listed codes that are model codes and printed on the page the draft cites (or, moved, on any page)."""
    flat = [_compact(t) for t in pages]
    out: list[str] = []
    for m in listed:
        code = re.sub(r"\s+", " ", str(m.get("code", "")).strip())[:40]
        if not is_code(code) or code in out:
            continue
        printed = _compact(_ANY.split(code)[0])
        page = int(m.get("page") or 0)
        if (1 <= page <= len(flat) and printed in flat[page - 1]) or any(printed in t for t in flat):
            out.append(code)
    return out[:MAX_MODELS]


def listing_page(listed: list[dict[str, Any]], kept: list[str], pages: list[str]) -> int | None:
    """The page that lists the kept codes (contracts' manual.models_page): the page the draft cites for most of them
    that prints the code, else the first page that prints the first one."""
    flat = [_compact(t) for t in pages]
    votes: dict[int, int] = {}
    for m in listed:
        code = re.sub(r"\s+", " ", str(m.get("code", "")).strip())[:40]
        page = int(m.get("page") or 0)
        if code in kept and 1 <= page <= len(flat) and _compact(_ANY.split(code)[0]) in flat[page - 1]:
            votes[page] = votes.get(page, 0) + 1
    if votes:
        return min(votes, key=lambda p: (-votes[p], p))
    if kept:
        first = _compact(_ANY.split(kept[0])[0])
        return next((n for n, t in enumerate(flat, 1) if first in t), None)
    return None


def covers(listed: list[str], plate_model: str) -> Literal["exact", "pattern"] | None:
    """Whether the manual's own list covers the plate's model: an exact code, one of its patterns, or not at all."""
    base, whole = _base(plate_model), _compact(plate_model)
    if not base:
        return None
    for code in listed:
        if not _ANY.search(code) and _compact(code) in (base, whole):
            return "exact"
    for code in listed:
        if _ANY.search(code):
            rx = "".join("[A-Z0-9]*" if _ANY.fullmatch(part) else re.escape(_compact(part))
                         for part in re.split(r"(\.{2,}|…|\*)", code) if part)
            if re.fullmatch(rx, base) and is_code(code):
                return "pattern"
    return None

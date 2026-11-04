"""Fault codes as the display shows them: "E:32-00", "F10", "Err C2", "F004". Never a word ("Waterafvoer"), a cut-off
symptom ("Product vibr"), a placeholder ("FXXX", "F-..") or a clock ("00:00").

The draft copies a code as the manual prints it. Code keeps it only if the manual prints it (on any page, however it
is spaced or punctuated) and it reads as a code, and spells out Bosch's alternating form: "E:36 / -25 / -26" is the
display alternating E:36 with -25 or -26, so the codes are E:36-25 and E:36-26.

A manual that draws its codes as display pictures (AEG's seven-segment "E20") has no text for code to check. Such a
code is kept on a fault row only when the row's own quote passed on its page and the code has display shape (a
letter first, letters and digits, at most 4): a drawn code, "read from the drawing, not text-checked". The check
record lists each one, and the card is shared only after a person confirms them.
"""

from __future__ import annotations

import re

MAX_LEN = 12  # contracts $defs.FaultCode
_ALTERNATING = re.compile(r"^([A-Za-z]{1,3}:?\d{1,4})((?:\s*/\s*-\d{1,3})+)\s*/?$")
_PLACEHOLDER = re.compile(r"\.\.|…|XX|\*|\?")
_WORD = re.compile(r"\b[^\W\d_]{4,}\b")


def _compact(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def spelled_out(code: str) -> list[str]:
    """One printed code as the display codes it stands for."""
    c = re.sub(r"\s+", " ", code.strip().strip('"“”'))
    if m := _ALTERNATING.match(c):
        return [f"{m.group(1)}{s}" for s in re.findall(r"-\d{1,3}", m.group(2))]
    return [c]


def problem(code: str) -> str | None:
    """Why a (spelled-out) code is not a code as a display shows it; None when it is one."""
    if _PLACEHOLDER.search(code):
        return "a placeholder, not a code"
    if not re.search(r"\d", code):
        return "no digit: not a code"
    if not re.search(r"[1-9A-Za-z]", code):
        return "only zeros: a clock, not a code"
    if _WORD.search(code):
        return "a word, not a code"
    if len(code) > MAX_LEN:
        return f"longer than {MAX_LEN} characters"
    return None


def display_shape(code: str) -> bool:
    """A code as a small display draws it: a letter first, then letters and digits, at most 4, with a digit."""
    flat = re.sub(r"[^A-Za-z0-9]", "", code)
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9]{1,3}", flat)) and bool(re.search(r"\d", flat))


def printed(code: str, pages: list[str]) -> bool:
    return _compact(code) in _compact(" ".join(pages))


def check(printed_codes: list[str], pages: list[str], *, drawn_ok: bool = False,
          drawn: frozenset[str] = frozenset()) -> tuple[list[str], list[tuple[str, str]]]:
    """The codes a job or a row keeps, in order and once each, and each one dropped with its reason. With `drawn_ok`
    (a row whose quote passed), a code the text does not print is kept when it has display shape; a code in `drawn`
    (one a passing row kept that way) is kept too."""
    text = _compact(" ".join(pages))
    kept: list[str] = []
    dropped: list[tuple[str, str]] = []
    for raw in printed_codes:
        if not raw.strip():
            continue
        if _compact(raw) not in text:
            code = re.sub(r"\s+", " ", raw.strip().strip('"“”'))
            if (drawn_ok and display_shape(code) and problem(code) is None) or code in drawn:
                if code not in kept:
                    kept.append(code)
            else:
                dropped.append((raw, "not printed in the manual"))
            continue
        for code in spelled_out(raw):
            if why := problem(code):
                dropped.append((code, why))
            elif code not in kept:
                kept.append(code)
    return kept, dropped

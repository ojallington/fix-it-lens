"""The citation check: is this quote really on this page? (The rule the design settled on.)

A quote is accepted when all three hold:
- `rapidfuzz.partial_ratio` of the normalised quote against the normalised page is at least 90 (85 on an OCR page);
- the aligned window of the page carries the same multiset of critical tokens as the quote (numbers, left/right,
  clockwise words, negations, open/close, first/before/after), because a flipped direction or a dropped "geen"
  still scores 91-92 on fuzzy match alone;
- the quote is at least 20 characters.

`check_quote` checks one page. `verify` checks the cited page and, on a miss, finds the page the quote is really on
(the "p42 → p43" correction). No model is involved: this runs on a contributor's machine with no key.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field

from rapidfuzz import fuzz

MIN_CHARS = 20
MIN_SCORE = 90.0
MIN_SCORE_OCR = 85.0

_CONTRACTIONS = re.compile(r"(\w)n['’]t\b", re.IGNORECASE)
_HYPHEN_BREAK = re.compile(r"(\w)[-‐‑]\s*\n\s*(\w)")
_NOT_WORD = re.compile(r"[^\w\s.,:]")
_SPACES = re.compile(r"\s+")
# Layout, not content: a step number at a line start ("5. Neem de zeef", "10.De onderste") and a figure's callout
# digits on lines of their own ("1\n2\na De pompafdekking…").
_LIST_NUMBER = re.compile(r"(?m)^[ \t]*\d{1,2}\.[ \t]*(?=[^\W\d])|^[ \t]*\d{1,2}[ \t]*$")
_LINE_START = re.compile(r"\s*\n\s*(?=[^\w\s]*[A-Z0-9])")  # a line break before a capital or digit
_RUNS = re.compile(r"[ \n]+")
_BOUNDARY = ".:\n"
MAX_EXTEND = 200
_CLOCKWISE = re.compile(r"\b(anti|counter)[\s-]*clockwise\b", re.IGNORECASE)

CRIT = re.compile(
    r"\b("
    r"\d+(?:[.,:]\d+)*"
    r"|links|rechts|left|right|linksom|rechtsom"
    r"|clockwise|anticlockwise|counterclockwise|tegen|mee"
    r"|nooit|altijd|niet|geen|never|always|not|no|none|nicht|kein\w*|nie"
    r"|open\w*|sluit\w*|dicht\w*|close\w*|clos\w*"
    r"|eerst|first|before|after|voordat|nadat|daarna|then"
    r")\b"
)


def norm(s: str, dehyph: bool = True, lines: bool = False) -> str:
    """Lower-case, NFKC, joiners and soft hyphens dropped, hyphenated line breaks joined, punctuation to spaces.

    With `lines`, a line break before a line that starts with a capital or a digit stays as one "\\n" (one
    character, so swapping it for a space keeps every index): it marks where a heading or a bullet ends."""
    s = unicodedata.normalize("NFKC", s).replace("⁠", "").replace("­", "")
    s = _CONTRACTIONS.sub(r"\1 not", s)
    s = _LIST_NUMBER.sub("", s)
    if dehyph:
        s = _HYPHEN_BREAK.sub(r"\1\2", s)
    s = _CLOCKWISE.sub("anticlockwise", s)
    if lines:
        s = _LINE_START.sub("\n", s)
        s = _NOT_WORD.sub(" ", s.lower())
        s = _RUNS.sub(lambda m: "\n" if "\n" in m.group() else " ", re.sub(r"[^\S\n]", " ", s))
        return s.strip()
    s = _NOT_WORD.sub(" ", s.lower())
    return _SPACES.sub(" ", s).strip()


def crit_tokens(normed: str) -> list[str]:
    return sorted(CRIT.findall(normed))


@dataclass(frozen=True)
class CitationResult:
    ok: bool
    score: float
    reason: str  # "ok", "too_short", "low_score", "critical_tokens", "no_text"
    quote_tokens: list[str] = field(default_factory=list)
    page_tokens: list[str] = field(default_factory=list)
    page: int | None = None  # the page it was accepted on (verify only)
    corrected_from: int | None = None  # the cited page, when the quote was found on another one
    ocr: bool = False


def _sentence_window(text: str, start: int, end: int) -> str:
    """Widen an alignment window to the sentences around it. A quote that is a true substring can still change the
    meaning by what it leaves out ("Do not | remove the filter…", "turn the knob | anticlockwise"), so the critical
    tokens are compared over the whole sentence, not just the matched part."""
    lo = max(0, start - MAX_EXTEND)
    while start > lo and text[start - 1] not in _BOUNDARY:
        start -= 1
    hi = min(len(text), end + MAX_EXTEND)
    while end < hi and text[end - 1] not in _BOUNDARY:
        end += 1
    return text[start:end]


def _align(q: str, t: str) -> tuple[float, int, int]:
    """(score, start, end) of the quote's best match on the page. `partial_ratio` slides the shorter string along
    the longer, so a page shorter than the quote would be found inside the quote ("a" scores 100): such a page is
    compared whole instead, and passes only if it is the quote."""
    if len(t) < len(q):
        return fuzz.ratio(q, t), 0, len(t)
    al = fuzz.partial_ratio_alignment(q, t)
    return (al.score, al.dest_start, al.dest_end) if al is not None else (0.0, 0, 0)


def locate(page_text: str, quote: str) -> tuple[str, int, int] | None:
    """Where the quote sits on the page: (the page's normalised text, start, end), or None when it is not there. The
    text keeps a newline where a line starts with a capital or a digit (a heading, a warning box, a bullet)."""
    tl = norm(page_text, lines=True)
    score, start, end = _align(norm(quote), tl.replace("\n", " "))
    return (tl, start, end) if tl and score >= MIN_SCORE else None


def check_quote(page_text: str, quote: str, *, ocr: bool = False) -> CitationResult:
    """Is `quote` verbatim on this page (fuzzy, with the critical tokens equal)?"""
    q = norm(quote)
    qt = crit_tokens(q)
    if len(q) < MIN_CHARS:
        return CitationResult(False, 0.0, "too_short", qt)
    best: tuple[float, str] = (0.0, "")
    for dehyph in (True, False):
        tl = norm(page_text, dehyph, lines=True)
        t = tl.replace("\n", " ")
        if not t:
            continue
        score, start, end = _align(q, t)
        if score > best[0]:
            best = (score, _sentence_window(tl, start, end))
    score, window = best
    if not window:
        return CitationResult(False, 0.0, "no_text", qt, ocr=ocr)
    wt = crit_tokens(window)
    if score < (MIN_SCORE_OCR if ocr else MIN_SCORE):
        return CitationResult(False, score, "low_score", qt, wt, ocr=ocr)
    if qt != wt:
        return CitationResult(False, score, "critical_tokens", qt, wt, ocr=ocr)
    return CitationResult(True, score, "ok", qt, wt, ocr=ocr)


def verify(pages: Sequence[str], page: int, quote: str, *, ocr_pages: frozenset[int] = frozenset()) -> CitationResult:
    """Check the quote on its cited page (1-based); on a miss, accept it on the page it is really on and say so."""
    if 1 <= page <= len(pages):
        r = check_quote(pages[page - 1], quote, ocr=page in ocr_pages)
        if r.ok or r.reason == "too_short":
            return _on(r, page, None)
    else:
        r = CitationResult(False, 0.0, "no_text", crit_tokens(norm(quote)))
    q = norm(quote)
    order = sorted(range(1, len(pages) + 1), key=lambda i: -_align(q, norm(pages[i - 1]))[0])
    for other in order[:3]:
        if other == page:
            continue
        alt = check_quote(pages[other - 1], quote, ocr=other in ocr_pages)
        if alt.ok:
            return _on(alt, other, page)
    return r


_SENTENCE_END = re.compile(r"(?<=[.!?:])\s+")


def _as_read(s: str) -> str:
    """The text as a reader sees it: hyphenated line breaks joined, step numbers and callout digits dropped (the
    same layout `norm` drops), whitespace to single spaces; case and punctuation kept."""
    s = unicodedata.normalize("NFKC", s).replace("\u2060", "").replace("\u00ad", "")
    s = _LIST_NUMBER.sub("", _HYPHEN_BREAK.sub(r"\1\2", s))
    return _SPACES.sub(" ", s).strip()


def widen(page_text: str, quote: str) -> str | None:
    """A quote too short to check, widened to the page's own words: the whole sentence that holds it, or with the
    sentence after it (else before it) when that sentence alone is still too short. None when the quote is not on
    the page word for word. Nothing is reworded: the caller checks the widened quote like any other."""
    page = _as_read(page_text)
    q = re.sub(r"^\d{1,2}\.\s*", "", _as_read(quote))
    at = page.find(q) if q else -1
    if at < 0:
        return None
    cuts = [0, *(m.end() for m in _SENTENCE_END.finditer(page)), len(page)]
    spans = [(a, b) for a, b in zip(cuts, cuts[1:], strict=False) if b > a]
    first = next(i for i, (a, b) in enumerate(spans) if b > at)
    last = next(i for i, (a, b) in enumerate(spans) if b >= at + len(q))
    if len(norm(page[spans[first][0]:spans[last][1]])) < MIN_CHARS:
        if last + 1 < len(spans):
            last += 1
        elif first > 0:
            first -= 1
    out = page[spans[first][0]:spans[last][1]].strip()
    return out if len(norm(out)) >= MIN_CHARS else None


def _on(r: CitationResult, page: int, corrected_from: int | None) -> CitationResult:
    return CitationResult(r.ok, r.score, r.reason, r.quote_tokens, r.page_tokens, page if r.ok else None,
                          corrected_from, r.ocr)

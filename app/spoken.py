"""The guide's text as it is drawn and spoken: plain sentences, no Markdown, no talk about its own tools, and short.

The guide writes chat-style replies (bold, bullets, `>` quotes, whole tables) although its prompt asks for plain
speech, and it narrates its tool calls ("Let me fix that citation."). A phone reads every sentence aloud, so code
makes the text speakable before G0 sees it:
- `plain`: Markdown to plain sentences. A quote block is dropped (the manual's words are shown by `cite`, not read
  out); each item of a list or row of a table becomes a sentence of its own: the steps are said, never replaced by
  a page to look up.
- `is_process`: a sentence about the guide's own work (looking things up, citing, retrying), never said.
- `spoken`: both, then at most MAX_SENTENCES sentences of prose, with a pointer to the page after them when it cut
  something. A list's items do not count (up to MAX_ITEMS), nor does the short line that leads into it.
- `split`: sentences, not split after an abbreviation ("e.g.", "bijv."). The session splits with it for G0, so each
  item goes through G0 on its own.
The lines code adds are in the session's language (`in_lang`: English when it has no line in that language).
"""

from __future__ import annotations

import re
from typing import Any

MAX_SENTENCES = 2
MAX_ITEMS = 12  # a list's items said, beyond the prose cap; past this many the rest is pointed to after them
REST = {"en": "The manual has the rest on page {page}.", "nl": "De rest staat in de handleiding op pagina {page}."}

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
ABBREVIATIONS = frozenset({"e.g.", "i.e.", "vs.", "approx.", "incl.", "excl.", "no.", "p.", "bijv.", "bv.", "b.v.",
                           "o.a.", "d.w.z.", "ca.", "nr.", "zgn.", "evt.", "m.b.v."})
_BULLET = re.compile(r"^\s*(?:[*\-+•]|\d{1,2}[.)])\s+(.*)$")
_TABLE_RULE = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_TABLE_ROW = re.compile(r"^\s*\|(.*)\|\s*$")
_QUOTE = re.compile(r"^\s*>")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_INLINE = [
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),  # image
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),  # link
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1"), r"\2"),
    (re.compile(r"~~(?=\S)(.+?)(?<=\S)~~"), r"\1"),
    (re.compile(r"(?<![\w*])\*(?=\S)(.+?)(?<=\S)\*(?![\w*])"), r"\1"),
    (re.compile(r"(?<![\w_])_(?=\S)(.+?)(?<=\S)_(?![\w_])"), r"\1"),
    (re.compile(r"\s*(?:->|→|=>)\s*"), ": "),
]
_QUOTED = re.compile(r"[“\"„«]\s*(?:\S+\s+){3,}\S+?\s*[”\"»]\.?")
_PAGE = re.compile(r"\b(?:page|p\.|pagina)\s*(\d{1,4})\b", re.IGNORECASE)
_PROCESS = [re.compile(p, re.IGNORECASE) for p in (
    r"\bcit(?:e|es|ed|ing|ation|ations)\b",
    r"\bretry\b|\btry (?:it|that|again)\b",
    r"^(?:let me|let's)\b(?!\s+know)",
    r"\b(?:i'll|i will|i'm going to|i am going to|i need to)\s+(?:now\s+|just\s+|first\s+)?"
    r"(?:check|pull|verify|look|get|fix|point|confirm|search|find|open|read|walk|grab|fetch)\b",
    r"\b(?:checking|looking up|pulling up|searching|consulting)\b.*\b(?:manual|card|page)\b",
    r"\b(?:tool|function call|lookup_manual|check_step|ask_for_view|report_hazard|step_id)\b",
    r"\bcit(?:aat|aten|eer|eren|eert)\b",
    r"^laat me\b(?!\s+(?:even\s+)?weten)",
    # Dutch puts the verb last ("Even de handleiding nakijken."): the guide speaking of itself (ik, or a sentence
    # that starts with even/eerst), a source and a lookup verb in any order; "Kijk even in de handleiding" is for
    # the user and stays
    r"^(?=(?:even|eerst)\b|.*\bik\b)(?=.*\b(?:handleiding|kaart)\b)"
    r".*\b(?:kijk|kijken|nakijken|zoek|zoeken|opzoeken|controleer|controleren|check|checken)\b",
)]


def in_lang(table: dict[str, Any], lang: str) -> Any:
    """A fixed line (or set of lines) in the session's language, else English."""
    return table.get(lang[:2].lower(), table["en"])


def _inline(s: str) -> str:
    for pat, rep in _INLINE:
        s = pat.sub(rep, s)
    return re.sub(r"\*{2,}|(?<!\S)\*(?!\S)", "", s).strip()  # what is left of an unclosed bold


def _sentence(s: str) -> str:
    s = s.strip().rstrip(":;,").strip()
    if not s:
        return ""
    s = s[0].upper() + s[1:]
    return s if s[-1] in ".!?" else s + "."


def page_of(text: str) -> int | None:
    """The page the text names, when it names exactly one (a pointer to the wrong page is worse than none)."""
    pages = {int(m.group(1)) for m in _PAGE.finditer(text)}
    return pages.pop() if len(pages) == 1 else None


def _unquote(sentence: str) -> str:
    """The sentence without the manual's words in quotes (`cite` shows them; the voice never reads them), or ''
    when nothing but a label is left ("Manual says:")."""
    s = _QUOTED.sub("", sentence).strip()
    if s == sentence.strip():
        return s
    s = re.sub(r"\s+([.,;:!?])", r"\1", s).rstrip(" .:;,—-").strip()
    return "" if len(s.split()) <= 3 else _sentence(s)


def plain(text: str) -> str:
    """Markdown to plain sentences, one space between them."""
    return re.sub(r"\s+", " ", " ".join(t for t, _ in _parts(text))).strip()


def _parts(text: str) -> list[tuple[str, str]]:
    """The text as plain pieces, each marked prose, item (of a list or table) or lead (the line before a list)."""
    blocks: list[tuple[str, list[str]]] = []  # ("para", [line]) | ("list", items) | ("drop", [])
    for raw in text.replace("\r", "").split("\n"):
        line = raw.rstrip()
        if not line.strip() or _FENCE.match(line) or _TABLE_RULE.match(line):
            if not line.strip():
                blocks.append(("gap", []))
            continue
        if _QUOTE.match(line):
            if not blocks or blocks[-1][0] != "drop":
                blocks.append(("drop", []))
            continue
        row = _TABLE_ROW.match(line)
        item = " — ".join(c.strip() for c in row.group(1).split("|") if c.strip()) if row else None
        if item is None:
            b = _BULLET.match(line)
            item = b.group(1) if b else None
        if item is not None:
            if blocks and blocks[-1][0] == "list":
                blocks[-1][1].append(_inline(item))
            else:
                blocks.append(("list", [_inline(item)]))
            continue
        h = _HEADING.match(line)
        blocks.append(("para", [_inline(h.group(1) if h else line)]))
    out: list[tuple[str, str]] = []
    for i, (kind, lines) in enumerate(blocks):
        nxt = next((k for k, _ in blocks[i + 1:] if k != "gap"), None)
        if kind == "para":
            s = lines[0]
            if s.endswith(":") and nxt in ("list", "drop", None):
                if len(s.split()) <= 3 or nxt == "list" and _PAGE.search(s):
                    continue  # "Suitable:", "From the manual, page 9:": a label, not a sentence (cite shows the page)
                out.append((s[:-1] + ".", "lead" if nxt == "list" else "prose"))
                continue
            if s:
                out.append((s if s.endswith(":") else _sentence(s), "prose"))
        elif kind == "list":
            out.extend((_sentence(x), "item") for x in lines if _sentence(x))
    return out


def is_process(sentence: str) -> bool:
    return any(p.search(sentence) for p in _PROCESS)


def split(text: str) -> list[str]:
    """Sentences: after . ! or ? and a space, but not after an abbreviation ("e.g. a bottle" stays one sentence)."""
    out: list[str] = []
    start = 0
    text = text.strip()
    for m in SENTENCE_END.finditer(text):
        words = text[start:m.start()].split()
        if words and words[-1].lower() in ABBREVIATIONS:
            continue
        out.append(text[start:m.start()])
        start = m.end()
    out.append(text[start:])
    return [s.strip() for s in out if s.strip()]


def spoken(text: str, page: int | None = None, max_sentences: int = MAX_SENTENCES, lang: str = "en") -> str:
    """What the guide's text becomes on the screen and in the voice ('' when nothing in it is for the user).
    At most `max_sentences` sentences of prose and MAX_ITEMS items of a list; when it cut something, a pointer to
    `page` (the manual page this turn cited, if any; else the one page the text names) follows what was said.
    A list's steps are always said: a page never stands in for them."""
    page = page or page_of(text)
    said: list[str] = []
    prose = items = 0
    cut = False
    for piece, kind in _parts(text):
        for s in split(piece):
            u = _unquote(s) if not is_process(s) else ""
            if not u:
                continue
            if kind == "item":
                items += 1
                keep = items <= MAX_ITEMS
            elif kind == "lead":
                keep = True
            else:
                prose += 1
                keep = prose <= max_sentences
            if keep:
                said.append(u)
            else:
                cut = True
    if cut and page:
        said.append(in_lang(REST, lang).format(page=page))
    return " ".join(said)

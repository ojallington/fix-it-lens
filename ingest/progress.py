"""What an ingest knows so far, for the wait screen (contracts 1.15, Ingest): every field is written by code.

A `Tracker` keeps the progress fields of the contract's Ingest object (not `stage` or `notice`, which the flow
owns) and hands a full copy to `on_update` after every change. `progress` never goes backwards. `eta_s` is code's
estimate from the page count, never a model's. Job titles in `jobs_ready` and the manual's `title` are untrusted
text (model-written, or the PDF's own metadata): the server passes them through G0 before it serves them.
"""

from __future__ import annotations

import contextlib
import json
import re
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

Update = Callable[[dict[str, Any]], None]

# Seconds for a build, fitted on the A2 baseline runs: a fixed part (clause pass, checks) and the draft per page.
FIXED_S = 60.0
PER_PAGE_S = 1.0
# The share of the bar each phase fills: fetch and pages, the draft, then the clause pass and the checks.
DRAFT_FROM, DRAFT_TO = 0.1, 0.7

STOPWORDS = {
    "en": "the and to of is with for it be not that this you your are or on",
    "nl": "de het een van en is niet met op te dat voor je uw zijn of wordt",
    "de": "der die das und ist nicht mit auf zu dass für sie ihr sind oder wird",
    "fr": "le la les et est pas avec sur pour que vous des une du ou",
    "es": "el la los y es no con en para que usted las una del o",
    "it": "il la le e non con per che di una del sono o della",
}
_WORDS = {lang: frozenset(ws.split()) for lang, ws in STOPWORDS.items()}


def page_lang(text: str) -> str | None:
    """The language of one page by its commonest small words; None for a page with too few to tell."""
    words = re.findall(r"[a-zà-ÿ]+", text.lower())
    score = {lang: sum(w in ws for w in words) for lang, ws in _WORDS.items()}
    best = max(score, key=lambda k: score[k])
    return best if score[best] >= 8 else None


def manual_lang(texts: list[str], prefer: str | None = None) -> str:
    """The manual's language: `prefer` (the household's) when a fair share of pages are in it, else the commonest."""
    langs = Counter(lang for t in texts if (lang := page_lang(t)))
    if not langs:
        return prefer or "en"
    if prefer and langs[prefer] >= max(2, 0.15 * sum(langs.values())):
        return prefer
    return langs.most_common(1)[0][0]


def pdf_title(pdf: Path) -> str | None:
    """The PDF's own metadata title (untrusted file text, at most 200 characters), or None."""
    from pypdf import PdfReader

    try:
        title = PdfReader(pdf).metadata.title  # type: ignore[union-attr]
    except Exception:  # no metadata, or a file pypdf can't read
        return None
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    return title[:200] or None


def eta_for(pages: int) -> int:
    return round(FIXED_S + PER_PAGE_S * pages)


class Tracker:
    def __init__(self, on_update: Update | None = None, *, clock: Callable[[], float] = time.monotonic) -> None:
        self.on_update = on_update
        self.clock = clock
        self.t0 = clock()
        self.total_s: float | None = None
        self.state: dict[str, Any] = {"progress": 0.0, "elapsed_s": 0}

    def _emit(self) -> None:
        elapsed = self.clock() - self.t0
        self.state["elapsed_s"] = int(elapsed)
        if self.total_s is not None:
            self.state["eta_s"] = max(0, round(self.total_s - elapsed))
        if self.on_update is not None:
            self.on_update(self.snapshot())

    def snapshot(self) -> dict[str, Any]:
        return {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v)
                for k, v in self.state.items()}

    def update(self, progress: float | None = None, **fields: Any) -> None:
        if progress is not None:
            self.state["progress"] = round(max(self.state["progress"], min(1.0, progress)), 3)
        self.state.update(fields)
        self._emit()

    def manual(self, pdf: Path, texts: list[str], household_lang: str | None) -> str:
        """The manual's facts as soon as its pages are read; returns its language."""
        lang = manual_lang(texts, household_lang)
        info: dict[str, Any] = {"pages": max(1, len(texts)), "lang": lang, "title": pdf_title(pdf)}
        if household_lang:
            info["user_lang"] = lang == household_lang
        self.total_s = (self.clock() - self.t0) + eta_for(len(texts))
        self.update(DRAFT_FROM, manual=info)
        return lang

    def drafting(self) -> None:
        """A tick while the draft runs: the bar moves with the clock towards the draft's share, never past it."""
        if self.total_s is None:
            return
        share = min(1.0, (self.clock() - self.t0) / max(1.0, self.total_s - FIXED_S / 2))
        self.update(DRAFT_FROM + (DRAFT_TO - DRAFT_FROM) * share * 0.95)

    def ready(self, jobs: list[dict[str, Any]], checked: int, passed: int, done: float) -> None:
        self.update(done, quotes={"checked": checked, "passed": passed}, jobs_ready=jobs)


class JobStream:
    """Each job of a draft as soon as the model has written it: an incremental scan of the streamed JSON for the
    objects in the top-level "jobs" array. Unchecked draft text: a job goes on the wait screen only after its own
    quotes pass the citation check. A retried reply (attempt 2) streams from the start, so the scan restarts."""

    def __init__(self) -> None:
        self.attempt = 1
        self._reset()

    def _reset(self) -> None:
        self.text = ""
        self.i = 0
        self.depth = 0
        self.in_str = self.esc = False
        self.str_start = 0
        self.last_str = ""
        self.in_jobs = False
        self.obj_start = 0
        self.top: dict[str, str] = {}  # the draft's top-level string values so far (appliance_kind, manual_lang)

    def feed(self, delta: str, attempt: int = 1) -> list[dict[str, Any]]:
        if attempt != self.attempt:
            self.attempt = attempt
            self._reset()
        self.text += delta
        out: list[dict[str, Any]] = []
        t = self.text
        while self.i < len(t):
            c = t[self.i]
            if self.in_str:
                if self.esc:
                    self.esc = False
                elif c == "\\":
                    self.esc = True
                elif c == '"':
                    self.in_str = False
                    if self.depth == 1:
                        value = t[self.str_start + 1:self.i]
                        if t[:self.str_start].rstrip().endswith(":"):
                            self.top[self.last_str] = value
                        else:
                            self.last_str = value
            elif c == '"':
                self.in_str, self.str_start = True, self.i
            elif c in "{[":
                self.depth += 1
                if c == "[" and self.depth == 2 and self.last_str == "jobs":
                    self.in_jobs = True
                elif c == "{" and self.in_jobs and self.depth == 3:
                    self.obj_start = self.i
            elif c in "}]":
                if c == "}" and self.in_jobs and self.depth == 3:
                    with contextlib.suppress(ValueError):  # not a whole job after all: the final parse decides
                        out.append(json.loads(t[self.obj_start:self.i + 1]))
                elif c == "]" and self.in_jobs and self.depth == 2:
                    self.in_jobs = False
                self.depth -= 1
            self.i += 1
        return out

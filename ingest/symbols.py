"""Where a fault row's symbol is drawn: the small picture the manual prints beside an indicator row (a light, a display
icon), found by code from the PDF's own drawing commands, never by a model. No picture beside the row is null.

    rows = locate(pdf, card["troubleshooting"])   # row id -> {page, bbox: [x0, y0, x1, y1]} (fractions, top-left)

A row is anchored by its first code, else the start of its quote, as the page's text prints it. A symbol is an image
placed on the same page, small (an icon, not a photo), on the anchor's line or the line above or below, nearest to
the anchor. Symbols drawn as vector paths or font glyphs are not images: those rows stay null.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from pypdf import PdfReader

MAX_ICON = 0.15  # of the page's width and height: larger is a picture of the appliance, not a symbol
LINE_SLACK = 1.6  # line heights above or below the anchor's line


def _mul(a: list[float], b: list[float]) -> list[float]:
    return [a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3], a[2] * b[0] + a[3] * b[2],
            a[2] * b[1] + a[3] * b[3], a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5]]


def _flat(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _words(s: str) -> str:
    """Lower case, a code's own punctuation dropped ("E:21" is "e21"), words apart."""
    return " " + re.sub(r"[^a-z0-9]+", " ", re.sub(r"(?<=\w)[:./-](?=\w)", "", s.lower())).strip() + " "


def _page_marks(page: Any) -> tuple[list[tuple[str, float, float, float]], list[tuple[float, float, float, float]]]:
    """The page's text runs (flat text, x, y, size) and its placed images (x0, y0, x1, y1), in PDF points."""
    texts: list[tuple[str, float, float, float]] = []  # (words, x, y, size)
    images: list[tuple[float, float, float, float]] = []
    xobjects = page.get("/Resources", {}).get("/XObject", {}) if page.get("/Resources") else {}

    def on_text(text: str, cm: list[float], tm: list[float], font: Any, size: float) -> None:
        if _flat(text):
            m = _mul(list(tm), list(cm))
            texts.append((_words(text), m[4], m[5], abs(size * m[3]) or size or 10.0))

    def on_op(op: bytes, args: list[Any], cm: list[float], tm: list[float]) -> None:
        if op != b"Do" or not args:
            return
        try:
            xo = xobjects[args[0]].get_object()
        except (KeyError, AttributeError, TypeError):
            return
        if xo.get("/Subtype") != "/Image":
            return
        x0, y0 = cm[4], cm[5]
        x1, y1 = cm[4] + cm[0] + cm[2], cm[5] + cm[1] + cm[3]
        images.append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))

    try:
        page.extract_text(visitor_text=on_text, visitor_operand_before=on_op)
    except Exception:  # a page pypdf can not walk has no symbols for us
        return [], []
    return texts, images


def _anchor(texts: list[tuple[str, float, float, float]], code: str | None,
            quote: str) -> tuple[float, float, float] | None:
    """Where the row starts on the page: its code as a whole word ("F1" is never "F10"), else the run that starts
    its quote (16 characters of it, or the whole run when shorter)."""
    for words, x, y, size in texts:
        flat = words.replace(" ", "")
        if code is not None:
            if f" {code} " in words:
                return x, y, size
        elif quote and len(flat) >= 4 and (quote[:16] in flat or quote.startswith(flat)):
            return x, y, size
    return None


def locate(pdf: Path, rows: list[dict[str, Any]], quotes: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Each indicator row's symbol on its page, by row id. `quotes` maps a row's quote_ref to its text."""
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # font warnings on every walk of a maker's PDF: not ours
    reader = PdfReader(str(pdf))
    marks: dict[int, Any] = {}
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        if not r.get("indicator") or not 1 <= r["page"] <= len(reader.pages):
            continue
        page = reader.pages[r["page"] - 1]
        if r["page"] not in marks:
            marks[r["page"]] = _page_marks(page)
        texts, images = marks[r["page"]]
        code = _words(r["codes"][0]).strip() if r["codes"] else None
        at = _anchor(texts, code, _flat(quotes.get(r["quote_ref"], "")))
        if at is None or not images:
            continue
        x, y, size = at
        box = page.mediabox
        w, h = float(box.width), float(box.height)
        left, bottom = float(box.left), float(box.bottom)
        near = [im for im in images
                if (im[2] - im[0]) <= MAX_ICON * w and (im[3] - im[1]) <= MAX_ICON * h
                and abs((im[1] + im[3]) / 2 - (y + size / 3)) <= LINE_SLACK * size + (im[3] - im[1]) / 2]
        if not near:
            continue
        x0, y0, x1, y1 = min(near, key=lambda im: abs((im[0] + im[2]) / 2 - x))
        frac = [(x0 - left) / w, (h - (y1 - bottom)) / h, (x1 - left) / w, (h - (y0 - bottom)) / h]
        out[r["id"]] = {"page": r["page"], "bbox": [round(min(1.0, max(0.0, f)), 4) for f in frac]}
    return out

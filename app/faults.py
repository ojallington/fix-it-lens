"""What a fault code means, from the card alone: GET /appliances/{id}/meaning?code=<as typed>.

No model is called. The typed code is matched against the card's troubleshooting rows (the manual's fault table,
`card.troubleshooting`) and its fix jobs (`jobs[].fault_codes`, for a card built before the table) after both are
normalised the way a display is misread or retyped: case, spaces and the separators `: - . /` are ignored, and O is
read as 0 and I as 1 ("e 32 00", "E3200" and "E:32-00" are one code).

- `matches`: a row whose remedy is the user's, with its page and quote and the fix job that carries it out (null
  when the remedy has no job, its title then the row's meaning); else a fix job for that code, with its title and
  its first step's page and quote. The phone starts the job or opens the page.
- `candidates`: when nothing matches exactly, the card's codes that begin or end with what was typed ("E32" for
  E:32-00, "C2" for "Err C2"), for the user to pick; never a guess between them. More than MAX_CANDIDATES is no
  list at all (a cut list could leave out the right one): the notice asks for more of the code.
- `refused`: codes whose remedy is a technician's or an installer's (the notice names who, as the manual does), or
  whose row or fix job the rules held (no title shown; the notice says it is a technician's job).
- `table_page`: where the manual's troubleshooting starts (the first page a fix job starts on, refused ones
  included), or null.
- `landlord`: the household rents and the rules file names a matched job the landlord's; the phone says to ask
  the landlord.
- `notice`: fixed, code-written text in the household's language when nothing can be started. A code that is not on
  the card is said to be not in the manual's list, with the page; a model never explains a code the card lacks.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import rules
from app import cards
from contracts.models.card import Card

_SEPARATORS = re.compile(r"[\s:\-./]+")
_LOOKALIKE = str.maketrans({"O": "0", "I": "1"})
MAX_CODE = 20
MAX_CANDIDATES = 5  # more than this is not a pick: the user types more of the code

NOTICES: dict[str, dict[str, str]] = {
    "not_listed": {"en": "This code isn't in your manual's list.",
                   "nl": "Deze code staat niet in de lijst van je handleiding."},
    "not_listed_page": {"en": "This code isn't in your manual's list. Your manual's troubleshooting starts on page "
                              "{page}.",
                        "nl": "Deze code staat niet in de lijst van je handleiding. Het deel over storingen begint op "
                              "pagina {page}."},
    "pick": {"en": "Which of these does the display show?", "nl": "Welke van deze staat op het display?"},
    "more": {"en": "Several codes in your manual look like that. Type a bit more of what the display shows.",
             "nl": "Meerdere codes in je handleiding lijken daarop. Typ wat meer van wat er op het display staat."},
    "not_guided": {"en": "Your manual has a fix for this code, but it isn't one I can guide you through. This is a "
                         "job for a technician.",
                   "nl": "Je handleiding heeft een oplossing voor deze code, maar die kan ik je niet laten doen. Dit "
                         "is werk voor een monteur."},
    "technician": {"en": "Your manual says this one is for a service technician.",
                   "nl": "Volgens je handleiding is dit werk voor een servicemonteur."},
    "installer": {"en": "Your manual says this one is for your installer.",
                  "nl": "Volgens je handleiding is dit werk voor je installateur."},
}


def normalise(code: str) -> str:
    return _SEPARATORS.sub("", code.strip().upper()).translate(_LOOKALIKE)


def notice(kind: str, lang: str | None, page: int | None = None) -> str:
    table = NOTICES[kind]
    return (table.get(lang or "en") or table["en"]).format(page=page)


def table_page(card: Card) -> int | None:
    """The manual's fault table: the first page a troubleshooting row cites, else the first page a fix job starts on
    (later steps can cite other sections, a safety page or a parts list)."""
    pages = [r.page for r in card.troubleshooting or []] or \
        [j.steps[0].page for j in card.jobs if j.kind == "fix" and j.steps]
    return min(pages) if pages else None


Drawn = frozenset[tuple[str, str]]  # (row id, code): fault codes read from a drawing that no person has confirmed


def drawn_unconfirmed(card_file: Path) -> Drawn:
    """The card's check record names the codes read from a drawing in the manual (a seven-segment display) rather
    than from its text; until a person confirms one, a match on it says so (FaultMeaning matches[].unconfirmed)."""
    from ingest import check_pr

    rec_file = check_pr.record_path(card_file)
    try:
        rec = json.loads(rec_file.read_text()) if rec_file.exists() else {}
    except (OSError, ValueError):
        rec = {}
    return frozenset((d["row"], d["code"]) for d in rec.get("drawn_codes", []) if not d.get("confirmed_by"))


def _hit(codes: Any, want: str) -> str | None:
    return next((c.root for c in codes if normalise(c.root) == want), None)


def row_match(row: Any, code: str | None, kept: dict[str, Any], quotes: dict[str, Any],
              drawn: Drawn = frozenset()) -> dict[str, Any]:
    """A troubleshooting row as a match: the whole row (its meaning, cause, remedy and who acts, every text through
    G0 at load) with its page and quote. A technician's or an installer's remedy has no job and no escalation."""
    user = row.remedy_kind == "user"
    job = kept.get(cards.slug(row.job_ref)) if user and row.job_ref else None
    q = quotes[row.quote_ref]
    m: dict[str, Any] = {"code": code or (row.codes[0].root if row.codes else None), "job_id": job.id if job else None,
                         "title": job.title.root if job else _lt(row.meaning), "page": row.page, "quote": q.text,
                         "row_id": row.id, "meaning": _lt(row.meaning), "cause": _lt(row.cause),
                         "remedy": _lt(row.remedy), "remedy_kind": row.remedy_kind,
                         "escalate_to": row.escalate_to if user else None,
                         "symbol": row.symbol.model_dump(mode="json") if row.symbol is not None else None}
    m["unconfirmed"] = (row.id, m["code"]) in drawn
    if q.en:
        m["quote_en"] = q.en
    return m


def _lt(text: Any) -> dict[str, str] | None:
    return dict(cards.lang_items(text)) if text is not None else None


def meaning(card: Card, raw: Card | None, code: str, lang: str | None, *, renter: bool = False,
            country: str = "nl", drawn: Drawn = frozenset()) -> dict[str, Any]:
    """`card`: the checked card (refused jobs and held rows already left it). `raw`: the card as written, for the codes
    of what left; None when it cannot be read (then those codes read as not listed)."""
    want = normalise(code)
    kept = {j.id: j for j in card.jobs}
    quotes = {q.id: q for q in card.quotes}
    matches: list[dict[str, Any]] = []
    refused: list[str] = []
    kinds: set[str] = set()  # who the manual names for the refused codes; "not_guided" for what the rules refused
    rows = {r.id: r for r in card.troubleshooting or []}
    for row in (raw.troubleshooting if raw is not None else card.troubleshooting) or []:
        hit = _hit(row.codes, want)
        if hit is None:
            continue
        checked = rows.get(row.id)
        if checked is None:  # held at load: its words are not shown
            refused.append(hit)
            kinds.add("not_guided")
            continue
        matches.append(row_match(checked, hit, kept, quotes, drawn))
        if checked.remedy_kind != "user":
            refused.append(hit)
    by_row = {m["job_id"] for m in matches if m["job_id"]}
    for job in raw.jobs if raw is not None else card.jobs:
        hit = _hit(job.fault_codes, want) if job.kind == "fix" else None
        if hit is None or job.id in by_row:
            continue
        if job.id not in kept:
            refused.append(hit)
            kinds.add("not_guided")
            continue
        step = job.steps[0]
        m = {"code": hit, "job_id": job.id, "title": job.title.root, "page": step.page, "quote": step.quote.text,
             "unconfirmed": False}  # a job's own codes are printed in the manual's text
        if step.quote.en:
            m["quote_en"] = step.quote.en
        matches.append(m)
    candidates: list[str] = []
    if not matches and not refused and len(want) >= 2:
        every = [c.root for r in card.troubleshooting or [] for c in r.codes] + \
            [c.root for j in card.jobs if j.kind == "fix" for c in j.fault_codes]
        for c in every:
            n = normalise(c)
            if n != want and (n.startswith(want) or n.endswith(want)) and c not in candidates:
                candidates.append(c)
    too_many = len(candidates) > MAX_CANDIDATES
    if too_many:
        candidates = []
    page = table_page(raw if raw is not None else card)  # what left the card still marks where the table is
    said: str | None = None
    if not matches:
        if refused:  # the manual's own word first: a technician over an installer, either over the rules' refusal
            said = notice(next(k for k in ("technician", "installer", "not_guided") if k in kinds), lang)
        elif too_many:
            said = notice("more", lang)
        elif candidates:
            said = notice("pick", lang)
        else:
            said = notice("not_listed_page" if page else "not_listed", lang, page)
    return {"code": code.strip(), "matches": matches, "candidates": candidates, "refused": sorted(set(refused)),
            "table_page": page, "notice": said, "landlord": landlord(card, matches, renter, country)}


def landlord(card: Card, matches: list[dict[str, Any]], renter: bool, country: str) -> bool:
    """True for a renter when a matched job is one the rules file names the landlord's."""
    jobs = {m["job_id"] for m in matches if m["job_id"]}
    return renter and bool(jobs & set(rules.landlord_jobs(card, country)))

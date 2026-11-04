"""The camera read of an appliance's display or lights: POST /appliances/{id}/display with one JPEG.

One model call (the plate read's role: a photo transcribed, no reasoning about the appliance) through calls/, with the
photo in the newest and only user message and store false. Its answer is constrained by the response schema to the
checked card's own codes and its indicator rows' ids, or null, so it can never name a code or a light the card does
not have, and it writes no text the user sees. Code then checks the answer against the card again and runs the same
lookup as GET .../meaning: what the user reads is the card's cited rows, never the model's words.

The photo is kept in FIXIT_PRIVATE/displays/homes/<household>/ (folders 0700, the photo 0600), so deleting the
household deletes it, and never enters a card or the repo. Photos kept before they were filed by home name no
household; `untrace` moves them aside, unread and undeleted:

    uv run python -m app.display untrace     # FIXIT_PRIVATE/displays/*.jpg -> displays/untraced-<date>/
"""

from __future__ import annotations

import argparse
import os
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import calls
from app import cards, config, faults, recorder
from contracts.models.card import Card

ROLE = "plate"
MAX_BYTES = 8_388_608
PROMPT = """\
This photo should show the display or the indicator lights of a home appliance. Answer only from the choices below.
- code: the fault code the display shows, as one of the listed codes, or null when it shows none of them or you
  cannot read every character of it.
- row_id: the id of the listed light or symbol that is lit or blinking in the photo, or null when none is.
- uncertain: true when the photo is unclear, or you are not sure of your answer.
Never guess: null and uncertain true are the right answer when you cannot tell.
"""
UNREAD: dict[str, str] = {
    "en": "I couldn't read the display. Take a closer photo, or type the code.",
    "nl": "Ik kon het display niet lezen. Maak een foto van dichterbij, of typ de code.",
}


def _private_folder(priv: Path, *parts: str) -> Path:
    """priv/parts..., each folder below priv made if missing and set to 0700 (priv itself is left as it is)."""
    priv.mkdir(parents=True, exist_ok=True)
    d = priv
    for part in parts:
        d = d / part
        d.mkdir(mode=0o700, exist_ok=True)
        os.chmod(d, 0o700)
    return d


def keep(jpeg: bytes, household: str) -> str:
    """The photo in the household's folder, 0600, under a new id (the record of what was read)."""
    photo_id = uuid.uuid4().hex
    priv = config.private_dir()
    if priv is not None:
        d = _private_folder(priv, "displays", "homes", recorder.safe_id(household))
        fd = os.open(d / f"{photo_id}.jpg", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(jpeg)
    return photo_id


def untrace(today: date | None = None) -> int:
    """Move the photos kept flat in displays/ (no household named) into displays/untraced-<date>/, 0700, each file
    0600; none is read or deleted. How many moved."""
    priv = config.private_dir()
    flat = [] if priv is None else sorted(p for p in (priv / "displays").glob("*.jpg") if p.is_file())
    if not flat or priv is None:
        return 0
    to = _private_folder(priv, "displays", f"untraced-{(today or date.today()).isoformat()}")
    for p in flat:
        os.chmod(p, 0o600)
        p.rename(to / p.name)
    return len(flat)


def choices(card: Card) -> tuple[list[str], dict[str, str]]:
    """The card's codes (fault table and fix jobs), and its indicator rows as id -> words (G0-checked at load)."""
    codes: list[str] = []
    for c in [c.root for r in card.troubleshooting or [] for c in r.codes] + \
            [c.root for j in card.jobs if j.kind == "fix" for c in j.fault_codes]:
        if c not in codes:
            codes.append(c)
    lights = {r.id: _words(r.indicator) for r in card.troubleshooting or [] if r.indicator is not None}
    return codes, lights


def _words(text: Any) -> str:
    d = dict(cards.lang_items(text))
    return d.get("en") or next(iter(d.values()), "")


def _nullable_enum(values: list[str]) -> dict[str, Any]:
    return {"anyOf": [{"type": "string", "enum": values}, {"type": "null"}]} if values else {"type": "null"}


def response_format(codes: list[str], lights: dict[str, str]) -> dict[str, Any]:
    schema = {"type": "object", "additionalProperties": False, "required": ["code", "row_id", "uncertain"],
              "properties": {"code": _nullable_enum(codes), "row_id": _nullable_enum(list(lights)),
                             "uncertain": {"type": "boolean"}}}
    return {"type": "json_schema", "name": "DisplayRead", "schema": schema, "strict": True}


def prompt(codes: list[str], lights: dict[str, str]) -> str:
    listed = "\n".join(f"- {c}" for c in codes) or "- (none)"
    lit = "\n".join(f"- {rid}: {words}" for rid, words in lights.items()) or "- (none)"
    return f"{PROMPT}\nCodes:\n{listed}\n\nLights and symbols:\n{lit}\n"


async def read(jpeg: bytes, card: Card, *, client: calls.Client, lane: str, household: str,
               household_cap_usd: float | None) -> dict[str, Any]:
    """The model's read, checked against the card: {code, row_id, uncertain}; both null means uncertain."""
    codes, lights = choices(card)
    r = await client.respond(ROLE, prompt(codes, lights), images=[jpeg], format=response_format(codes, lights),
                             lane=lane, household=household, household_cap_usd=household_cap_usd)
    data = r.parsed() if isinstance(r.parsed(), dict) else {}
    code = data.get("code") if data.get("code") in codes else None
    row_id = data.get("row_id") if data.get("row_id") in lights else None
    uncertain = bool(data.get("uncertain")) or (code is None and row_id is None)
    return {"code": code, "row_id": row_id, "uncertain": uncertain}


def answer(card: Card, raw: Card | None, got: dict[str, Any], lang: str | None, *, renter: bool,
           country: str, drawn: faults.Drawn = frozenset()) -> dict[str, Any]:
    """FaultMeaning for what was read: a code runs the typed-code lookup; a light is its row as the match."""
    if got["code"] is not None:
        out = faults.meaning(card, raw, got["code"], lang, renter=renter, country=country, drawn=drawn)
    else:
        out = {"code": None, "matches": [], "candidates": [], "refused": [], "notice": None, "landlord": False,
               "table_page": faults.table_page(raw if raw is not None else card)}
        row = next((r for r in card.troubleshooting or [] if r.id == got["row_id"]), None)
        if row is not None:
            m = faults.row_match(row, None, {j.id: j for j in card.jobs}, {q.id: q for q in card.quotes}, drawn)
            out["matches"], out["landlord"] = [m], faults.landlord(card, [m], renter, country)
            if row.remedy_kind != "user" and m["code"]:
                out["refused"] = [m["code"]]
        else:
            out["notice"] = UNREAD.get(lang or "en") or UNREAD["en"]
    return {**out, "read": got}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m app.display")
    ap.add_argument("cmd", choices=["untrace"])
    ap.parse_args(argv)
    if config.private_dir() is None:
        raise SystemExit("FIXIT_PRIVATE is not set")
    print(f"moved {untrace()} display photo(s) that name no household aside, kept")


if __name__ == "__main__":
    main()

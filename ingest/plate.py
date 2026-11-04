"""The plate read: Spark reads the type plate, code decides what to trust.

A probe of the model found 0 invented fields in 102 reads; the misses were look-alike glyphs only
(`El 2` → `EI2` 7/7, O ↔ 0 1/3). So code:
- keeps only the fields the model returned (never fills one in);
- a reading that fits the brand's model-code pattern: expands the look-alikes the probe saw (l/I/1, O/0) and keeps
  the spellings that fit; a reading that does not fit: expands the wider table (plus S/5, B/8, Z/2, G/6) to repair
  it. Spaces are ignored when matching;
- one fitting spelling: take it. A known card for one of several: take that. Several: those characters go amber
  ("Letter O or zero?") and the user confirms with one tap. None: "closer photo", never a guess.

The serial is never asked for, so it never reaches a card, a fixture or a log line.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

import calls

OBSERVED: list[set[str]] = [{"l", "I", "1"}, {"O", "0"}]  # the probe's misreads: amber when the pattern allows both
REPAIR: list[set[str]] = [*OBSERVED, {"S", "5"}, {"B", "8"}, {"Z", "2"}, {"G", "6"}]  # only to fix a failing read
MAX_AMBER = 2  # more uncertain characters than this asks for a closer photo
MAX_VARIANTS = 4096

# Model-code patterns on the compact code (spaces removed, upper-cased). Loose on purpose: they exist to reject a
# reading no maker prints, and to choose between look-alike spellings.
BRAND_PATTERNS: dict[str, re.Pattern[str]] = {
    # E-Nr: SMV4HAX40N/17, WAT28400NL/22, SN23HI60AE
    "bosch": re.compile(r"^[A-Z]{2,3}[0-9]{1,2}[A-Z0-9]{3,7}(/[0-9]{2})?$"),
    "siemens": re.compile(r"^[A-Z]{2,3}[0-9]{1,2}[A-Z0-9]{3,7}(/[0-9]{2})?$"),
    # WW90T534AAW/S2, WW11DG5B25ABEU, DV90T6240LH
    "samsung": re.compile(r"^(WW|WD|WF|DV|DW|RB|RS|RF|RR|NV|NZ|NQ|MS|MG)[0-9]{1,2}[A-Z0-9]{4,12}(/[A-Z0-9]{2,3})?$"),
    # WWD120WCS, G7110SCI, WA75T1/EL2
    "miele": re.compile(r"^[A-Z]{1,4}[0-9]{2,5}[A-Z0-9/.]{0,10}$"),
    # L7FEE865R, FSE63716P
    "aeg": re.compile(r"^[A-Z]{1,4}[0-9][A-Z0-9]{3,10}$"),
}
# A typed model code with no brand: the makers' code shapes, most specific first. BSH codes for the Bosch brand start
# with three letters (SMV4…, WAT28…, KGN39…), for Siemens with two (SN23…, WM14…, KG39…); Samsung's two letters are
# its own list, Miele's washers and dryers W/T plus letters, AEG's a letter and a digit or F[SF] dishwashers.
BRAND_SHAPES: list[tuple[str, re.Pattern[str]]] = [
    ("samsung", re.compile(r"^(WW|WD|WF|DV|DW|RB|RS|RF|RR|NV|NZ|NQ|MS|MG)[0-9]")),
    ("miele", re.compile(r"^((WW|WC|WE|WS|WT|TW|TC|TE|TS)[A-Z][0-9]{3}|[GHK][0-9]{4})")),
    ("aeg", re.compile(r"^([LT][0-9][A-Z]|F[SF][A-Z][0-9])")),
    ("bosch", re.compile(r"^[A-Z]{3}[0-9]")),
    ("siemens", re.compile(r"^[A-Z]{2}[0-9]")),
]


def guess_brand(model_code: str) -> str | None:
    """The maker a typed model code belongs to, from its shape; None when no shape fits."""
    code = compact(model_code)
    for brand, shape in BRAND_SHAPES:
        if shape.match(code) and BRAND_PATTERNS[brand].match(code):
            return brand
    return None


BRAND_ALIASES = {"bsh": "bosch", "robert bosch": "bosch", "samsung electronics": "samsung", "electrolux aeg": "aeg"}

PLATE_SCHEMA: dict[str, Any] = {
    "type": "object", "additionalProperties": False,
    "required": ["plate_visible", "brand", "model_code", "product_code", "unreadable_fields"],
    "properties": {
        "plate_visible": {"type": "boolean"},
        "brand": {"anyOf": [{"type": "null"}, {"type": "string"}]},
        "model_code": {"anyOf": [{"type": "null"}, {"type": "string"}]},
        "product_code": {"anyOf": [{"type": "null"}, {"type": "string"}]},
        "unreadable_fields": {"type": "array", "items": {"type": "string"}},
    },
}
PLATE_FORMAT = {"type": "json_schema", "name": "PlateRead", "schema": PLATE_SCHEMA, "strict": True}
PLATE_PROMPT = """\
This photo should show the type plate (rating label) of a home appliance. Transcribe exactly what is printed:
- brand: the maker's name as printed;
- model_code: the model or type code (on Bosch and Siemens plates the E-Nr; on Samsung "Model"; on Miele the type);
- product_code: a product number if printed separately (Electrolux/AEG PNC); else null.
Copy characters exactly, keeping spaces and slashes. Never guess: if a field is not printed, or you cannot read every
character of it, give null and name it in unreadable_fields. Do not transcribe the serial number or any other field.
"""


@dataclass(frozen=True)
class Uncertain:
    index: int  # character index in the value
    options: list[str]


@dataclass(frozen=True)
class PlateField:
    name: str
    value: str
    uncertain: list[Uncertain] = field(default_factory=list)


@dataclass(frozen=True)
class PlateCard:
    brand: str | None
    model: str | None
    fields: list[PlateField]
    amber_chars: list[int]  # indices into `model` the user must confirm
    confirm_needed: bool
    closer_photo: bool
    reason: str  # "ok", "amber", "pattern", "too_uncertain", "no_plate", "unreadable"

    def ws_fields(self) -> list[dict[str, Any]]:
        """The `fields` of the ws PlateRead message."""
        return [{"name": f.name, "value": f.value,
                 "uncertain": [{"index": u.index, "options": u.options} for u in f.uncertain]} for f in self.fields]


def brand_slug(brand: str | None) -> str | None:
    if not brand or not brand.strip():
        return None
    b = re.sub(r"[^a-z0-9 ]+", "", brand.lower()).strip()
    b = BRAND_ALIASES.get(b, b)
    for known in BRAND_PATTERNS:
        if b == known or b.startswith(known + " "):
            return known
    return re.sub(r"\s+", "-", b) or None


def compact(code: str) -> str:
    return re.sub(r"\s+", "", code).upper()


def _options(ch: str, groups: list[set[str]]) -> list[str]:
    for group in groups:
        if ch in group:
            return sorted({c.upper() if c.isalpha() else c for c in group})
    return [ch.upper()]


def variants(code: str, groups: list[set[str]] = OBSERVED) -> list[str]:
    """Every look-alike spelling of the compact code."""
    choices = [_options(ch, groups) for ch in re.sub(r"\s+", "", code)]
    total = 1
    for ch in choices:
        total *= len(ch)
    if total > MAX_VARIANTS:
        return []
    return ["".join(v) for v in itertools.product(*choices)]


def _amber(read: str, fitting: list[str]) -> tuple[str, list[Uncertain]]:
    """The model's spelling (upper-cased, spaces kept) with the characters where fitting spellings disagree."""
    raw_index = [i for i, ch in enumerate(read) if not ch.isspace()]
    first = fitting[0]
    out = list(read.upper())
    uncertain = []
    for k in range(len(first)):
        opts = sorted({v[k] for v in fitting})
        if len(opts) > 1:
            i = raw_index[k]
            uncertain.append(Uncertain(i, opts))
        elif out[raw_index[k]] != opts[0]:
            out[raw_index[k]] = opts[0]
    return "".join(out), uncertain


def resolve(brand: str | None, model_code: str, known_models: Iterable[str] = ()) -> PlateCard:
    """Code's half of the plate read, on the model's transcription."""
    pattern = BRAND_PATTERNS.get(brand or "")
    if pattern is None:
        fitting = [compact(model_code)]  # an unknown maker: no pattern to choose with, so take the reading as read
    else:
        groups = OBSERVED if pattern.match(compact(model_code)) else REPAIR
        fitting = [v for v in variants(model_code, groups) if pattern.match(v)]
    fields = [PlateField("brand", brand)] if brand else []
    if not fitting:
        return PlateCard(brand, None, fields, [], False, True, "pattern")
    known = {compact(k) for k in known_models}
    hits = [v for v in fitting if v in known]
    if len(hits) == 1:
        fitting = hits
    value, uncertain = _amber(model_code, fitting)
    if len(uncertain) > MAX_AMBER:
        return PlateCard(brand, None, fields, [], False, True, "too_uncertain")
    fields.append(PlateField("model", value, uncertain))
    amber = [u.index for u in uncertain]
    return PlateCard(brand, value, fields, amber, bool(amber), False, "amber" if amber else "ok")


async def read_plate(photo: bytes, *, lane: str = "l1", household: str | None = None,
                     session_id: str | None = None, known_models: Iterable[str] = (),
                     client: calls.Client | None = None, household_cap_usd: float | None = None) -> PlateCard:
    cl = client or calls.default_client()
    r = await cl.respond("plate", PLATE_PROMPT, images=[photo], format=PLATE_FORMAT, lane=lane,
                         household=household, session_id=session_id, household_cap_usd=household_cap_usd)
    data = r.parsed()
    brand = brand_slug(data.get("brand"))
    model_code = (data.get("model_code") or "").strip()
    if not data.get("plate_visible"):
        return PlateCard(brand, None, [PlateField("brand", brand)] if brand else [], [], False, True, "no_plate")
    if not model_code:
        return PlateCard(brand, None, [PlateField("brand", brand)] if brand else [], [], False, True, "unreadable")
    return resolve(brand, model_code, known_models)

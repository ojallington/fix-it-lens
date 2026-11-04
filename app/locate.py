"""Locate once, then track.

- Function-named parts (the filter system, the drain cap, the gas tap) are located by Spark as a box from the card's
  wording and a visible cue; SAM 3.1 masks from that box.
- Appearance-named parts (a hand, a plug, a socket, the display) go to SAM by text.
- A mask under 0.6 falls back to Spark's box, drawn larger.
- A locate with no box asks for a better view and draws nothing; it never guesses.
- SAM is never the authority on a red zone: a red-zone part is never segmented by text, and its label comes from
  Spark's visible cue and the card. With no cue seen, it reads "possible <family>: don't touch".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import calls
from app import config
from app.frames import StoredFrame
from app.geometry import Box, Region, box_overlap, box_valid, grow
from app.models import Models
from contracts.models.card import Card, Part

LOCATE_FORMAT: dict[str, Any] = {
    "type": "json_schema", "name": "Locate", "strict": True,
    "schema": {
        "type": "object", "additionalProperties": False, "required": ["found", "cue_seen", "box", "note"],
        "properties": {
            "found": {"type": "boolean"},
            "cue_seen": {"type": "boolean", "description": "true only if the visible cue described is in the image"},
            "box": {"anyOf": [{"type": "null"}, {"type": "array", "items": {"type": "integer", "minimum": 0,
                                                                              "maximum": 1000},
                                                  "minItems": 4, "maxItems": 4}]},
            "note": {"type": "string", "maxLength": 200},
        },
    },
}


def part_name(part: Part, lang: str) -> str:
    return part.names.root.get(lang) or part.names.root.get("en") or next(iter(part.names.root.values()))


def locate_input(part: Part, frame: StoredFrame, lang: str) -> list[dict[str, Any]]:
    from calls.images import prepare

    im = prepare(frame.jpeg)
    ask = (f"You are an object grounding expert. Find the {part_name(part, 'en')} in this image. "
           f"What to look for: {part.cue}. Give its box as [x_min, y_min, x_max, y_max] on a 0-1000 grid "
           "(x right, y down). Set cue_seen only if that visible cue is in the image. If it is not visible, "
           "found = false and box = null. Never guess a box.")
    return [{"type": "message", "role": "user", "content": [
        {"type": "input_text", "text": ask},
        {"type": "input_image", "image_url": im.data_url(), "detail": "high"}]}]


@dataclass(frozen=True)
class Located:
    found: bool
    box: Box | None
    cue_seen: bool
    note: str
    ms: float
    error: str | None = None  # timeout | failed | malformed


async def locate(models: Models, part: Part, frame: StoredFrame, lang: str) -> Located:
    try:
        r, ms = await models.respond("locate", locate_input(part, frame, lang), format=LOCATE_FORMAT,
                                     effort="minimal")
    except calls.CallTimeout:
        return Located(False, None, False, "", config.CALL_TIMEOUT_S * 1000, "timeout")
    except calls.CallError as e:
        return Located(False, None, False, str(e)[:120], 0.0, "failed")
    try:
        data = r.parsed()
    except ValueError:
        return Located(False, None, False, "", ms, "malformed")
    box = data.get("box") if isinstance(data, dict) else None
    if not (isinstance(data, dict) and data.get("found") is True and box_valid(box)):
        return Located(False, None, False, str((data or {}).get("note", ""))[:200] if isinstance(data, dict) else "",
                       ms)
    assert box is not None
    return Located(True, (box[0], box[1], box[2], box[3]), bool(data.get("cue_seen")), str(data.get("note", ""))[:200],
                   ms)


@dataclass(frozen=True)
class Outlined:
    region: Region | None
    count: int
    source: Literal["sam", "box", "none"]
    ms: float
    note: str = ""
    failed: bool = False  # SAM itself failed (timeout, unavailable), as opposed to finding nothing


def _best(res: calls.SamResult, hint: Box | None) -> tuple[calls.SamObject | None, int]:
    objs = [o for o in res.objects if o.frame in (None, 0)]
    if not objs:
        return None, 0
    if hint is not None:
        objs.sort(key=lambda o: (-box_overlap(res.box_1000(o), hint), -(o.conf or 0)))
    else:
        objs.sort(key=lambda o: -(o.conf or 0))
    return objs[0], len(objs)


async def segment(models: Models, frame: StoredFrame, *, box: Box | None = None, text: str | None = None) -> Outlined:
    """SAM from a box (with the 0.6 fallback to the box, drawn larger) or by a short noun phrase."""
    if box is None and not text:
        return Outlined(None, 0, "none", 0.0, "nothing to segment")
    prompt: str | calls.Box = box if box is not None else str(text)
    try:
        res, ms = await models.sam_image(frame, prompt)
    except calls.CallError as e:
        if box is not None:
            return Outlined(Region(frame.frame_id, frame.width, frame.height, grow(box, config.BOX_FALLBACK_GROW),
                                   source="box"), 1, "box", 0.0, f"SAM {type(e).__name__}; Spark's box")
        return Outlined(None, 0, "none", 0.0, f"SAM {type(e).__name__}", failed=True)
    obj, count = _best(res, box)
    if obj is None or (obj.conf or 0) < config.MASK_MIN_CONF or not obj.mask_svg:
        if box is not None:
            return Outlined(Region(frame.frame_id, frame.width, frame.height, grow(box, config.BOX_FALLBACK_GROW),
                                   conf=obj.conf if obj else None, source="box"), count, "box", ms,
                            "mask under 0.6; Spark's box, drawn larger")
        return Outlined(None, count, "none", ms, "no confident match")
    # SAM's mask is in the sent image's pixels; Region keeps those and maps the 0-1000 grid onto them
    region = Region.from_mask(frame.frame_id, res.width, res.height, res.box_1000(obj), obj.mask_svg, obj.conf)
    return Outlined(region, count, "sam", ms)


def red_zone_rule(card: Card, part_id: str) -> str | None:
    for z in card.red_zones:
        if z.part_id == part_id:
            return z.rule_id
    return None


ZONE_LABEL = {"en": ("{family}: don't touch", "possible {family}: don't touch"),
              "nl": ("{family}: niet aankomen", "mogelijk {family}: niet aankomen")}
FAMILY_WORD = {"en": {"mains": "mains", "gas": "gas", "water": "water", "danger": "danger"},
               "nl": {"mains": "stroom", "gas": "gas", "water": "water", "danger": "gevaar"}}


def outline_label(card: Card, part: Part, lang: str, cue_seen: bool) -> str:
    """The label drawn on an outline. A red zone's label comes from the card's rule and Spark's visible cue, never
    from SAM, and names only the rule's family: naming the part ("gas tap") is itself held by G0."""
    if part.kind != "red_zone":
        return part_name(part, lang)
    rule = red_zone_rule(card, part.id) or ""
    family = rule.split(".")[1] if rule.count(".") == 2 else "danger"
    lang = lang if lang in ZONE_LABEL else "en"
    word = FAMILY_WORD[lang].get(family, family)
    return ZONE_LABEL[lang][0 if cue_seen else 1].format(family=word)


def needs_spark(part: Part) -> bool:
    """Red zones and function-named parts go to Spark for a box; appearance-named ones go to SAM by text."""
    return part.kind == "red_zone" or part.locate == "function"


async def outline(models: Models, part: Part, frame: StoredFrame, lang: str) -> tuple[Outlined, Located | None]:
    """Locate once, then segment. A function-named part: Spark's box, then SAM from it. An appearance part: SAM by
    text; when that finds nothing confident, SAM from the locate role's box, and failing that (or when SAM itself
    failed) the box, drawn larger: the phone draws it and the check reads it. A red zone is never outlined by text."""
    if needs_spark(part):
        found = await locate(models, part, frame, lang)
        if not found.found or found.box is None:
            return Outlined(None, 0, "none", found.ms, "no box"), found
        return await segment(models, frame, box=found.box), found
    if part.kind == "red_zone":
        return Outlined(None, 0, "none", 0.0, "a red zone is never outlined by text"), None
    out = await segment(models, frame, text=part.sam_text)
    if out.region is not None:
        return out, None
    found = await locate(models, part, frame, lang)
    if not found.found or found.box is None:
        return out, found
    if not out.failed:  # SAM answered but not to the words: it may still outline the part from the locate box
        boxed = await segment(models, frame, box=found.box)
        return Outlined(boxed.region, boxed.count, boxed.source, out.ms + found.ms + boxed.ms,
                        f"{out.note}; SAM from the locate box" if boxed.source == "sam" else
                        f"{out.note}; {boxed.note}", boxed.failed), found
    region = Region(frame.frame_id, frame.width, frame.height, grow(found.box, config.BOX_FALLBACK_GROW), source="box")
    return Outlined(region, 1, "box", out.ms + found.ms, f"{out.note}; the locate box"), found

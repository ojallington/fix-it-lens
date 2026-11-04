"""The shared contracts. The JSON Schemas in this folder are the source of truth; `contracts.models` holds the
pydantic v2 models generated from them (`make contracts`), and `contracts/ts/src` the TypeScript types.

    from contracts.models.card import Card
    from contracts import validate
    validate("ws", message, "Held")      # raises jsonschema.ValidationError
    card = load_card(yaml.safe_load(text))  # pydantic Card plus the invariants a schema cannot state

Invariants (code, both languages; contracts/ts/src/validate.ts mirrors them):
- card: step ids are unique across the whole card, not only within a job.
"""

from __future__ import annotations

import json
from collections import Counter
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator, FormatChecker, ValidationError
from referencing import Registry, Resource

if TYPE_CHECKING:
    from contracts.models.card import Card

HERE = Path(__file__).resolve().parent
VERSION = (HERE / "VERSION").read_text().strip()
NAMES = ("card", "rules", "g1", "safety", "tools", "judge", "ws", "session", "ledger", "claim", "feedback")
LOOKUP_MAKERS: tuple[str, ...] = tuple(json.loads((HERE / "lookup_makers.json").read_text())["makers"])
STORE_SQL = HERE / "store.sql"
OPENAPI = HERE / "api.openapi.yaml"
MCP_TOOLS = HERE / "mcp_tools.json"


def schema_path(name: str) -> Path:
    return HERE / f"{name}.schema.json"


@cache
def schema(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(schema_path(name).read_text())
    return data


@cache
def _registry() -> Registry:
    resources = [(schema(n)["$id"], Resource.from_contents(schema(n))) for n in NAMES]
    return Registry().with_resources(resources)


@cache
def validator(name: str, definition: str | None = None) -> Draft202012Validator:
    """A validator for a whole schema, or for one of its $defs."""
    root = schema(name)
    target: dict[str, Any] = root if definition is None else {"$ref": f"{root['$id']}#/$defs/{definition}"}
    return Draft202012Validator(target, registry=_registry(), format_checker=FormatChecker())


class ContractError(ValidationError):
    """An invariant the JSON Schema cannot state failed."""


def card_invariants(card: Any) -> list[str]:
    """What is wrong with a card beyond its schema (a dict or a contracts.models.card.Card)."""
    data = card.model_dump(mode="json") if hasattr(card, "model_dump") else card
    seen = Counter(str(s.get("id")) for j in data.get("jobs") or [] for s in j.get("steps") or [])
    out = [f"step id {sid!r} is used {n} times; step ids are unique per card" for sid, n in sorted(seen.items())
           if n > 1]
    return (out + _fixit_invariants(data) + _troubleshooting_invariants(data) + _needs_invariants(data)
            + _camera_invariants(data))


def _fixit_invariants(data: dict[str, Any]) -> list[str]:
    """A fixit step (ours, uncited) only undoes an earlier power-off step of its own job (its plug, switch, water or a
    part it took out), after every power-off step, and at most once per step it restores."""
    out: list[str] = []
    for job in data.get("jobs") or []:
        steps = job.get("steps") or []
        restored = Counter(str(s.get("restores")) for s in steps if s.get("origin") == "fixit")
        out += [f"job {job.get('id')!r}: step {r!r} is restored {n} times" for r, n in sorted(restored.items())
                if n > 1]
        for i, s in enumerate(steps):
            if s.get("origin") != "fixit":
                continue
            earlier = {str(t.get("id")): t for t in steps[:i]}
            target = earlier.get(str(s.get("restores")))
            if target is None or target.get("power") != "off":
                out.append(f"step {s.get('id')!r}: restores {s.get('restores')!r}, which is not an earlier power-off "
                           "step of its job")
            if any(t.get("power") == "off" for t in steps[i + 1:]):
                out.append(f"step {s.get('id')!r}: a fixit step comes after every power-off step of its job")
    return out


def _troubleshooting_invariants(data: dict[str, Any]) -> list[str]:
    """Every row's references resolve: quotes exist, job_ref is a fix job, prevented_by names upkeep jobs."""
    rows = data.get("troubleshooting") or []
    quotes = {str(q.get("id")) for q in data.get("quotes") or []}
    kinds = {str(j.get("id")): j.get("kind") for j in data.get("jobs") or []}
    out = [f"troubleshooting id {rid!r} is used {n} times" for rid, n in
           sorted(Counter(str(r.get("id")) for r in rows).items()) if n > 1]
    for r in rows:
        rid = r.get("id")
        refs = [r.get("quote_ref"), *(t.get("quote_ref") for t in r.get("prevented_by") or [])]
        if r.get("repeat"):
            refs.append(r["repeat"].get("quote_ref"))
        out += [f"troubleshooting {rid!r}: quote_ref {q!r} is not in quotes" for q in refs if q not in quotes]
        if r.get("job_ref") is not None and kinds.get(str(r["job_ref"])) != "fix":
            out.append(f"troubleshooting {rid!r}: job_ref {r['job_ref']!r} is not a fix job on this card")
        out += [f"troubleshooting {rid!r}: prevented_by {t.get('job_id')!r} is not an upkeep job on this card"
                for t in r.get("prevented_by") or [] if kinds.get(str(t.get("job_id"))) != "upkeep"]
    return out


def _needs_invariants(data: dict[str, Any]) -> list[str]:
    """A job's needs have unique ids within the job, and each one's quote_ref is in quotes."""
    quotes = {str(q.get("id")) for q in data.get("quotes") or []}
    out: list[str] = []
    for job in data.get("jobs") or []:
        needs = job.get("needs") or []
        out += [f"job {job.get('id')!r}: need id {nid!r} is used {n} times" for nid, n in
                sorted(Counter(str(x.get("id")) for x in needs).items()) if n > 1]
        out += [f"job {job.get('id')!r}: need {x.get('id')!r}: quote_ref {x.get('quote_ref')!r} is not in quotes"
                for x in needs if x.get("quote_ref") not in quotes]
    return out


def _camera_invariants(data: dict[str, Any]) -> list[str]:
    """A step without the camera (camera none) has no zones, zone rings, red-zone part or power on or off; a find step
    has a part to find."""
    red = {str(p.get("id")) for p in data.get("parts") or [] if p.get("kind") == "red_zone"}
    red |= {str(z.get("part_id")) for z in data.get("red_zones") or []}
    out: list[str] = []
    for job in data.get("jobs") or []:
        for s in job.get("steps") or []:
            cam, sid = s.get("camera") or "check", s.get("id")
            if cam == "none":
                why = [w for w, hit in (("zones", s.get("zones")), ("zone rings", s.get("zone_rings")),
                                        ("a red-zone part", red & {str(p) for p in s.get("parts") or []}),
                                        (f"power {s.get('power')}", s.get("power") in ("on", "off"))) if hit]
                out += [f"step {sid!r}: camera none, but it has {w}" for w in why]
            if cam == "find" and not s.get("parts"):
                out.append(f"step {sid!r}: camera find, but it names no part to find")
    return out


def cite_args_invariants(args: Any) -> list[str]:
    """CiteArgs is exactly one shape: {step_id} (page and quote null) or {page, quote} (step_id null)."""
    data = args.model_dump(mode="json") if hasattr(args, "model_dump") else args
    step = data.get("step_id") is not None
    manual = [data.get(k) is not None for k in ("page", "quote")]
    if step and not any(manual):
        return []
    if not step and all(manual):
        return []
    return ["cite takes step_id alone, or page and quote together"]


# keyed "<schema>" for a whole document, "<schema>#<definition>" for one definition
INVARIANTS = {"card": card_invariants, "tools#CiteArgs": cite_args_invariants}


def validate(name: str, instance: Any, definition: str | None = None) -> None:
    validator(name, definition).validate(instance)
    check = INVARIANTS.get(name if definition is None else f"{name}#{definition}")
    problems = check(instance) if check else []
    if problems:
        raise ContractError("; ".join(problems))


def load_card(data: Any) -> Card:
    """A card from parsed YAML or JSON: the pydantic model, then the invariants. Raises ContractError."""
    from contracts.models.card import Card

    card = Card.model_validate(data)
    problems = card_invariants(card)
    if problems:
        raise ContractError("; ".join(problems))
    return card

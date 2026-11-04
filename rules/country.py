"""Country files: `rules/<country>.yaml`, checked against `contracts/rules.schema.json` and again in code.

A country with no file fails closed (`FAIL_CLOSED`): no gas or mains guidance, the international emergency advice,
and a prompt to contribute the file. An answer language with no table in the file fails closed the same way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

import yaml

from contracts import validate
from contracts.models.rules import CountryRules, Rule

HERE = Path(__file__).resolve().parent
RING_FLOOR_CM = 3.0
RULE_ID = re.compile(r"^[a-z]{2}\.[a-z]+\.[a-z_]+$")
# Locales say GB; the file, and its rule IDs, say uk (the design's country table).
ALIASES = {"gb": "uk"}

# Rules whose part names are things a person says when something is already wrong (a gas smell, a CO alarm, smoke,
# water on the socket). On these the user's own words stop the repair (`check_utterance`). Every other rule acts on
# what the model writes and on the card's steps.
UTTERANCE_ZONES = frozenset({"smell", "alarm", "fire", "burning", "flood", "breaker_repeat"})

INTERNATIONAL_ADVICE = (
    "In an emergency, call 112 (in Europe) or your local emergency number. If you smell gas, leave the building "
    "and call your gas emergency line from outside."
)
CONTRIBUTE = "There is no safety rules file for this yet; you can add one (see schema/rules.md)."


class RulesError(ValueError):
    """A country file that must not load. The build fails on it; nothing falls back to a weaker table."""


@dataclass(frozen=True)
class Country:
    code: str
    rules: CountryRules
    by_id: dict[str, Rule] = field(repr=False)

    @property
    def languages(self) -> list[str]:
        return [lang.root for lang in self.rules.languages]

    def rule(self, rule_id: str) -> Rule | None:
        return self.by_id.get(rule_id)

    def emergency(self, lang: str) -> str:
        texts = self.rules.emergency.root
        return texts.get(lang) or texts.get("en") or INTERNATIONAL_ADVICE

    @property
    def allowed_boiler_actions(self) -> frozenset[str]:
        return frozenset(a.id for a in self.rules.allow_lists.sealed_boiler)

    def landlord_rules(self) -> list[Rule]:
        """The rules this file lists as the landlord's job for a renter."""
        return [r for r in self.rules.rules if r.landlord]


def check(data: dict[str, Any], code: str | None = None) -> Country:
    """Validate a parsed country file, schema first and then the code-level rules the schema cannot say."""
    try:
        validate("rules", data)
    except Exception as e:  # jsonschema.ValidationError, kept as one error type for callers
        raise RulesError(f"country file fails the schema: {e}") from e
    rules = CountryRules.model_validate(data)
    country = rules.country
    if code is not None and country != code:
        raise RulesError(f"file for {code} says country {country}")
    by_id: dict[str, Rule] = {}
    for r in rules.rules:
        if not RULE_ID.match(r.id):
            raise RulesError(f"{r.id}: rule IDs are <country>.<family>.<zone>")
        if r.id != f"{country}.{r.family}.{r.zone}":
            raise RulesError(f"{r.id}: the ID must be {country}.{r.family}.{r.zone}")
        if r.id in by_id:
            raise RulesError(f"{r.id}: duplicate rule ID")
        # 3 cm is a floor in code, whatever the schema says.
        if r.ring_cm < RING_FLOOR_CM:
            raise RulesError(f"{r.id}: ring_cm {r.ring_cm} is below the {RING_FLOOR_CM} cm floor")
        langs = [lang.root for lang in rules.languages]
        for lang in langs:
            if r.action != "ALLOW" and (lang not in r.line.root or lang not in r.script.root):
                raise RulesError(f"{r.id}: no line or script in {lang}, which the file says it covers")
            if not r.parts.root.get(lang):
                raise RulesError(f"{r.id}: no part names in {lang}, which the file says it covers")
        by_id[r.id] = r
    return Country(country, rules, by_id)


def country_path(code: str) -> Path:
    return HERE / f"{code}.yaml"


@cache
def load_country(code: str) -> Country | None:
    """The country's rules, or None when there is no file (callers then fail closed)."""
    if not re.fullmatch(r"[a-z]{2}", code):
        return None
    code = ALIASES.get(code, code)
    path = country_path(code)
    if not path.exists():
        return None
    return check(yaml.safe_load(path.read_text()), code)


def shipped() -> list[str]:
    return sorted(p.stem for p in HERE.glob("*.yaml") if re.fullmatch(r"[a-z]{2}", p.stem))


def no_country_line(lang: str) -> str:
    return f"I have no safety rules for this country, so I won't guide gas or mains work. {INTERNATIONAL_ADVICE} " \
        f"{CONTRIBUTE}"


def no_language_line(lang: str) -> str:
    return f"I have no safety rules in this language ({lang}) yet, so I can't give repair steps in it. " \
        f"{INTERNATIONAL_ADVICE} {CONTRIBUTE}"

"""check_step: may this card step be guided now? OK | HOLD{precondition} | STOP{rule_id}.

In order, the first answer wins:

1. The step's own text hits a STOP or HOLD rule (a step that would open a casing or touch gas) -> STOP. The casing
   fires whatever the power state: an unplugged washer's back panel is still a STOP. A step that only wipes the
   casing's outside with a dry or damp cloth (G0's `outside_wipe`, the whole text) does not fire the casing.
2. On a sealed boiler, a job that is not on the country's allow-list -> STOP. Repeated top-ups -> STOP.
3. The pressure, during a boiler fill: at or above the maximum -> STOP; at or above the card's target -> HOLD.
4. A breaker step after a second trip -> STOP.
5. A step near a red zone needs the zone outlined, a side view and its scale; a ring step needs the user to have
   heard the warning -> HOLD.
6. A `power: off` step needs the plug seen out, and 0 W where Home Assistant has a power entity -> HOLD.
7. A stale frame -> HOLD.

Power off is evidence, not a word: nothing here reads what the model says about it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from contracts.models.card import Card, Job, Step
from rules import text as tx
from rules.country import Country, load_country
from rules.g0 import CASING_ZONES, match_rule, no_gas_names, outside_wipe, resolve

Precondition = Literal[
    "side_view", "power_off_seen", "hand_visible", "fresh_frame", "zone_outlined", "view_scale", "user_confirm"
]
MAX_BAR = 3.0
TOP_UPS_PER_YEAR = 2  # more than this in 12 months means a leak: call the engineer (Remeha "2 keer per jaar")


@dataclass(frozen=True)
class Evidence:
    """What code has seen. Every field defaults to 'not seen', so missing evidence holds."""

    plug_out_seen: bool = False  # SAM saw the plug out of the socket
    ha_power_w: float | None = None  # None: no power entity in HA; the camera alone decides
    view: Literal["front", "side"] | None = None
    zone_outlined: bool = False
    view_scale_ok: bool = False
    frame_fresh: bool = True
    ring_warning_confirmed: bool = False
    pressure_bar: float | None = None
    pressure_target_bar: float | None = None  # from the card's brand row
    breaker_trips: int = 0  # this session
    top_ups_12_months: int = 0  # household memory, not counting today's


@dataclass(frozen=True)
class StepOk:
    state: Literal["OK"] = "OK"


@dataclass(frozen=True)
class StepHold:
    precondition: Precondition
    rule_id: str | None = None
    detail: str = ""
    state: Literal["HOLD"] = "HOLD"


@dataclass(frozen=True)
class StepStop:
    rule_id: str
    evidence: str
    state: Literal["STOP"] = "STOP"


StepResult = StepOk | StepHold | StepStop


def job_of(step: Step, card: Card) -> Job | None:
    return next((j for j in card.jobs if any(s is step or s.id == step.id for s in j.steps)), None)


def boiler_action(job: Job, c: Country) -> str | None:
    """The allow-list action a boiler job is: its id is the action's id, or starts with it and '_'."""
    return next((a for a in sorted(c.allowed_boiler_actions) if job.id == a or job.id.startswith(a + "_")), None)


def _rid(c: Country, family_zone: str) -> str:
    rule = c.rule(f"{c.code}.{family_zone}")
    return rule.id if rule else f"{c.code}.{family_zone}"


def text_stop(step: Step, c: Country, kind: str | None = None) -> StepStop | None:
    """A STOP or HOLD rule hit in the step's own text, in any language the card writes it in. `kind` is the card's
    appliance kind: on one with no gas, a gas-tap colour name names some other part (g0.no_gas_names)."""
    for lang, text in step.text.root.items():
        if lang not in c.languages:
            continue
        digest = tx.line_hash(text)
        norm = tx.normalise(text)
        for rule in c.rules.rules:
            if rule.action == "ALLOW":
                for p in rule.parts.root.get(lang, []):
                    norm = tx.mask(norm, p.root)
        for name in no_gas_names(kind, lang, c, text):
            norm = tx.mask(norm, name)
        wipe = outside_wipe(norm, lang)
        for rule in c.rules.rules:
            if wipe and rule.id.split(".", 1)[-1] in CASING_ZONES:
                continue
            # Only the country file's own look-only hashes exempt a line; the card cannot exempt itself.
            if digest in {h.root for h in rule.look_only_exemptions}:
                continue
            if rule.action != "ALLOW" and match_rule(norm, rule, lang):
                return StepStop(rule.id, f"step {step.id} text ({lang}) matches {rule.id}")
    return None


def guarded(step: Step, card: Card) -> bool:
    """A stop or hold in the rules can guard this step: it is unplugged, near a red zone, or in a boiler fill."""
    job = job_of(step, card)
    return (
        step.power == "off"
        or bool(step.zones)
        or bool(step.zone_rings)
        or (card.appliance_kind == "boiler" and job is not None and job.id.startswith("top_up"))
    )


def check_step(step: Step, card: Card, evidence: Evidence, *, country: str | Country) -> StepResult:
    c = country if isinstance(country, Country) else load_country(country)
    if c is None:
        # No country file: no gas or mains guidance at all.
        if card.appliance_kind == "boiler" or step.power == "off" or step.zones:
            return StepStop(f"{country if isinstance(country, str) else 'zz'}.gas.no_rules_file",
                            "no rules file for this country")
        return StepOk()
    job = job_of(step, card)

    hit = text_stop(step, c, card.appliance_kind)
    if hit:
        return hit
    for zone_id in (z.root for z in step.zones):
        rz = next((z for z in card.red_zones if z.id == zone_id), None)
        if rz is None:
            return StepStop(_rid(c, "mechanical.hazard"), f"step {step.id} names unknown red zone {zone_id}")
        if resolve(c, rz.rule_id) is None:
            return StepStop(rz.rule_id, f"red zone {zone_id}: no rule {rz.rule_id} in {c.code}")

    if card.appliance_kind == "boiler":
        action = boiler_action(job, c) if job else None
        if action is None:
            name = job.id if job else "?"
            return StepStop(_rid(c, "gas.boiler"), f"job {name} is not on the sealed-boiler allow-list")
        if action == "top_up" and evidence.top_ups_12_months > TOP_UPS_PER_YEAR:
            return StepStop(_rid(c, "pressure.repeat_top_up"), f"{evidence.top_ups_12_months} top-ups in 12 months")
        if action == "top_up" and evidence.pressure_bar is not None:
            if evidence.pressure_bar >= MAX_BAR:
                return StepStop(_rid(c, "pressure.relief_valve"), f"pressure {evidence.pressure_bar} bar")
            target = evidence.pressure_target_bar or 2.0
            if evidence.pressure_bar >= target:
                return StepHold("user_confirm", _rid(c, "pressure.relief_valve"), "close the tap now: at target")

    if ("breaker" in step.id or (job is not None and "breaker" in job.id)) and evidence.breaker_trips >= 2:
        return StepStop(_rid(c, "mains.breaker_repeat"), f"breaker tripped {evidence.breaker_trips} times")

    if step.zones or step.zone_rings:
        if not evidence.zone_outlined:
            return StepHold("zone_outlined", None, "the red zone must be outlined before the step starts")
        if evidence.view != "side":
            return StepHold("side_view", None, "a step near a red zone starts only from a side view")
        if not evidence.view_scale_ok:
            return StepHold("view_scale", None, "both outlines side by side, with the scale known")
        if step.zone_rings and not evidence.ring_warning_confirmed:
            return StepHold("user_confirm", None, "the zone is a ring on this step: warn before it starts")

    if step.power == "off":
        power_zero = evidence.ha_power_w is None or evidence.ha_power_w == 0
        if not (evidence.plug_out_seen and power_zero):
            return StepHold("power_off_seen", None, "the camera must see the plug out (and HA read 0 W)")

    if not evidence.frame_fresh:
        return StepHold("fresh_frame")
    return StepOk()

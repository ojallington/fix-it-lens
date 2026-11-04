"""The rules check over a card, which the card loader in app/ runs on each card (Card.checks.rules)."""

from __future__ import annotations

from contracts.models.card import Card
from rules.country import RING_FLOOR_CM, Country, load_country
from rules.g0 import resolve
from rules.steps import boiler_action, text_stop


def check_card(card: Card, country: str | Country) -> tuple[list[str], list[str]]:
    """(failures, rule_ids). A card with failures does not load; nothing falls back to a weaker check."""
    c = country if isinstance(country, Country) else load_country(country)
    failures: list[str] = []
    rule_ids: list[str] = []
    zones = {z.id: z for z in card.red_zones}
    parts = {p.id for p in card.parts}
    for z in card.red_zones:
        if z.ring_cm < RING_FLOOR_CM:
            failures.append(f"red zone {z.id}: ring {z.ring_cm} cm is below the {RING_FLOOR_CM} cm floor")
        if z.part_id not in parts:
            failures.append(f"red zone {z.id}: no part {z.part_id}")
        if c is not None:
            rule = resolve(c, z.rule_id)
            if rule is None or rule.action == "ALLOW":
                failures.append(f"red zone {z.id}: {c.code} has no rule for {z.rule_id}")
            else:
                rule_ids.append(rule.id)
                if z.ring_cm < rule.ring_cm and not any(
                    zr.zone_id == z.id for j in card.jobs for s in j.steps for zr in (s.zone_rings or [])
                ):
                    failures.append(f"red zone {z.id}: ring {z.ring_cm} cm is below {rule.id}'s {rule.ring_cm} cm")
    for job in card.jobs:
        if c is not None and card.appliance_kind == "boiler" and boiler_action(job, c) is None:
            failures.append(f"job {job.id}: not on {c.code}'s sealed-boiler allow-list")
        for step in job.steps:
            for zid in (z.root for z in step.zones):
                if zid not in zones:
                    failures.append(f"step {step.id}: unknown red zone {zid}")
            for zr in step.zone_rings or []:
                if zr.ring_cm < RING_FLOOR_CM:
                    failures.append(f"step {step.id}: zone ring {zr.ring_cm} cm is below the {RING_FLOOR_CM} cm floor")
                if zr.zone_id not in {z.root for z in step.zones}:
                    failures.append(f"step {step.id}: zone ring for {zr.zone_id}, which the step does not list")
            if c is not None:
                hit = text_stop(step, c, card.appliance_kind)
                if hit:
                    failures.append(f"step {step.id}: {hit.evidence}")
    return failures, sorted(set(rule_ids))

"""For renters: the jobs the country's rules file lists as the landlord's (the technician note marks them)."""

from __future__ import annotations

from contracts.models.card import Card
from rules.country import Country, load_country
from rules.steps import boiler_action, text_stop


def landlord_jobs(card: Card, country: str | Country) -> dict[str, str | None]:
    """{job_id: rule_id, or None when only the card's default says so} for each job that is the landlord's.

    The country file overrides the card's default both ways: a job on a sealed boiler's allow-list is the tenant's
    (NL: Besluit kleine herstellingen), any other boiler job is the landlord's, and a step whose text falls under a
    landlord rule makes its job the landlord's. Red zones beside a step are do-not-touch, not work the job does, so
    they do not count."""
    c = country if isinstance(country, Country) else load_country(country)
    out: dict[str, str | None] = {}
    for job in card.jobs:
        if c is None:
            if job.landlord:
                out[job.id] = None
            continue
        hits = [text_stop(step, c, card.appliance_kind) for step in job.steps]
        rules = [c.rule(h.rule_id) for h in hits if h is not None]
        landlord_rule = next((r.id for r in rules if r is not None and r.landlord), None)
        if landlord_rule is not None:
            out[job.id] = landlord_rule
        elif card.appliance_kind == "boiler":
            if boiler_action(job, c) is None:
                out[job.id] = f"{c.code}.gas.boiler"
        elif job.landlord:
            out[job.id] = None
    return out

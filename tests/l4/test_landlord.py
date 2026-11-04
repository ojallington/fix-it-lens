"""For renters, the note marks the jobs the country's rules file lists as the landlord's."""

from __future__ import annotations

from contracts.models.card import Card
from rules import landlord_jobs
from rules.country import load_country


def test_nl_landlord_flags() -> None:
    c = load_country("nl")
    assert c is not None
    landlord = {r.id for r in c.landlord_rules()}
    assert {"nl.gas.tap", "nl.gas.casing", "nl.gas.boiler", "nl.gas.flue", "nl.mains.wiring",
            "nl.mains.consumer_unit"} <= landlord
    # The tenant's own jobs (Besluit kleine herstellingen) and appliance work are not.
    assert not {"nl.mains.casing", "nl.mains.breaker", "nl.gas.smell"} & landlord


def test_boiler_allow_list_jobs_are_the_tenants_and_the_rest_the_landlords(boiler: Card) -> None:
    assert landlord_jobs(boiler, "nl") == {"clean_burner": "nl.gas.boiler"}


def test_a_washer_job_is_not_the_landlords(acme: Card) -> None:
    assert landlord_jobs(acme, "nl") == {}

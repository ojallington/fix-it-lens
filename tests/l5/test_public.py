"""The public pilot door never routes /preflight/* or /replay/*, and passes only the household's /api routes."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deploy.public import PublicOnly, is_public

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("path", [
    "/preflight", "/preflight/", "/api/v1/preflight", "/api/v1/preflight/warm-up", "/api/v1/preflight/x/y",
    "/api/v1/replay/abc", "/api/v1/replay/abc/frames/1.jpg", "/api/v1/unknown", "/api/v2/households",
    "/api/v1/households/../preflight", "/api/v1/appliances/a1/manual/pages/x.png",
    "/api/v1/appliances/a1/manual/pages/../../card",
])
def test_never_public(path: str) -> None:
    assert not is_public(path)


@pytest.mark.parametrize("path", [
    "/", "/sw.js", "/assets/a.js", "/ws", "/job/x", "/api/v1/households", "/api/v1/household",
    "/api/v1/feeds/hh1-abc.ics", "/api/v1/due", "/api/v1/sessions/s1/pages/42.png",
    "/api/v1/appliances/a1/jobs/clean_filters/last-done", "/api/v1/reminders/a1:j:2026-10-27/snooze",
    "/api/v1/appliances/a1/jobs/clean_filters/undo", "/api/v1/household/join", "/api/v1/me/alerts",
    "/api/v1/appliances/a1/manual", "/api/v1/households/try", "/api/v1/households/return",
    "/api/v1/household/return-link", "/api/v1/appliances/a1/manual/pages/12.png",
    "/api/v1/appliances/a1/meaning", "/api/v1/appliances/a1/display",
    "/api/v1/appliances/a1/reports", "/api/v1/appliances/a1/card/record",
    "/api/v1/appliances/a1/not-mine", "/api/v1/reports", "/api/v1/household/feedback",
    "/api/v1/household/feedback/fb-0123456789ab/withdraw",
])
def test_household_routes_are_public(path: str) -> None:
    assert is_public(path)


def test_every_contract_path_is_decided() -> None:
    """A route added to the contract has to be placed: public, or in NEVER. Only preflight and replay are private."""
    spec = (ROOT / "contracts/api.openapi.yaml").read_text()
    paths = [re.sub(r"\{[^}]+\}", "1", p) for p in re.findall(r"^  (/[^:]+):$", spec, re.M)]
    assert len(paths) > 15
    private = {p for p in paths if not is_public("/api/v1" + p)}
    assert private == {p for p in paths if p.startswith(("/preflight", "/replay"))}


def test_the_door_refuses_before_the_app() -> None:
    app = FastAPI()
    hits: list[str] = []

    @app.get("/{path:path}")
    def anything(path: str) -> dict[str, str]:
        hits.append(path)
        return {"ok": path}

    c = TestClient(PublicOnly(app))
    assert c.get("/api/v1/preflight").status_code == 404
    assert c.post("/api/v1/preflight/warm-up").status_code == 404
    assert c.get("/preflight").status_code == 404
    assert c.get("/api/v1/due").status_code == 200
    assert hits == ["api/v1/due"]

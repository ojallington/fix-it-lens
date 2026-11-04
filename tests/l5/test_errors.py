"""Every error the API sends is the contract's Error {error, detail}: each contract path, called the wrong way
(no token, an unknown id, a bad body), answers with that shape, on the session routes and the household routes alike."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import calls
from app.feeds.errors import error_body, install_errors
from app.feeds.router import Deps, make_router
from app.server import create_app

ROOT = Path(__file__).resolve().parents[2]
SPEC = yaml.safe_load((ROOT / "contracts/api.openapi.yaml").read_text())
ERROR_KEYS = set(SPEC["components"]["schemas"]["Error"]["properties"])
METHODS = ("get", "post", "put", "delete")


def calls_in_contract() -> list[tuple[str, str]]:
    out = []
    for path, ops in SPEC["paths"].items():
        url = "/api/v1" + re.sub(r"\{[^}]+\}", "nosuch", path).replace("nosuch.png", "1.png")
        out += [(m, url) for m in METHODS if m in ops and not path.startswith("/preflight")
                and (m, path) != ("get", "/households/door")]  # public, no ids: it answers 200
    return out


@pytest.fixture
def api(tmp_path: Path, conn: sqlite3.Connection, secret: bytes, loader: Any) -> TestClient:
    client = calls.make_client("replay", fixtures_root=tmp_path / "fx", private=None)
    app = create_app(client=client, record_root=tmp_path / "sessions")
    if not any(getattr(r, "path", "") == "/api/v1/households" for r in app.routes):
        app.include_router(make_router(Deps(conn, secret, loader)), prefix="/api/v1")
    install_errors(app)
    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize(("method", "url"), calls_in_contract())
def test_every_error_is_the_contract_error(api: TestClient, method: str, url: str) -> None:
    r = api.request(method.upper(), url, json={})
    assert r.status_code >= 400, f"{method} {url} answered {r.status_code} to a call with no token and no ids"
    body = r.json()
    assert isinstance(body.get("error"), str) and set(body) <= ERROR_KEYS, f"{method} {url}: {body}"


def test_shapes() -> None:
    app = FastAPI()
    install_errors(app)

    @app.get("/teapot")
    def teapot() -> None:
        raise HTTPException(418)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("secret path /home/x")

    @app.get("/n/{n}")
    def n(n: int) -> int:
        return n

    c = TestClient(app, raise_server_exceptions=False)
    assert c.get("/teapot").json()["error"] == "error"  # a status with no name still has the shape
    assert c.get("/boom").json() == {"error": "internal"}  # nothing of the exception reaches the phone
    assert c.get("/n/x").json()["error"] == "invalid" and c.get("/n/x").json()["detail"].startswith("path.n:")
    assert error_body(409, {"error": "cooldown", "detail": "41 s"}) == {"error": "cooldown", "detail": "41 s"}
    assert error_body(404, "no such job") == {"error": "not_found", "detail": "no such job"}

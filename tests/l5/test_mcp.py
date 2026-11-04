"""The MCP server (integrations/mcp_server): each tool is the REST call contracts/mcp_tools.json names, made with
the caller's token, so an agent's change is logged as the agent's. A read token sees the read tools only, the
manual's answer is the card's own quotes, and /mcp on the app's door needs a bearer token (deploy/mcp_door.py)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client

from app.api import household_routes
from app.cards import Loaded
from app.feeds.router import Deps, make_router
from app.store import Store
from deploy.mcp_door import WithMcp
from deploy.public import is_public
from integrations.mcp_server.server import WRITE_TOOLS, Headers, build, manual_says
from tests.l5.conftest import CARD_PATH, ROOT, T0, acme_card, add_appliance
from tests.l5.test_pilot_door import JOB, join

CONTRACT = json.loads((ROOT / "contracts/mcp_tools.json").read_text())


@pytest.fixture
def app(conn: sqlite3.Connection, secret: bytes, loader: Any, tmp_path: Path) -> FastAPI:
    app = FastAPI()
    card = acme_card()
    app.include_router(household_routes(Store(conn, secret), sessions_root=lambda: tmp_path,
                                        load_card=lambda p: Loaded(card, True, []) if p == CARD_PATH else None),
                       prefix="/api/v1")
    app.include_router(make_router(Deps(conn, secret, loader, clock=lambda: T0)), prefix="/api/v1")
    return app


@pytest.fixture
def home(app: FastAPI, conn: sqlite3.Connection) -> Iterator[tuple[str, str, TestClient]]:
    with TestClient(app, base_url="https://fixit.test") as api:
        _, auth = join(api, conn)
        aid = add_appliance(conn, conn.execute("SELECT id FROM households").fetchone()["id"])
        yield aid, auth["Authorization"][7:], api


def issued(api: TestClient, token: str, scope: str) -> str:
    r = api.post("/api/v1/household/tokens", json={"by_kind": "agent", "scope": scope, "label": "my agent"},
                 headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201, r.text
    return str(r.json()["token"])


def server(app: FastAPI, token: str, scope: str | None = "write") -> Any:
    def rest(_: Headers) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://fixit.test",
                                 headers={"Authorization": f"Bearer {token}"})
    return build(rest, lambda _: "https://fixit.test", lambda _: scope)  # type: ignore[arg-type,return-value]


async def call(srv: Any, tool: str, **args: Any) -> Any:
    async with Client(srv) as c:
        r = await c.call_tool(tool, args)
    if r.is_error:
        return {"error": getattr(r.content[0], "text", "")}
    return r.structured_content.get("result", r.structured_content) if r.structured_content else None


async def tools(srv: Any) -> list[str]:
    async with Client(srv) as c:
        return [t.name for t in (await c.list_tools()).tools]


async def test_the_tools_are_the_contracts(app: FastAPI, home: tuple[str, str, TestClient]) -> None:
    _, token, api = home
    pinned = {t["name"]: t for t in CONTRACT["tools"]}
    assert sorted(await tools(server(app, token))) == sorted(pinned)
    assert {n for n, t in pinned.items() if t["write"]} == WRITE_TOOLS
    async with Client(server(app, token)) as c:
        for t in (await c.list_tools()).tools:
            assert t.annotations is not None and t.annotations.read_only_hint is (not pinned[t.name]["write"]), t.name
    read = issued(api, token, "read")
    assert sorted(await tools(server(app, read, "read"))) == sorted(n for n in pinned if n not in WRITE_TOOLS)


async def test_an_agent_marks_it_done_and_the_change_is_the_agents(app: FastAPI,
                                                                   home: tuple[str, str, TestClient]) -> None:
    aid, token, api = home
    agent = server(app, issued(api, token, "write"))
    [d] = await call(agent, "whats_due")
    assert (d["appliance_id"], d["job_id"], d["status"]) == (aid, JOB, "none")  # not answered yet: no date
    assert d["manual"]["every"] == 3 and "page" in d["manual"]
    done = await call(agent, "mark_done", appliance_id=aid, job_id=JOB, on="2026-10-01")
    assert (done["due_on"], done["status"]) == ("2027-01-01", "upcoming")
    later = await call(agent, "snooze", appliance_id=aid, job_id=JOB, until="2027-02-01")
    assert later["due_on"] == "2027-02-01"
    sooner = await call(agent, "snooze", appliance_id=aid, job_id=JOB, until="2026-12-01")
    assert "only moves it later" in sooner["error"]
    yours = await call(agent, "set_schedule", appliance_id=aid, job_id=JOB, kind="window", min=2, max=4, unit="month")
    assert yours["schedule"] == {"kind": "window", "min": 2, "max": 4, "unit": "month"}
    assert (await call(agent, "last_done", appliance_id=aid, job_id=JOB))["last_done"] == {
        "date": "2026-10-01", "source": "user"}
    ch = api.get(f"/api/v1/appliances/{aid}/jobs/{JOB}/changes", headers={"Authorization": f"Bearer {token}"}).json()
    assert {c["by"]["kind"] for c in ch} == {"agent"} and {c["by"]["label"] for c in ch} == {"my agent"}


async def test_a_read_token_cannot_write_even_by_name(app: FastAPI, home: tuple[str, str, TestClient]) -> None:
    aid, token, api = home
    reader = server(app, issued(api, token, "read"), "read")
    assert "can only read" in (await call(reader, "mark_done", appliance_id=aid, job_id=JOB))["error"]
    assert len(await call(reader, "whats_due")) == 1


async def test_the_manual_says_its_own_words(app: FastAPI, home: tuple[str, str, TestClient]) -> None:
    aid, token, _ = home
    srv = server(app, token)
    out = await call(srv, "what_does_the_manual_say", appliance_id=aid, job_id=JOB)
    card = acme_card()
    texts = {q.text for q in card.quotes}
    assert out["quotes"] and all(q["quote"] in texts and q["page"] for q in out["quotes"])
    asked = await call(srv, "what_does_the_manual_say", appliance_id=aid, question="how long does it take?")
    assert asked["quotes"][0]["quote"] == "The whole job takes about 10 minutes."
    none = await call(srv, "what_does_the_manual_say", appliance_id=aid, question="bluetooth pairing")
    assert none["quotes"] == [] and "do not guess" in none["note"]
    assert "job_id or a question" in (await call(srv, "what_does_the_manual_say", appliance_id=aid))["error"]
    assert "whats_due" in (await call(srv, "what_does_the_manual_say", appliance_id="nope", question="x"))["error"]


def test_manual_says_ranks_the_jobs_own_quotes_first() -> None:
    card = json.loads(acme_card().model_dump_json())
    got = manual_says(card, job_id=JOB, question="arrow marks")
    assert got[0]["quote"].startswith("Screw the filter back in") and got[0]["job_id"] == JOB


async def test_start_repair_is_a_link_to_the_job_sheet(app: FastAPI, home: tuple[str, str, TestClient]) -> None:
    aid, token, _ = home
    out = await call(server(app, token), "start_repair", appliance_id=aid, job_id=JOB)
    assert out["link"] == f"https://fixit.test/#job/{aid}/{JOB}" and token not in out["link"]
    assert "whats_due" in (await call(server(app, token), "start_repair", appliance_id=aid, job_id="x"))["error"]


def test_mcp_on_the_door(app: FastAPI, home: tuple[str, str, TestClient], conn: sqlite3.Connection,
                         secret: bytes) -> None:
    aid, token, api = home
    door = WithMcp(app, app, lambda: (conn, secret))
    rpc = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
           "MCP-Protocol-Version": "2025-06-18"}
    with TestClient(door, base_url="https://fixit.test") as c:
        assert c.post("/mcp", json={}).status_code == 401
        listed = c.post("/mcp", headers={**rpc, "Authorization": f"Bearer {token}"},
                        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert listed.status_code == 200, listed.text
        assert {t["name"] for t in listed.json()["result"]["tools"]} >= WRITE_TOOLS
        reader = {**rpc, "Authorization": f"Bearer {issued(api, token, 'read')}"}
        listed = c.post("/mcp", headers=reader, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {t["name"] for t in listed.json()["result"]["tools"]}
        assert names and not names & WRITE_TOOLS
        link = c.post("/mcp", headers={**rpc, "Authorization": f"Bearer {token}"},
                      json={"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": "start_repair", "arguments": {"appliance_id": aid, "job_id": JOB}}})
        assert f"https://fixit.test/#job/{aid}/{JOB}" in link.text
        assert c.get("/api/v1/due", headers={"Authorization": f"Bearer {token}"}).status_code == 200  # the app
    assert is_public("/mcp") and not is_public("/api/v1/mcp")


def test_the_readme_says_what_the_server_does() -> None:
    import re

    from integrations.mcp_server import __main__ as cli

    text = (ROOT / "README.md").read_text()
    sec = text[text.index("## Use it from an agent (MCP)"):text.index("## Develop")]
    cfg = json.loads(re.search(r"```json\n(.*?)```", sec, re.S).group(1))  # type: ignore[union-attr]
    fixit = cfg["mcpServers"]["fixit"]
    assert fixit["type"] == "http" and fixit["url"].endswith("/mcp")
    assert fixit["headers"]["Authorization"] == "Bearer ${FIXIT_AGENT_TOKEN}"  # from the environment, not argv
    reads = [t for t in CONTRACT["tools"] if not t["write"]]
    assert f"the {['no', 'one', 'two', 'three', 'four', 'five'][len(reads)]} read tools" in sec
    flags = set(re.findall(r"(--[a-z-]+)", sec))
    known = {a for act in cli.parser()._actions for a in act.option_strings} | {"--kind", "--label", "--read-only"}
    assert flags <= known, flags - known
    assert "fxt_" not in sec and "Bearer fxt" not in text


def test_stdio_against_a_running_server(app: FastAPI, home: tuple[str, str, TestClient], tmp_path: Path) -> None:
    import asyncio
    import socket
    import sys
    import threading
    import time

    import uvicorn
    from mcp import StdioServerParameters

    aid, token, api = home
    tok = tmp_path / "agent-token"
    tok.write_text(issued(api, token, "read") + "\n")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    while not srv.started:
        time.sleep(0.02)
    try:
        args = ["-m", "integrations.mcp_server", "--origin", f"http://127.0.0.1:{port}", "--token-file", str(tok)]
        tok.chmod(0o644)
        with pytest.raises(Exception):  # noqa: B017 (a subprocess exit surfaces as the client's connection error)
            asyncio.run(_stdio_call(StdioServerParameters(command=sys.executable, args=args, cwd=str(ROOT)), aid))
        tok.chmod(0o600)
        out = asyncio.run(_stdio_call(StdioServerParameters(command=sys.executable, args=args, cwd=str(ROOT)), aid))
        assert out == f"http://127.0.0.1:{port}/#job/{aid}/{JOB}"
    finally:
        srv.should_exit = True
        t.join(5)


async def _stdio_call(params: Any, aid: str) -> str:
    async with Client(params) as c:
        r = await c.call_tool("start_repair", {"appliance_id": aid, "job_id": JOB})
        assert not r.is_error, r.content
        return str((r.structured_content or {})["link"])


async def test_an_agent_takes_back_its_own_mistake_and_only_that(app: FastAPI,
                                                                 home: tuple[str, str, TestClient]) -> None:
    aid, token, api = home
    sent: list[dict[str, Any]] = []

    def rest(_: Headers) -> httpx.AsyncClient:
        async def record(request: httpx.Request) -> None:
            if request.url.path.endswith("/undo"):
                sent.append(json.loads(request.content))
        return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://fixit.test",
                                 headers={"Authorization": f"Bearer {key}"}, event_hooks={"request": [record]})
    key = issued(api, token, "write")
    agent = build(rest, lambda _: "https://fixit.test", lambda _: "write")  # type: ignore[arg-type]
    await call(agent, "mark_done", appliance_id=aid, job_id=JOB, on="2026-10-01")
    back = await call(agent, "undo_my_change", appliance_id=aid, job_id=JOB)
    assert back["due_on"] is None and back["reason"] == "unanswered" and "Undone" in back["note"]
    [entry] = [t for t in CONTRACT["tools"] if t["name"] == "undo_my_change"]
    assert sent == [entry["body"]] == [{"own": True}]  # the contract's body, every time
    person = {"Authorization": f"Bearer {token}"}
    api.post(f"/api/v1/appliances/{aid}/jobs/{JOB}/done", json={"date": "2026-10-02"}, headers=person)
    refused = await call(agent, "undo_my_change", appliance_id=aid, job_id=JOB)
    assert "nothing was undone" in refused["error"] and "in the app" in refused["error"]
    ch = api.get(f"/api/v1/appliances/{aid}/jobs/{JOB}/changes", headers=person).json()
    assert ch[0]["by"]["kind"] == "app" and ch[0]["undone"] is False  # the person's change stands


async def test_a_job_with_no_date_says_why(app: FastAPI, home: tuple[str, str, TestClient]) -> None:
    aid, token, _ = home
    agent = server(app, token)
    [d] = await call(agent, "whats_due")
    assert "due_on" in d and d["due_on"] is None and d["reason"] == "unanswered"
    assert "Nobody has said when it was last done" in d["reason_text"]
    cleared = await call(agent, "snooze", appliance_id=aid, job_id=JOB, until=None)
    assert cleared["note"] == "There was no snooze to clear; nothing changed."
    assert "no due date to snooze" in (await call(agent, "snooze", appliance_id=aid, job_id=JOB,
                                                  until="2027-01-01"))["error"]
    yours = await call(agent, "set_schedule", appliance_id=aid, job_id=JOB, kind="every", every=2, unit="month")
    assert yours["due_on"] is None and "no date yet" in yours["note"] and "mark_done" in yours["note"]
    done = await call(agent, "mark_done", appliance_id=aid, job_id=JOB, on="2026-10-01")
    assert done["reason_text"].startswith("Due by the interval") and "note" not in done


async def test_the_manual_numbers_a_jobs_steps(app: FastAPI, home: tuple[str, str, TestClient]) -> None:
    aid, token, _ = home
    out = await call(server(app, token), "what_does_the_manual_say", appliance_id=aid, job_id=JOB)
    [job] = [j for j in acme_card().jobs if j.id == JOB]
    assert [s["n"] for s in out["steps"]] == list(range(1, len(job.steps) + 1))
    assert out["steps"][0] == {"n": 1, "step": job.steps[0].text.root["en"], "quote": job.steps[0].quote.text,
                               "lang": "en", "page": job.steps[0].quote.page}
    assert "start_repair" in out["note"]
    asked = await call(server(app, token), "what_does_the_manual_say", appliance_id=aid,
                       question="how long does it take?")
    assert "steps" not in asked and "start_repair" in asked["note"]

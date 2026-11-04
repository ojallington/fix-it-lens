"""A client that hangs up on /ws before its hello leaves one log line in the server's log, not a traceback
(deploy/client_gone.py); other errors still surface."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from starlette.websockets import WebSocketDisconnect
from uvicorn.protocols.utils import ClientDisconnected

import calls
from app.server import create_app
from deploy import serve
from deploy.client_gone import ClientGone


def hang_up(app: Any) -> list[dict[str, Any]]:
    """What uvicorn does when the client leaves after the accept: a 1005 disconnect, then every send fails."""
    incoming: Iterator[dict[str, Any]] = iter([{"type": "websocket.connect"},
                                               {"type": "websocket.disconnect", "code": 1005}])
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return next(incoming)

    async def send(message: dict[str, Any]) -> None:
        if sent:
            raise ClientDisconnected()
        sent.append(message)

    scope = {"type": "websocket", "path": "/ws", "raw_path": b"/ws", "query_string": b"", "headers": [],
             "scheme": "ws", "server": ("127.0.0.1", 8790), "client": ("127.0.0.1", 50000), "root_path": "",
             "subprotocols": [], "asgi": {"version": "3.0"}}
    asyncio.run(app(scope, receive, send))
    return sent


def served() -> Any:
    return create_app(client=calls.make_client("replay", private=None))


def test_a_client_gone_mid_handshake_is_one_line(caplog: pytest.LogCaptureFixture) -> None:
    with pytest.raises(WebSocketDisconnect):
        hang_up(served())  # the finding: the served app alone lets it out, and uvicorn logs the traceback
    caplog.clear()
    with caplog.at_level(logging.INFO):
        sent = hang_up(ClientGone(served()))
    assert [m["type"] for m in sent] == ["websocket.accept"]
    lines = [r for r in caplog.records if r.name == "fixit.ws"]
    assert [r.getMessage() for r in lines] == ["/ws: the client went away before it was answered"]
    assert all(r.exc_info is None for r in caplog.records)


def test_other_errors_still_surface() -> None:
    async def broken(scope: Any, receive: Any, send: Any) -> None:
        raise RuntimeError("a real fault")

    async def receive() -> Any:
        raise AssertionError

    async def send(message: Any) -> None:
        raise AssertionError

    with pytest.raises(RuntimeError):
        asyncio.run(ClientGone(broken)({"type": "websocket", "path": "/ws"}, receive, send))
    with pytest.raises(ClientDisconnected):  # an HTTP request's own failures are not this one's to hide
        async def gone(scope: Any, receive: Any, send: Any) -> None:
            raise ClientDisconnected()
        asyncio.run(ClientGone(gone)({"type": "http", "path": "/"}, receive, send))


@pytest.mark.parametrize("replay_only", [False, True])
def test_serve_wraps_both_doors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replay_only: bool) -> None:
    import uvicorn

    import app.server
    from tests.l5.test_replay_host import recording

    ran: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda a, **kw: ran.update(app=a))
    monkeypatch.setattr(app.server, "production_app", lambda dist: FastAPI())
    monkeypatch.setenv("FIXIT_CALLS", "replay")
    monkeypatch.setenv("FIXIT_PRIVATE", str(tmp_path / "unused"))
    recording(tmp_path / "replays", "rehearsal")
    argv = ["serve.py", "--dist", str(tmp_path)] + (["--replay-only", "--replays", str(tmp_path / "replays")]
                                                    if replay_only else [])
    monkeypatch.setattr(sys, "argv", argv)
    serve.main()
    assert isinstance(ran["app"], ClientGone)

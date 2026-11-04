"""Acceptance: two recorded API failures switch a real Session to local, and the badge reaches s2c. Real
Breaker, real Session, replayed fixtures; the second witness becomes the track witness (no SAM server in replay)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import calls
from app.session import Session
from calls.testing import responses_events, write_respond_fixture
from local import witness
from tests.l1.test_session import acme
from tests.l7.test_breaker import ERROR, MINE, private


async def test_two_api_failures_switch_the_session_and_show_the_badge(tmp_path: Path,
                                                                     monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FIXIT_PRIVATE", str(private(tmp_path)))  # h_mine is the operator's own home
    api = calls.make_client("replay", fixtures_root=tmp_path, private=None)
    local = calls.make_client("replay", fixtures_root=tmp_path, private=None, local=True)
    for p in ("one", "two"):
        write_respond_fixture(tmp_path, api.build_respond_body("guide", p), ERROR)
        write_respond_fixture(tmp_path, local.build_respond_body("guide", p),
                              responses_events(text=f"local {p}", model="muse-glimmer:30b"))
    s = Session(session_id="t7", client=api, fallback=local, card=acme(), job_id="clean_debris_filter",
                country="nl", household="h_mine", record_root=tmp_path / "sessions")
    s.guide = None
    out: list[dict[str, Any]] = []

    async def sink(m: dict[str, Any]) -> None:
        out.append(m)

    s.sinks.append(sink)
    assert isinstance(s.client, calls.Breaker)
    with pytest.raises(calls.CallFailed):
        await s.client.respond("guide", "one", **MINE)
    assert s.mode == "api" and not [m for m in out if m.get("kind") == "local_mode"]
    r = await s.client.respond("guide", "two", **MINE)
    assert r.text == "local two" and r.model == "muse-glimmer:30b"
    assert s.mode == "local"
    badges = [m for m in out if m["type"] == "notice" and m["kind"] == "local_mode"]
    assert len(badges) == 1 and badges[0]["text"]
    assert s.witness_provider is witness.track_witness
    recorded = "\n".join(p.read_text() for p in (tmp_path / "sessions").rglob("*.jsonl"))
    assert "local_mode" in recorded

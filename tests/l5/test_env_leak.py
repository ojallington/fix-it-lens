"""A test that builds the replay host, which sets FIXIT_PRIVATE and FIXIT_CALLS for its whole process, leaves
both as they were for the next test (the autouse fixture in conftest records them)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from deploy import replay_host
from tests.l5.test_replay_host import recording

SEEN: dict[str, str] = {}


def test_building_the_replay_host_sets_the_process_env(tmp_path: Path) -> None:
    recording(tmp_path / "replays", "demo")
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "index.html").write_text("<!doctype html>")
    replay_host.build(tmp_path / "replays", tmp_path / "dist")  # no monkeypatch of its own, on purpose
    SEEN["private"] = os.environ["FIXIT_PRIVATE"]
    assert "fixit-replay-host-" in SEEN["private"] and os.environ["FIXIT_CALLS"] == "replay"


def test_the_next_test_does_not_see_it() -> None:
    if "private" not in SEEN:
        pytest.skip("runs after the test above")
    assert "fixit-replay-host-" not in os.environ.get("FIXIT_PRIVATE", "")

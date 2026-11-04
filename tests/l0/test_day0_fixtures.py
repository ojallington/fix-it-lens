"""The day-0 recordings in fixtures/l0: well-formed, responses only, from the pinned models."""

from __future__ import annotations

from pathlib import Path

import pytest

import contracts
from calls.config import load_config
from calls.fixtures import read_fixture
from tools import guard

FILES = sorted((Path(__file__).resolve().parents[2] / "fixtures" / "l0").glob("*.jsonl"))
PINNED = {r.model for r in load_config().roles.values()}


@pytest.mark.parametrize("path", FILES, ids=[p.name[:12] for p in FILES])
def test_fixture_is_clean(path: Path) -> None:
    fx = read_fixture(path)
    contracts.validate("session", fx.header, "FixtureHeader")
    assert fx.header["request_sha256"] == path.stem
    assert fx.header["response_model"] in PINNED | {None}
    assert fx.events
    assert guard.check_fixture(str(path), path.read_text()) == []


def test_day0_covers_every_kind() -> None:
    kinds = {read_fixture(p).header["kind"] for p in FILES}
    assert {"count_tokens", "sam_image", "respond", "transcribe"} <= kinds

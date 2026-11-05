"""For every test: deleting a household never sweeps the real private folder. A test that deletes one sets
FIXIT_PRIVATE to a folder of its own first."""

from __future__ import annotations

import shutil
from collections.abc import Iterator
from typing import Any

import pytest

import calls


@pytest.fixture
def ffmpeg() -> None:
    """Without ffmpeg app/witness.py drops the video check and goes on, so a test of that check says what it lacks"""
    if shutil.which("ffmpeg") is None:
        pytest.fail("this test needs ffmpeg on PATH: the video witness builds its clips with it")


@pytest.fixture(autouse=True)
def _no_sweep_of_the_real_private_folder(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from app import erase

    real = erase.household

    def guarded(*a: Any, **kw: Any) -> dict[str, int]:
        priv = calls.private_dir()
        if priv is not None and priv.resolve() == calls.DEFAULT_PRIVATE.resolve():
            raise AssertionError("a test deleted a household with FIXIT_PRIVATE unset: set it to a tmp folder first")
        return real(*a, **kw)

    monkeypatch.setattr(erase, "household", guarded)
    yield

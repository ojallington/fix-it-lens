"""Replay: answer from recorded fixtures by request hash. No key, no network; a missing fixture fails loudly."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from calls.fixtures import find_fixture, read_fixture


class ReplayBackend:
    name = "replay"

    def __init__(self, roots: list[Path], realtime: bool = False) -> None:
        self.roots = roots
        self.realtime = realtime
        self.calls = 0

    async def stream(
        self, body: dict[str, Any], *, sha: str, kind: str
    ) -> AsyncIterator[tuple[float, dict[str, Any]]]:
        self.calls += 1
        fx = read_fixture(find_fixture(self.roots, sha))
        last = 0.0
        for t_ms, ev in fx.events:
            if self.realtime and t_ms > last:
                await asyncio.sleep((t_ms - last) / 1000)
            last = t_ms
            yield t_ms, ev

    async def transcribe(
        self, request: dict[str, Any], wav: bytes, *, sha: str
    ) -> tuple[float, int, dict[str, Any]]:
        return self._one(sha)

    async def count_tokens(self, body: dict[str, Any], *, sha: str) -> tuple[float, int, dict[str, Any]]:
        return self._one(sha)

    def _one(self, sha: str) -> tuple[float, int, dict[str, Any]]:
        """A fixture holding one non-streamed reply: {"type": "<kind>.response", "body": ...}."""
        self.calls += 1
        fx = read_fixture(find_fixture(self.roots, sha))
        t_ms, ev = fx.events[0]
        return t_ms, int(fx.header.get("http_status") or 200), dict(ev.get("body", {}))

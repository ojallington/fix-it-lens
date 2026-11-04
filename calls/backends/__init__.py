"""Backends move bytes; the policy (store false, caps, retry, model check, ledger) lives in calls.client.

Each backend streams Responses events as dicts with their arrival offset in ms, and posts transcriptions.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol


class Backend(Protocol):
    name: str

    def stream(self, body: dict[str, Any], *, sha: str, kind: str) -> AsyncIterator[tuple[float, dict[str, Any]]]:
        """POST /v1/responses with stream=true; yield (t_ms, event) per SSE event."""
        ...

    async def transcribe(
        self, request: dict[str, Any], wav: bytes, *, sha: str
    ) -> tuple[float, int, dict[str, Any]]:
        """POST /v1/asr/transcribe; return (latency_ms, http_status, json body)."""
        ...

    async def count_tokens(self, body: dict[str, Any], *, sha: str) -> tuple[float, int, dict[str, Any]]:
        """POST /v1/responses/input_tokens (free: no generation usage); return (latency_ms, http_status, json)."""
        ...

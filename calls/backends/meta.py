"""The Meta Model API backend. The key is read from its file at call time: never from env or argv, never logged."""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
from openai import AsyncOpenAI

from calls.errors import CallFailed


class MetaBackend:
    name = "meta"

    def __init__(self, base_url: str, key_path: Path) -> None:
        self.base_url = base_url.rstrip("/")
        self.key_path = key_path

    def _key(self) -> str:
        key = self.key_path.read_text(encoding="utf-8").strip()
        if not key:
            raise CallFailed(f"empty key file {self.key_path}")
        return key

    async def stream(
        self, body: dict[str, Any], *, sha: str, kind: str
    ) -> AsyncIterator[tuple[float, dict[str, Any]]]:
        t0 = time.perf_counter()
        async with AsyncOpenAI(base_url=self.base_url, api_key=self._key(), max_retries=0) as api:
            stream = await api.responses.create(**body)
            async with stream:
                async for ev in stream:
                    yield (time.perf_counter() - t0) * 1000, ev.model_dump(mode="json", exclude_none=True)

    async def transcribe(
        self, request: dict[str, Any], wav: bytes, *, sha: str
    ) -> tuple[float, int, dict[str, Any]]:
        t0 = time.perf_counter()
        async with httpx.AsyncClient(base_url=self.base_url, timeout=None) as http:
            r = await http.post(
                "/asr/transcribe",
                params={"sessionId": f"fixit-{sha[:12]}"},
                headers={"Authorization": f"Bearer {self._key()}", "Accept": "application/json"},
                files={
                    "request": (None, json.dumps(request), "application/json"),
                    "audio": ("audio.wav", wav, "audio/wav"),
                },
            )
        latency = (time.perf_counter() - t0) * 1000
        try:
            data: dict[str, Any] = r.json()
        except ValueError:
            data = {"error": r.text[:500]}
        return latency, r.status_code, data

    async def count_tokens(self, body: dict[str, Any], *, sha: str) -> tuple[float, int, dict[str, Any]]:
        t0 = time.perf_counter()
        async with httpx.AsyncClient(base_url=self.base_url, timeout=None) as http:
            r = await http.post("/responses/input_tokens", json=body,
                                headers={"Authorization": f"Bearer {self._key()}"})
        latency = (time.perf_counter() - t0) * 1000
        try:
            data: dict[str, Any] = r.json()
        except ValueError:
            data = {"error": r.text[:500]}
        return latency, r.status_code, data

"""The partner backend: Muse Spark Open on a partner's free credit (Baseten, Fireworks or Modal), over OpenAI chat
completions, translated both ways by calls.backends.chat like the local backend.

The key is read from its file at call time (~/.config/fleet/<provider>.key): never from env or argv, never logged.
It fails closed: a partner that is off in config, or whose key file is missing, empty or readable by others, raises
BackendUnavailable before any network I/O, so nothing is sent before the maintainer's key is in place. Only the
Spark roles run here; SAM and voice stay on Meta.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from calls.backends.chat import ResponsesTranslator, chat_body, sse
from calls.config import PartnerConfig
from calls.errors import BackendUnavailable, CallFailed


def key_problem(p: PartnerConfig) -> str | None:
    """Why this partner can't be called now, in plain words, or None when it can."""
    if not p.enabled:
        return f"{p.name}: off in config/models.toml"
    try:
        st = p.key_path.stat()
    except FileNotFoundError:
        return f"{p.name}: waits on key (no {p.key_path.name} in {p.key_path.parent})"
    if st.st_mode & 0o077:
        return f"{p.name}: key file {p.key_path.name} must be 0600 (it is {st.st_mode & 0o777:o})"
    if st.st_size == 0:
        return f"{p.name}: key file {p.key_path.name} is empty"
    return None


class PartnerBackend:
    name = "partner"

    def __init__(self, partner: PartnerConfig, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.partner = partner
        self.provider = partner.name  # the ledger's provider field
        self.transport = transport  # tests pass a mock transport; nothing else does

    def effort(self, body: dict[str, Any]) -> str:
        """The partner's own setting when config has one, else the role's effort from the Responses body."""
        return self.partner.reasoning_effort or str((body.get("reasoning") or {}).get("effort") or "low")

    def _key(self) -> str:
        why = key_problem(self.partner)
        if why is not None:
            raise BackendUnavailable(why)
        key = self.partner.key_path.read_text(encoding="utf-8").strip()
        if not key:
            raise BackendUnavailable(f"{self.partner.name}: key file {self.partner.key_path.name} is empty")
        return key

    async def stream(
        self, body: dict[str, Any], *, sha: str, kind: str
    ) -> AsyncIterator[tuple[float, dict[str, Any]]]:
        if kind != "respond":
            raise BackendUnavailable(f"{self.partner.name}: {kind} stays on Meta (Spark Open has no SAM)")
        key = self._key()
        t0 = time.perf_counter()
        tr = ResponsesTranslator(self.partner.name, strip_stop=False)
        req = chat_body(body, reasoning_effort=self.effort(body))
        try:
            async with httpx.AsyncClient(timeout=None, transport=self.transport) as http, http.stream(
                "POST", self.partner.base_url + "/chat/completions", json=req,
                headers={"Authorization": f"Bearer {key}"},
            ) as r:
                if r.status_code != 200:
                    detail = (await r.aread()).decode(errors="replace")[:300]
                    yield (time.perf_counter() - t0) * 1000, {"type": "error", "code": f"http_{r.status_code}",
                                                              "message": detail}
                    return
                async for chunk in sse(r):
                    for ev in tr.feed(chunk):
                        yield (time.perf_counter() - t0) * 1000, ev
        except httpx.TransportError as e:
            raise CallFailed(f"{self.partner.name} unreachable: {type(e).__name__}") from None
        yield (time.perf_counter() - t0) * 1000, tr.final()

    async def transcribe(
        self, request: dict[str, Any], wav: bytes, *, sha: str
    ) -> tuple[float, int, dict[str, Any]]:
        raise BackendUnavailable(f"{self.partner.name}: speech-to-text stays on Meta")

    async def count_tokens(self, body: dict[str, Any], *, sha: str) -> tuple[float, int, dict[str, Any]]:
        raise BackendUnavailable(f"{self.partner.name}: no token count endpoint; count on Meta")

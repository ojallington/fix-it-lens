"""The circuit breaker for a live session: after 2 consecutive API failures the session
switches to local mode and stays there, and `on_switch` fires once so the app can send Notice(kind="local_mode"),
the visible badge.

    b = calls.Breaker(api_client, local_client, on_switch=lambda why: session.notice("local_mode", why))
    r = await b.respond("judge", input, format=fmt, lane="l1")   # the same calls as a Client

What counts as a failure is the API being unusable: an error reply, a timeout, a refused budget or cap, a wrong
response model. An incomplete reply or a malformed answer is the model's, not the line's, and does not count. The
call that trips the breaker is re-run locally, so the user is not left waiting on it; the first failure is raised.
The session never switches back by itself: flapping mid-repair is worse than staying on the local stack.

Local mode runs on the machine that serves Fix-It Lens. On the maintainer's server that is the maintainer's own GPU,
so it is for the maintainer's own home only, never anyone else's. A call may switch to local only when:
- this instance is self-hosted: its owner runs it on their own computer with local mode set up
  ([local_fallback] self_hosted = true), so every home it serves is theirs; or
- its household is one of the operator's own homes ([local_fallback] homes).
A call with an origin (the open door or the judges' link) never switches, self-hosted or not, and neither does an
invited home or a call with no household: their failures are raised as they are, and the session stays on the API.
The homes are household ids, so they live in FIXIT_PRIVATE/local_fallback.toml, which config/models.toml's list
is joined with; either file may set self_hosted.
"""

from __future__ import annotations

import tomllib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from calls.client import Client, Response, SamResult, Transcript
from calls.config import Config
from calls.errors import BudgetExceeded, CallFailed, CallTimeout, ModelMismatch

TRIP_AFTER = 2
OUTAGE = (CallFailed, CallTimeout, BudgetExceeded, ModelMismatch)
DEFAULT: Any = object()


def local_fallback(config: Config, private: Path | None) -> tuple[bool, frozenset[str]]:
    """(self_hosted, the operator's homes): config/models.toml's [local_fallback], joined with
    FIXIT_PRIVATE/local_fallback.toml when there is one."""
    self_hosted, homes = config.self_hosted, set(config.local_homes)
    f = private / "local_fallback.toml" if private is not None else None
    if f is not None and f.is_file():
        data = tomllib.loads(f.read_text())
        self_hosted = self_hosted or bool(data.get("self_hosted", False))
        homes |= {str(h) for h in data.get("homes", [])}
    return self_hosted, frozenset(homes)


class Breaker:
    def __init__(self, api: Client, local: Client, *, on_switch: Callable[[str], Any] | None = None,
                 trip_after: int = TRIP_AFTER, private: Path | None = DEFAULT) -> None:
        if local.config.prompts_dir is None or api.config.prompts_dir is not None:  # set only by Config.local()
            raise ValueError("Breaker(api, local): the second client must be the local one")
        self.api = api
        self.local = local
        self.on_switch = on_switch
        self.trip_after = trip_after
        self.failures = 0
        self.tripped_by: str | None = None
        if private is DEFAULT:
            from calls import private_dir

            private = private_dir()
        self.self_hosted, self.homes = local_fallback(api.config, private)

    def may_go_local(self, household: str | None, origin: Any) -> bool:
        """Whether this call's home may use the models on this machine (see the module docstring)."""
        if origin is not None:
            return False
        return self.self_hosted or (household is not None and household in self.homes)

    @property
    def mode(self) -> str:
        return "local" if self.tripped_by is not None else "api"

    @property
    def active(self) -> Client:
        return self.local if self.tripped_by is not None else self.api

    async def _run(self, name: str, call: Callable[[Client], Awaitable[Any]], kw: dict[str, Any]) -> Any:
        if not self.may_go_local(kw.get("household"), kw.get("origin")):
            return await call(self.api)
        if self.tripped_by is not None:
            return await call(self.local)
        try:
            out = await call(self.api)
        except OUTAGE as e:
            self.failures += 1
            if self.failures < self.trip_after:
                raise
            self.tripped_by = f"{name}: {type(e).__name__}: {e}"[:200]
            if self.on_switch is not None:
                await _maybe_await(self.on_switch(self.tripped_by))
            return await call(self.local)
        self.failures = 0
        return out

    async def respond(self, role: str, input: Any, **kw: Any) -> Response:
        r: Response = await self._run("respond", lambda c: c.respond(role, input, **kw), kw)
        return r

    async def count_tokens(self, role: str, input: Any, **kw: Any) -> int:
        n: int = await self._run("count_tokens", lambda c: c.count_tokens(role, input, **kw), kw)
        return n

    async def sam_image(self, frame: bytes, prompt: Any, **kw: Any) -> SamResult:
        r: SamResult = await self._run("sam_image", lambda c: c.sam_image(frame, prompt, **kw), kw)
        return r

    async def sam_video(self, clip: bytes, prompt: Any, **kw: Any) -> SamResult:
        r: SamResult = await self._run("sam_video", lambda c: c.sam_video(clip, prompt, **kw), kw)
        return r

    async def transcribe(self, audio: bytes, *a: Any, **kw: Any) -> Transcript:
        """In local mode this raises BackendUnavailable: speech falls back to taps and typed input."""
        r: Transcript = await self._run("transcribe", lambda c: c.transcribe(audio, *a, **kw), kw)
        return r


async def _maybe_await(x: Any) -> None:
    if isinstance(x, Awaitable):
        await x


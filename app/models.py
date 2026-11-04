"""The app's one door to calls/. Every model call made in a session goes through here, so that:

- each call's outcome (ok, timeout, failed, refused by the budget) and its time is a line in the session record;
- a re-run of a recorded session (`Rerun`) answers from fixtures and reproduces the recorded timeouts and failures
  without calling anything, so it emits the same s2c sequence as the live run;
- every call carries the household and its daily cap (households.cap_usd_day), which the ledger enforces; a
  transcription carries the household but not its cap, so a spoken hazard is heard at any spend.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from typing import Any, Literal, TypeVar

import calls
from app import config
from app.frames import StoredFrame
from app.recorder import Recorder

T = TypeVar("T")

# Which of the session's concurrent streams is running: "main" (the worker and inbound messages), "guide" (the guide's
# own task) or "voice" (an utterance: transcription and the hazard check). Each stream is sequential, so calls and
# picks are numbered per stream: a re-run finds each one again however the live streams interleaved.
STREAM: ContextVar[str] = ContextVar("fixit_stream", default="main")

_ERRORS: dict[str, type[calls.CallError]] = {
    "timeout": calls.CallTimeout,
    "failed": calls.CallFailed,
    "budget": calls.BudgetExceeded,
    "incomplete": calls.IncompleteResponse,
    "model": calls.ModelMismatch,
    "missing": calls.MissingFixture,
}


def _status(e: BaseException) -> str:
    for name, cls in _ERRORS.items():
        if isinstance(e, cls):
            return name
    return "failed"


Origin = Literal["door", "judges"]


class Models:
    def __init__(self, client: calls.Client, recorder: Recorder, clock: Callable[[], float], *,
                 household: str | None, session_id: str, cap_usd: float | None = None,
                 rerun: list[dict[str, Any]] | None = None, lane: str = config.LANE,
                 origin: Origin | None = None) -> None:
        self.client = client
        self.rec = recorder
        self.clock = clock
        self.household = household
        self.cap_usd = cap_usd  # the household's daily cap (households.cap_usd_day); the ledger enforces it
        self.session_id = session_id
        self.lane = lane
        self.origin = origin  # how the household came in (the door or the judges' link); calls/ routes on it
        self.usd = 0.0  # what this session's calls cost, as calls/ priced them
        self.offline = False  # capture mode: no model calls at all
        self.on_cap: Callable[[], Awaitable[None]] | None = None  # the session's cap_reached notice
        self.on_missing: Callable[[], Awaitable[None]] | None = None  # the session's no_recording notice
        self.missed = 0  # calls replay had no fixture for: a failure of the recording, never of the connection
        self._n: dict[tuple[str, str], int] = defaultdict(int)
        self._rerun: dict[tuple[str, str, int], dict[str, Any]] | None = None
        self._picks: dict[tuple[str, str], list[Any]] | None = None
        # a record made before the streams numbers everything in one stream
        self._streamed = rerun is None or any("stream" in x for x in rerun if x.get("kind") in ("call", "pick"))
        if rerun is not None:
            self._rerun = {(x["role"], x.get("stream", "main"), int(x["n"])): x for x in rerun
                           if x.get("kind") == "call"}
            self._picks = defaultdict(list)
            for x in rerun:
                if x.get("kind") == "pick":
                    self._picks[(x["what"], x.get("stream", "main"))].append(x["value"])

    def _stream(self) -> str:
        return STREAM.get() if self._streamed else "main"

    @property
    def rerunning(self) -> bool:
        return self._rerun is not None

    def pick(self, what: str, value: Any) -> Any:
        """A choice that depends on timing or on which frames had arrived (the latest frame, the NOW pair, whether
        the video witness beat the judge). Live: logged and returned. Re-run: the recorded choice, in order."""
        stream = self._stream()
        if self._picks is not None:
            recorded = self._picks.get((what, stream))
            if recorded:
                return recorded.pop(0)
        self.rec.state(self.clock(), "pick", what=what, value=value, stream=stream)
        return value

    def skip(self, role: str) -> None:
        """A call the live run started and cancelled: the re-run does not make it, but keeps the count."""
        self._n[(role, self._stream())] += 1

    async def _run(self, role: str, fn: Callable[[], Awaitable[T]]) -> tuple[T, float]:
        if self.offline:
            raise calls.BackendUnavailable("capture mode makes no model calls")
        stream = self._stream()
        n = self._n[(role, stream)]
        self._n[(role, stream)] += 1
        recorded = self._rerun.get((role, stream, n)) if self._rerun is not None else None
        if recorded is not None and recorded["status"] == "cancelled" and role == "guide":
            raise asyncio.CancelledError  # a stop cancelled this guide turn live: the re-run's turn ends here too
        if recorded is not None and recorded["status"] not in ("ok", "cancelled"):
            if recorded["status"] == "budget" and self.on_cap is not None:
                await self.on_cap()
            raise _ERRORS.get(recorded["status"], calls.CallFailed)(f"{role}: recorded {recorded['status']}")
        t0 = time.perf_counter()
        try:
            out = await fn()
        except asyncio.CancelledError:
            self.rec.state(self.clock(), "call", role=role, stream=stream, n=n, status="cancelled",
                           ms=round((time.perf_counter() - t0) * 1000, 1))
            raise
        except calls.CallError as e:
            ms = (time.perf_counter() - t0) * 1000
            self.usd += float(getattr(e, "usd", 0.0) or 0.0)  # a timed-out call is billed its estimate (the ledger's)
            self.rec.state(self.clock(), "call", role=role, stream=stream, n=n, status=_status(e), ms=round(ms, 1),
                           error=str(e)[:200])
            if isinstance(e, calls.BudgetExceeded) and self.on_cap is not None:
                await self.on_cap()
            if isinstance(e, calls.MissingFixture):
                self.missed += 1
                if self.on_missing is not None:
                    await self.on_missing()
            raise
        ms = (time.perf_counter() - t0) * 1000 if recorded is None else float(recorded["ms"])
        self.usd += float(getattr(out, "usd", 0.0) or 0.0)
        sha = getattr(out, "request_sha256", None)
        self.rec.state(self.clock(), "call", role=role, stream=stream, n=n, status="ok", ms=round(ms, 1), sha=sha)
        return out, ms

    def _kw(self) -> dict[str, Any]:
        """What every client call carries. `origin` only when the household has one, so a client that does not
        route on it never sees it."""
        kw: dict[str, Any] = {"lane": self.lane, "household": self.household, "session_id": self.session_id,
                              "household_cap_usd": self.cap_usd}
        if self.origin is not None:
            kw["origin"] = self.origin
        return kw

    async def respond(self, role: str, input: list[dict[str, Any]], **kw: Any) -> tuple[calls.Response, float]:
        return await self._run(role, lambda: self.client.respond(
            role, input, **self._kw(), **kw))  # the role's timeout: 6 s in api mode, longer local rows

    async def sam_image(self, frame: StoredFrame, prompt: str | calls.Box,
                        role: str = "sam") -> tuple[calls.SamResult, float]:
        self.rec.frame(frame.frame_id, frame.jpeg)
        return await self._run(role, lambda: self.client.sam_image(
            frame.jpeg, prompt, frame_hash=frame.phash, **self._kw()))

    async def sam_video(self, clip: bytes, prompt: str | calls.Box, *, width: int, height: int, frames: int,
                        timeout_s: float) -> tuple[calls.SamResult, float]:
        return await self._run("sam_video", lambda: self.client.sam_video(
            clip, prompt, width=width, height=height, frames=frames, timeout_s=timeout_s, **self._kw()))

    async def transcribe(self, wav: bytes, keywords: Sequence[str]) -> tuple[calls.Transcript, float]:
        """Never refused by the household's daily cap: a spoken hazard must reach the rules at any spend (the lane
        budget still bounds it)."""
        kw = {**self._kw(), "household_cap_usd": None}
        return await self._run("transcribe", lambda: self.client.transcribe(
            wav, ("Dutch", "English"), list(keywords)[:100], **kw))

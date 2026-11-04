"""Re-run a recorded session: its c2s messages, in order, through a fresh Session whose model calls come from
calls/ in replay mode, with every timing-dependent choice (which frame, which clip, whether the video witness won)
taken from the record. The s2c it emits is the s2c the live run sent.

`?replay=<id>` (server.py) plays the recorded s2c back to the phone; this is the stronger check behind it: the
code, not the record, produces each message again, and nothing calls the live API (the client must be in replay
mode; a request the live run did not make raises MissingFixture).

Live, the session runs streams concurrently: the worker ("main"), the guide's own task ("guide") and each utterance
("voice"); the stop and step path (safety, checking, verdict, step) is the "safety" stream. Here each c2s message is
handled, with everything it started, before the next, so the streams interleave differently. Order is therefore
compared within each stream: the safety stream strictly, the guide's sentences in order among themselves, and so on.
A live session where the user tapped during the opening can still differ; `Rerun.diff` says where.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app import cards, config, stepcheck
from app.frames import FrameBuffer, StoredFrame
from app.recorder import Recording, load
from app.session import Session, stream_of


class RecordedFrames(FrameBuffer):
    """The live buffer, plus every frame the recording kept: a frame the live run picked is always found again,
    even after the ring has dropped it."""

    def __init__(self, rec: Recording, times: dict[str, float]) -> None:
        super().__init__()
        self.rec, self.times = rec, times

    def get(self, frame_id: str) -> StoredFrame | None:
        got = super().get(frame_id)
        if got is None and frame_id in self.times:
            jpeg = self.rec.frame_bytes(frame_id)
            if jpeg is not None:
                got = stepcheck.stored(frame_id, self.times[frame_id], jpeg)
        return got


@dataclass(frozen=True)
class Rerun:
    live: list[dict[str, Any]]
    again: list[dict[str, Any]]
    live_streams: list[str] | None = None  # the stream of each live message (None: all one stream)
    again_streams: list[str] | None = None

    def diff(self) -> list[tuple[str, dict[str, Any] | None, dict[str, Any] | None]]:
        """("stream#index", live, again) for each message that differs within its stream; wall-clock fields and the
        global seq (which follows the interleaving) are not compared."""
        a, b = _by_stream(self.live, self.live_streams), _by_stream(self.again, self.again_streams)
        out = []
        for st in sorted(set(a) | set(b)):
            x, y = a.get(st, []), b.get(st, [])
            for i in range(max(len(x), len(y))):
                one, two = x[i] if i < len(x) else None, y[i] if i < len(y) else None
                if one != two:
                    out.append((f"{st}#{i}", one, two))
        return out

    @property
    def same(self) -> bool:
        return not self.diff()


WALL_CLOCK = {"t_ms", "latency_ms"}  # measured, not decided: the re-run's own timings


def _by_stream(msgs: list[dict[str, Any]], streams: list[str] | None) -> dict[str, list[Any]]:
    one = streams is None or set(streams) <= {"main"}  # a record from before the streams: one global order
    out: dict[str, list[Any]] = {}
    for i, m in enumerate(msgs):
        st = "all" if one else streams[i]  # type: ignore[index]
        out.setdefault(st, []).append({k: v for k, v in _comparable(m).items() if one or k != "seq"})
    return out


def _comparable(m: Any) -> Any:
    if isinstance(m, dict):
        return {k: _comparable(v) for k, v in m.items() if k not in WALL_CLOCK}
    if isinstance(m, list):
        return [_comparable(v) for v in m]
    return m


def previewed(lines: list[dict[str, Any]]) -> bool:
    """Did the recorded session start its job with job_preview (so its step 0 waited for job_ready)?"""
    return any(x["dir"] == "s2c" and x.get("msg", {}).get("type") == "job_preview" for x in lines)


def opened(lines: list[dict[str, Any]]) -> bool:
    """Did the recorded job open with its opening (the disclosure at its end, not first)?"""
    return any(x["dir"] == "state" and x.get("kind") == "opening" for x in lines)


async def rerun(rec: Recording, client: Any, *, card: Any = None, guide: bool = True,
                manuals: Any = None) -> Rerun:
    """`card` overrides the recorded card path (relative to the repo, as the server stored it). `manuals` are the
    served session's (app.ports.IngestManuals): the guide's requests carry the manual's pages, so a re-run of a live
    session needs them to send the same requests."""
    meta = rec.meta
    if card is None and meta.card:
        loaded = cards.load(config.ROOT / meta.card, meta.country or "nl", manuals)
        card = loaded.card if loaded.usable else None
    c2s = [x["msg"] for x in rec.lines if x["dir"] == "c2s"]
    times = {m["frame_id"]: float(m["t_ms"]) for m in c2s if m.get("type") == "frame"}
    s = Session(session_id=meta.session_id, client=client, lang=meta.lang, country=meta.country or "nl",
                mode=meta.mode, card=card, card_path=meta.card, job_id=meta.job_id,
                household=meta.household, appliance_id=meta.appliance_id, record_root=None, rerun=rec.lines,
                manuals=manuals, preview=previewed(rec.lines), opening=opened(rec.lines))
    s.frames = RecordedFrames(rec, times)
    if guide and s.mode != "capture" and card is not None:
        from app.guide import Guide

        s.guide = Guide(s)
    out: list[dict[str, Any]] = []
    streams: list[str] = []

    async def sink(m: dict[str, Any]) -> None:
        out.append(m)
        streams.append(stream_of(m))  # emit calls its sinks on the emitting task

    s.sinks.append(sink)
    s.start()
    hellos = 0
    try:
        for m in c2s:
            if m.get("type") == "hello":
                resumed = hellos > 0
                hellos += 1
                await s.emit({"type": "hello_ok", "session_id": s.session_id, "resumed": resumed,
                              "mode": "local" if s.mode == "local" else "api"})
                if resumed:
                    await s.notice("reconnected")
                else:
                    await s.greet()
                    await s.start_job()
                continue
            jpeg = rec.frame_bytes(m["frame_id"]) if m.get("type") == "frame" else \
                b"" if m.get("type") == "plate_photo" else None  # no photo is kept: the recorded read plays back
            audio = rec.audio_bytes(m["utterance_id"]) if m.get("type") == "utterance" else None
            await s.handle(m, audio=audio, jpeg=jpeg)
            await s.idle()  # a frame can queue a gate retry too
    finally:
        await s.close()
    live_streams = rec.s2c_streams()
    return Rerun(rec.s2c(), out, live_streams, streams if set(live_streams) - {"main"} else None)


async def rerun_id(root: Path, session_id: str, client: Any, **kw: Any) -> Rerun:
    return await rerun(load(root, session_id), client, **kw)

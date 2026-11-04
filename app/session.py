"""One repair session, on the server. The state machine lives here, not on the phone, so a reconnect is invisible.

Every s2c message goes out through `emit`: it is numbered, validated against ws.schema.json, recorded and kept in a
10-minute ring for resume. Text reaches the phone only through `say` and `gate`, which run G0 first: a passed
sentence goes out as `sentence`, a held one only as `held{rule_id, line, words}`; the held words never leave.

Safety is `rules.SafetyEngine` (the rules package), used directly: G0 on every sentence, the step gate, G1,
report_hazard, what the user says. The session only feeds it evidence code has seen; the model can raise a stop and
never clear one.

Job phases: idle → [preview] → [gas_confirm] → [held] → instructed ⇄ checking → … → job_done; any phase → stopped.
A live job starts with job_preview (what to have ready, every step); step 0 waits for the person's job_ready, recorded
as their word. A step whose camera is find or none has no camera check: Done there is the person's word. A step whose
gate holds is not shown: the session asks for the precondition (outline, side view, plug out…) and tries again when
new evidence arrives. The step pointer moves only on a code-computed `done` verdict, except past our own step
(origin fixit: plug it back in), which the camera has nothing to check: the user ends it, in a by_person verdict; and
past any manual step the person says they did ("I did it, continue", tap did_it), offered on every step while no
hold or stop stands: a by_person verdict too, confirmed by them and never called checked. Before the start (the
job's last manual step, a power-on step, our own step, the job's end) the steps they confirmed whose part should
still be in place are checked again; one the camera sees undone reopens, and only a done check ends it then.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

import calls
import rules
from app import config, judge, locate, power, stepcheck, trace, witness
from app.frames import FrameBuffer, StoredFrame, decode, hamming
from app.geometry import Region
from app.models import STREAM, Models, Origin
from app.ports import HomeAssistant, Manuals, manual_household
from app.recorder import BYTE_FIELDS, Recorder
from app.spoken import in_lang, split
from app.verdict import JudgeFinding, PowerWitness, SamWitness, combine
from contracts import VERSION, unions
from contracts.models import ws
from contracts.models.card import Card, Job, Part, Step
from contracts.models.judge import Verdict, Witnesses
from contracts.models.safety import Hold, Ok, SafetyEvent, Stop
from contracts.models.session import SessionMeta

Phase = Literal["idle", "preview", "gas_confirm", "held", "instructed", "checking", "job_done", "stopped"]
Sink = Callable[[dict[str, Any]], Awaitable[None]]
# what the person says when they mean only "done": filler words around one of DONE or DID_IT, nothing more. Anything
# else ("done, but every light is red", "is it done?") is for the guide to answer and ends nothing.
_FILLER = frozenset({"yes", "yeah", "yep", "ok", "okay", "oke", "oké", "so", "right", "that", "it", "it's", "thats",
                     "that's", "is", "i", "i'm", "im", "am", "all", "now", "too", "there", "we", "go", "ja", "jep",
                     "zo", "dat", "het", "ik", "ben", "nu", "alles", "hij", "ze"})
DONE = frozenset({"done", "finished", "ready", "klaar", "gedaan", "af"})
DID_IT = frozenset({"did it", "did that", "i did it", "i did that", "heb het gedaan", "ik heb het gedaan",
                    "heb ik gedaan", "gedaan heb ik"})


def said_done(text: str) -> str | None:
    """'did_it' when the words are only "I did it" (ik heb het gedaan), 'done' when only "done" (klaar), with filler
    words around them; None for anything more, or a question."""
    if "?" in text:
        return None
    words = re.findall(r"[\w'’]+", text.lower().replace("’", "'"))
    if not words or len(words) > 6:
        return None
    phrase = " ".join(words)
    for p in sorted(DID_IT, key=len, reverse=True):
        if f" {p} " in f" {phrase} ":
            rest = f" {phrase} ".replace(f" {p} ", " ", 1).split()
            return "did_it" if all(w in _FILLER for w in rest) else None
    core = [w for w in words if w not in _FILLER]
    return "done" if len(core) == 1 and core[0] in DONE else None
# What the session asks for while a step's gate holds. Code-written, and still through G0.
ASK_FOR: dict[str, dict[str, str]] = {
    "en": {
        "zone_outlined": "Point the camera at the part so I can outline the area not to touch.",
        "side_view": "Show me the side view first, so I can see how close your hands are.",
        "view_scale": "Hold the camera so both outlines are side by side.",
        "user_confirm": "This step is close to the red outline. Keep your hands clear of it; tap yes when ready.",
        "power_off_seen": "Pull the plug out of the socket, show me the plug and the socket, then tap yes.",
        "fresh_frame": "Hold the camera still for a moment.",
        "hand_visible": "Keep your hand in view.",
    },
    "nl": {
        "zone_outlined": "Richt de camera op het onderdeel, dan omlijn ik waar je niet aan mag komen.",
        "side_view": "Laat me eerst de zijkant zien, zodat ik zie hoe dicht je handen erbij zijn.",
        "view_scale": "Houd de camera zo dat beide omlijningen naast elkaar staan.",
        "user_confirm": "Deze stap is dicht bij de rode omlijning. Houd je handen ervan weg; tik op Ja als je zover "
                        "bent.",
        "power_off_seen": "Trek de stekker uit het stopcontact, laat me de stekker en het stopcontact zien en tik "
                          "op Ja.",
        "fresh_frame": "Houd de camera even stil.",
        "hand_visible": "Houd je hand in beeld.",
    },
}
# The taps about the step on screen: one whose step the job has left is refused (Tap.step_id, else the step current
# when it came in).
STEP_TAPS = ("did_it", "done", "repeat", "next", "back", "closer")
# A spoken "done" or "I did it" that came in for a step the job has since left; a confirmed step the camera sees undone
# before the start; a "yes" whose look did not see the plug out.
STALE = {"en": "That came in for the step before, so I didn't count it. Have a look at the step on the screen now.",
         "nl": "Dat kwam binnen voor de vorige stap, dus dat tel ik niet mee. Kijk nu naar de stap op het scherm."}
REOPENED = {"en": "This step isn't done after all: I can see it from here. Please finish this step before the machine "
                  "starts.",
            "nl": "Deze stap is toch nog niet klaar: dat zie ik hier. Doe deze stap eerst, voordat de machine start."}
PLUG_UNSEEN = {"en": "I can't see the plug out of the socket yet. Show me the plug and the socket, then tap yes.",
               "nl": "Ik zie de stekker nog niet uit het stopcontact. Laat me de stekker en het stopcontact zien en "
                     "tik op Ja."}
HELD_STEP = {"en": "This step is held by the safety rules.", "nl": "De veiligheidsregels houden deze stap tegen."}
# the preview's warnings: a step's power precondition, and a red zone whose manual quote G0 holds
POWER_LINE = {"en": {"off": "Unplug the appliance first; the camera checks the plug is out.",
                     "on": "This step needs the machine plugged in."},
              "nl": {"off": "Eerst de stekker eruit; de camera kijkt of hij eruit is.",
                     "on": "Voor deze stap moet de machine aan de stroom."}}
# The job's opening, said and shown right after job_preview, built from it: the job, what to get, read the steps, the
# phone's ready button by its own words. The AI disclosure (rules' spoken line) comes last in it, not first.
OPENING = {
    "en": {"steps": "{n} steps, about {m} minutes.", "steps_no_time": "{n} steps.",
           "steps_1": "1 step, about {m} minutes.", "steps_1_no_time": "1 step.",
           "needs": "First get these:",
           "read": "Have a quick read through the steps below, and tap \u201cI'm ready\u201d when you have everything.",
           "read_no_needs": "Have a quick read through the steps below, and tap \u201cI'm ready\u201d to start."},
    "nl": {"steps": "{n} stappen, ongeveer {m} minuten.", "steps_no_time": "{n} stappen.",
           "steps_1": "1 stap, ongeveer {m} minuten.", "steps_1_no_time": "1 stap.",
           "needs": "Leg eerst klaar:",
           "read": "Lees de stappen hieronder even door en tik op \u201cIk ben er klaar voor\u201d als je alles hebt.",
           "read_no_needs": "Lees de stappen hieronder even door en tik op \u201cIk ben er klaar voor\u201d om te"
                            " beginnen."}}
ZONE_LINE = {"en": "Keep your hands out of the red zone marked on this step.",
             "nl": "Blijf met je handen uit de rode zone die bij deze stap staat."}
NOTICE = {
    "en": {"no_recording": "Replay has no recording for this call.",
           "local_mode": "The connection to Meta's models failed twice, so I'm using the model on your own "
                         "network.",
           "slow_check": "The check took too long, so it didn't count. Try again.",
           "slow_outline": "Finding the part took too long. Hold the camera still a moment.",
           "slow_answer": "That answer took too long. Ask again."},
    "nl": {"no_recording": "Van dit deel is geen opname.",
           "local_mode": "De verbinding met de modellen van Meta viel twee keer weg, dus ik gebruik het model op je "
                         "eigen netwerk.",
           "slow_check": "De controle duurde te lang en telt niet. Probeer het nog eens.",
           "slow_outline": "Het onderdeel zoeken duurde te lang. Houd de camera even stil.",
           "slow_answer": "Dat antwoord duurde te lang. Vraag het nog eens."},
}

FRAME_RETRY_MS = 2000  # a held zone_outlined step re-tries its outline at most this often

# Asked before a step near a gas tap is shown. It never names the part: G0 holds any sentence naming a gas part, and the
# outline's label already says what it is.
GAS_CONFIRM: dict[str, str] = {
    "en": "Look at the red outline. Is it on the right tap? Tap yes if it is, and don't touch it.",
    "nl": "Kijk naar de rode omlijning. Staat die op de goede kraan? Tik op Ja als dat zo is, en raak hem niet aan.",
}
GAS_DETAIL = "confirm the red outline before this step"
GAS_AGAIN: dict[str, str] = {"en": "Then I'll outline it again.", "nl": "Dan omlijn ik hem opnieuw."}


class Hooks(Protocol):
    """Writes outside the session: the store's history and pilot events (the household store does the writing)."""

    def job_started(self, s: Session) -> None: ...
    def job_done_checked(self, s: Session) -> None: ...
    def job_done_by_person(self, s: Session) -> None: ...
    def history(self, s: Session, kind: str, text: str | None) -> None: ...


class NoHooks:
    def job_started(self, s: Session) -> None:
        pass

    def job_done_checked(self, s: Session) -> None:
        pass

    def job_done_by_person(self, s: Session) -> None:
        pass

    def history(self, s: Session, kind: str, text: str | None) -> None:
        pass


def split_sentences(text: str) -> list[str]:
    return split(text)


@dataclass
class StepRun:
    index: int
    step: Step
    attempts: int = 0
    before: StoredFrame | None = None
    last_verdict: Verdict | None = None
    msg: dict[str, Any] | None = None
    asked: str | None = None  # the precondition last asked for while the gate held
    did_it_offered: bool = False  # cant_see MAX_CHECK_ATTEMPTS times: the guide points to "I did it, continue"
    reopened: bool = False  # confirmed by the person, then seen not done before the start: only a done check ends it


@dataclass
class Session:
    session_id: str
    client: calls.Client
    lang: str = "en"
    country: str = "nl"
    mode: Literal["api", "local", "capture"] = "api"
    card: Card | None = None
    card_path: str | None = None
    job_id: str | None = None
    appliance_id: str | None = None
    alias: str | None = None
    household: str | None = None
    cap_usd: float | None = None
    lane: str = config.LANE  # the ledger lane the session's calls bill to (Household.lane in production)
    origin: Origin | None = None  # how the household came in (Household.origin): calls/ routes and budgets on it
    manuals: Manuals | None = None
    ha: HomeAssistant | None = None
    hooks: Hooks = field(default_factory=NoHooks)
    record_root: Path | None = None

    @property
    def manual_household(self) -> str | None:
        """Whose copy of the manual the card cites: its home's for a home's own card, else the shared one."""
        return manual_household(self.card_path)
    rerun: list[dict[str, Any]] | None = None
    fallback: calls.Client | None = None  # the local client the breaker switches to after 2 API outages
    preview: bool = False  # the job starts with job_preview and step 0 waits for the person's job_ready
    opening: bool = False  # with the preview: the job's opening comes first, the AI disclosure at its end

    def __post_init__(self) -> None:
        from app.ports import NoHomeAssistant

        self._t0 = time.monotonic()
        self._seq = 0
        self.ring: deque[tuple[float, dict[str, Any]]] = deque()
        self.sinks: list[Sink] = []
        self.frames = FrameBuffer()
        self.safety = rules.SafetyEngine(self.country, self.lang, self.card, self.job_id)
        self.phase: Phase = "idle"
        self.run: StepRun | None = None
        self.regions: dict[str, Region] = {}
        self.cue_seen: dict[str, bool] = {}
        self.zone_rules: dict[str, str] = {}
        self.gas_confirmed = False
        self.power_off_seen = False
        self.job_checked = False  # the job's manual steps all passed and job_done_checked has run (once per job)
        self.by_person: set[str] = set()  # this job's manual steps the person confirmed (did_it), not the camera
        self.stood: set[str] = set()  # confirmed steps seen again before the start and not seen undone
        self.resume_at: int | None = None  # the start a reopened step interrupted: the job goes on there after it
        self.ha_power_w: float | None = None  # the last HA reading; None when HA has no power entity
        self.ring_warned = False
        self.view: ws.View | None = None
        self.ha = self.ha or NoHomeAssistant()
        self.events: list[str] = []  # code-written notes for the guide's next turn
        self._n_sentence = 0
        self._disclosed = False  # the AI disclosure has been said (once, in every session that talks)
        self._reopening = False  # a step reopened before the start: its lines carry no step_id until it is shown
        self._n_check = 0
        self._g1_pause_at: float | None = None
        self._n_safety = 0
        self._script_said: Stop | None = None  # the stop whose script was said
        self._cap_said = False
        self._missing_said = False
        self.plates: dict[str, dict[str, Any]] = {}  # photo_id -> the plate read (session only, never stored)
        self.plate_reader: Any = None  # ingest's read_plate unless a test sets it
        self._retry_queued = False
        self._retry_at: float = -FRAME_RETRY_MS
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._worker: asyncio.Task[None] | None = None
        # The guide runs on its own task, one turn at a time, so nothing in the worker or on the voice path waits on
        # it; a stop cancels the turn in flight and drops the queued ones.
        self._guide_q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._guide_worker: asyncio.Task[None] | None = None
        self._guide_now: asyncio.Task[None] | None = None
        # Turns nobody asked for (a verdict's) are bounded: none while the guide waits on the person after asking for a
        # new view (the phash of the view it asked about), and none once the step has used GUIDE_STEP_ROUNDS rounds.
        self._auto_turn = False  # the turn running now has no words of the person's
        self._waiting_on: str | None = None
        self._step_rounds_from = 0
        # Each utterance (transcription, then the hazard check) runs at once on its own task, one at a time.
        self._voice: set[asyncio.Task[None]] = set()
        self._voice_lock = asyncio.Lock()
        # A step's parts are outlined on their own task once the step is shown, so a tap behind them ("I did it")
        # never waits on locate and SAM; and the worker's item runs as a task an "I did it" can cancel mid-check.
        self._outlining: set[asyncio.Task[None]] = set()
        self._work_now: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self.meta = SessionMeta(
            session_id=self.session_id, household=self.household, appliance_id=self.appliance_id,
            card=self.card_path, job_id=self.job_id, started_at=datetime.now(UTC), mode=self.mode, lang=self.lang,
            country=self.country, contracts_version=VERSION)
        self.rec = Recorder(self.record_root, self.meta)
        breaker = getattr(calls, "Breaker", None)  # the local backend's, when calls/ has it
        if self.fallback is not None and self.mode == "api" and breaker is not None:
            self.client = breaker(self.client, self.fallback, on_switch=self._went_local)
        self.models = Models(self.client, self.rec, self.clock, household=self.household,
                             session_id=self.session_id, cap_usd=self.cap_usd, rerun=self.rerun, lane=self.lane,
                             origin=self.origin)
        self.models.offline = self.mode == "capture"
        self.models.on_cap = self._cap_reached
        self.models.on_missing = self._no_recording
        self.guide: Any = None  # app.guide.Guide, set by the server (never in capture mode)
        # the second witness; local mode plugs in its own (it must still return a SamWitness, and done needs it)
        self.witness_provider: Callable[..., Awaitable[SamWitness]] = witness.sam_witness

    async def _no_recording(self) -> None:
        """Replay had no recorded response for a call: say that (once), never slow_connection."""
        if not self._missing_said:
            self._missing_said = True
            await self.notice("no_recording", in_lang(NOTICE, self.lang)["no_recording"])

    async def _cap_reached(self) -> None:
        """The ledger refused a call: the household's daily cap (or the lane's) is spent. Said once."""
        if not self._cap_said:
            self._cap_said = True
            self.rec.state(self.clock(), "cap_reached")
            await self.notice("cap_reached")

    async def _went_local(self, why: str) -> None:
        """The breaker tripped: from here every call is local, and the phone shows the badge. It never switches
        back. Without SAM the second witness is the local backend's (Glimmer locate plus optical flow), labelled as
        such."""
        self.mode = "local"
        self.rec.state(self.clock(), "local_mode", why=why)
        try:
            from local.witness import provider  # type: ignore[import-untyped,unused-ignore]

            self.witness_provider = await provider(self.fallback)
        except Exception as e:  # noqa: BLE001 - the SAM witness stays; with no SAM it fails to cant_see, never done
            self.rec.state(self.clock(), "local_witness_failed", error=f"{type(e).__name__}: {e}"[:200])
        await self.notice("local_mode", in_lang(NOTICE, self.lang)["local_mode"])

    # ---- time, emit, text ----------------------------------------------------------------------------------

    def clock(self) -> float:
        return round((time.monotonic() - self._t0) * 1000, 1)

    @property
    def last_seq(self) -> int:
        return self._seq - 1

    async def emit(self, msg: dict[str, Any]) -> dict[str, Any]:
        msg = {**msg, "seq": self._seq, "t_ms": self.clock()}
        unions.s2c.validate_python(msg)  # nothing leaves that the contract does not allow
        self._seq += 1  # the session's logical order; the record adds the stream, and a re-run compares per stream
        self.rec.client(msg["t_ms"], "s2c", msg, stream=stream_of(msg))
        now = time.monotonic()
        self.ring.append((now, msg))
        while self.ring and now - self.ring[0][0] > config.S2C_RING_S:
            self.ring.popleft()
        for sink in list(self.sinks):
            try:
                await sink(msg)
            except Exception:  # a dead socket is dropped; the session goes on and the phone resumes
                self.sinks.remove(sink)
        return msg

    def since(self, last_seq: int | None) -> list[dict[str, Any]]:
        return [m for _, m in self.ring if last_seq is None or m["seq"] > last_seq]

    def _hold_logged(self, h: rules.Hold, text: str) -> None:
        self.rec.state(self.clock(), "g0_hold", rule_id=h.rule_id, sha256=hashlib.sha256(text.encode()).hexdigest())

    async def say(self, text: str, *, whole: bool = False, step_id: str | None = None) -> list[str]:
        """Each sentence through G0, then drawn and spoken; a held one goes out as `held` only. Returns what passed.
        `whole` keeps a card line as one unit: a look-only exemption is by the exact line's hash. `step_id`: the step
        the words are about, on each sentence and held line, so the phone can let them go with their step."""
        tag = {"step_id": step_id} if step_id else {}
        passed = []
        warned: set[str] = set()
        await self._safety_sync()
        for s in [text.strip()] if whole else split_sentences(text):
            self._n_sentence += 1
            sid = f"s{self._n_sentence}"
            r = self.safety.on_sentence(s)
            if isinstance(r, rules.Hold):
                self._hold_logged(r, s)
                await self.emit({"type": "held", "sentence_id": sid, "rule_id": r.rule_id, "line": r.line,
                                 "words": r.words, **tag})
                # the user hears the rule's own warning, once per rule per reply; in STOP every model sentence is
                # held with the stop script as its line, and the script was said once at the stop
                if r.rule_id not in warned and not self.stopped:
                    warned.add(r.rule_id)
                    await self.say(rules.warning_for(r, self.lang, self.card, country=self.country), whole=True,
                                   step_id=step_id)
            else:
                passed.append(s)
                await self.emit({"type": "sentence", "id": sid, "text": s, **tag})
        return passed

    async def gate(self, text: str | None, *, step_id: str | None = None) -> str | None:
        """G0 for text inside other messages (a step's words, a label, a quote): the text, or None if held. A held
        line carries `step_id`, the step the text is about, when there is one."""
        if not text:
            return None
        await self._safety_sync()
        r = self.safety.on_sentence(text)
        if isinstance(r, rules.Hold):
            self._hold_logged(r, text)
            await self.emit({"type": "held", "sentence_id": None, "rule_id": r.rule_id, "line": r.line,
                             "words": r.words, **({"step_id": step_id} if step_id else {})})
            return None
        return text

    async def slow(self, what: str) -> None:
        """A call that took too long: the slow notice, with words that say what it held up (the check, finding the
        part, an answer) rather than blaming the person's connection."""
        await self.notice("slow_connection", in_lang(NOTICE, self.lang)[what])

    async def notice(self, kind: str, text: str | None = None) -> None:
        await self.emit({"type": "notice", "kind": kind, "text": await self.gate(text) if text else None})

    async def rail(self, tool: str, args_short: str, result_short: str, ms: float, **detail: Any) -> None:
        """One row of the phone's "how it thinks" line, in plain words (app.trace); `detail` is the machine side
        (ids, sources, witnesses), kept in the record as a `trace` state line and never sent."""
        if detail:
            self.rec.state(self.clock(), "trace", tool=tool, **detail)
        await self.emit({"type": "rail", "tool": tool, "args_short": args_short[:80],
                         "result_short": result_short[:80], "ms": round(max(0.0, ms), 1)})

    # ---- safety ----------------------------------------------------------------------------------------------

    @property
    def stopped(self) -> bool:
        return isinstance(self.safety.state, Stop)

    async def _safety_sync(self) -> None:
        """Emit every state change the engine made since the last call, and record the engine's event log. Entering
        STOP says the stop's script, once per stop, right after the safety message: this is the one place it is
        said, so a stop from any path (a hand in a zone, the step gate, the user's words, report_hazard) is heard."""
        new = self.safety.events[self._n_safety:]
        self._n_safety = len(self.safety.events)
        for e in new:
            self.rec.state(e.t_ms, "safety", source=e.source, move=e.kind, to=e.to.state, reason=e.reason)
        if not new:
            return
        st = self.safety.state
        if isinstance(st, Stop) and self.phase != "stopped":
            self.phase = "stopped"
            self._cancel_guide()
            self.rec.state(self.clock(), "phase", to="stopped", rule_id=st.rule_id)
            self.hooks.history(self, "stop", f"{st.rule_id}: {st.evidence[:200]}")
        await self.emit({"type": "safety", "state": st.model_dump(exclude_none=True)})
        if not isinstance(st, Stop):
            self._script_said = None  # a later stop is a new stop, and is heard
        elif (said := self._script_said) is None or (said.rule_id, said.script) != (st.rule_id, st.script):
            self._script_said = st  # a stop raised over a lesser one (gas during a mains stop) says its own script
            await self.say(st.script, whole=True)

    async def report_hazard(self, rule_id: str | None, evidence: str) -> Stop:
        """The model's tool: it always stops. Nothing the model does can clear it."""
        self.safety.report_hazard(rule_id, evidence, self.clock())
        await self._safety_sync()
        assert isinstance(self.safety.state, Stop)
        return self.safety.state

    async def _user_hold(self, detail: str) -> None:
        h = Hold(state="HOLD", precondition="user_confirm", detail=detail)
        self.safety.apply(SafetyEvent(t_ms=self.clock(), source="user", kind="raise", to=h, reason=detail))
        await self._safety_sync()

    async def _clear(self, source: Literal["user", "g1"], reason: str) -> bool:
        """A clear the engine allows (it refuses what it must); False when it refused or nothing was held."""
        if not isinstance(self.safety.state, Hold):
            return False
        try:
            if source == "g1":
                self.safety.g1_clear(self.clock())
            else:
                self.safety.apply(SafetyEvent(t_ms=self.clock(), source="user", kind="clear", to=Ok(state="OK"),
                                              reason=reason))
        except rules.SafetyError:
            await self._safety_sync()
            return False
        await self._safety_sync()
        return isinstance(self.safety.state, Ok)

    # ---- inbound -------------------------------------------------------------------------------------------

    def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.ensure_future(self._work())
        if self._guide_worker is None:
            self._guide_worker = asyncio.ensure_future(self._guide_work())

    async def close(self) -> None:
        tasks = ([t for t in (self._worker, self._guide_worker, self._guide_now) if t is not None] + list(self._voice)
                 + list(self._outlining))
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        self._worker = self._guide_worker = self._guide_now = None

    async def _work(self) -> None:
        while True:
            msg = await self._queue.get()
            try:
                self._work_now = asyncio.ensure_future(self._handle_slow(msg))
                await asyncio.wait([self._work_now])
                if self._work_now.cancelled() and self.phase == "checking":
                    self.phase = "instructed"  # a check the person's "I did it" cut short
                elif not self._work_now.cancelled() and self._work_now.exception() is not None:
                    raise self._work_now.exception()  # type: ignore[misc]
            finally:
                self._work_now = None
                self._queue.task_done()

    def queue_check(self, reason: str) -> None:
        """A check the guide asked for: it runs after the guide's turn, and the verdict starts the next one, unless
        the guide asked in a turn nobody asked for (a verdict's): a verdict starts at most one such turn."""
        self._queue.put_nowait({"kind": "check", "msg": reason, "auto": self._auto_turn})

    async def idle(self) -> None:
        """Wait until every queued tap, utterance and guide turn has been handled (tests and the re-run use this)."""
        while True:
            await self._queue.join()
            if self._voice or self._outlining:
                await asyncio.gather(*list(self._voice), *list(self._outlining), return_exceptions=True)
                continue
            await self._guide_q.join()
            if self._queue.empty() and self._guide_q.empty() and not self._voice and not self._outlining:
                return

    async def _guide_work(self) -> None:
        STREAM.set("guide")
        while True:
            item = await self._guide_q.get()
            try:
                if self.guide is not None and not (self.stopped and item["text"] is None):
                    self._auto_turn = item["text"] is None
                    self.guide.asked_view = False
                    self._guide_now = asyncio.ensure_future(self.guide.turn(item["text"]))
                    await asyncio.wait([self._guide_now])
                    if self.guide.asked_view:  # it asked the person to move: it waits for them now
                        latest = self.frames.latest()
                        self._waiting_on = latest.phash if latest else ""
                    if not self._guide_now.cancelled() and self._guide_now.exception() is not None:
                        e = self._guide_now.exception()
                        self.rec.state(self.clock(), "guide_failed", error=f"{type(e).__name__}: {e}"[:200])
            finally:
                self._guide_now = None
                self._auto_turn = False
                self._guide_q.task_done()

    def _tag(self, step: Step) -> str | None:
        """The step_id a line about `step` carries: none while the step is reopened and not yet shown again."""
        return None if self._reopening else step.id

    def _seen_step(self, t: ws.Tap) -> str | None:
        """The step a tap is about: the one the phone says the person saw, else the step current when it came in."""
        return t.step_id or (self.run.step.id if self.run is not None else None)

    async def _stale(self, item: dict[str, Any], what: str) -> bool:
        """A step tap or a spoken "done" or "I did it" about a step the job has left (it came in while the job moved
        on, or the phone still showed the step before): never applied to the step now. Refused, recorded, and the
        person told: a tap by the notice the phone words (tap_not_current), spoken words by a line."""
        seen, run = item.get("step_at"), self.run
        if seen is None or run is None or run.step.id == seen:
            return False
        self.rec.state(self.clock(), "stale_refused", what=what, seen=seen, step_id=run.step.id)
        await self.rail(what, trace.step(run.index), f"for {seen}, not this step: not counted", 0, step_id=run.step.id)
        if isinstance(item["msg"], ws.Tap):
            await self.notice("tap_not_current")
        else:
            await self.say(in_lang(STALE, self.lang), step_id=run.step.id)
        return True

    def _cut_check_short(self, why: str) -> None:
        """The person says they did the step while the camera checks it: their word confirms it at once, so the check
        in flight is cancelled and its verdict never comes. Not where their word would be refused (a step reopened
        because the camera saw it undone, a hold or stop): there the check runs on."""
        run, t = self.run, self._work_now
        if (self.phase != "checking" or run is None or run.reopened or self.stopped
                or isinstance(self.safety.state, Hold) or t is None or t.done()):
            return
        t.cancel()
        self.rec.state(self.clock(), "check_cancelled", step_id=run.step.id, reason=why)
        self.events.append(f"the check of {run.step.id} was cut short: the person says they did it")

    def _cancel_guide(self) -> None:
        """A stop: drop the queued guide turns and cancel the one in flight (unless the stop came from that turn,
        the guide's own report_hazard, whose turn then ends)."""
        while not self._guide_q.empty():
            self._guide_q.get_nowait()
            self._guide_q.task_done()
        t = self._guide_now
        if t is not None and t is not asyncio.current_task() and not t.done():
            t.cancel()
            self.rec.state(self.clock(), "guide_cancelled", reason="stop")

    async def handle(self, raw: dict[str, Any], audio: bytes | None = None, jpeg: bytes | None = None) -> None:
        """One c2s message. Frames, G1, a stop tap, pings and view checks are handled at once; other taps and
        utterances queue for the worker, so a slow model call (a guide turn) never delays a stop. In a re-run,
        `jpeg`/`audio` carry the recorded bytes the event line does not."""
        if jpeg is not None:  # a frame's jpeg_1024 or a plate photo's jpeg
            raw = {**raw, BYTE_FIELDS[raw["type"]]: __import__("base64").b64encode(jpeg).decode()}
        msg = unions.c2s.validate_python(raw)
        self.rec.client(self.clock(), "c2s", raw)  # the line on the session's clock; the phone's t_ms stays in msg
        self._person_acted(msg)
        if isinstance(msg, ws.Frame):
            await self._on_frame(msg)
        elif isinstance(msg, ws.G1):
            await self._on_g1(msg)
        elif isinstance(msg, ws.Tap) and msg.action == "stop":
            await self._on_tap(msg)  # never behind a guide turn in the worker's queue
        elif isinstance(msg, ws.Tap) and msg.action == "end":
            pass  # on the record; the server ends the session (Registry.end)
        elif isinstance(msg, ws.Ping):
            await self.emit({"type": "pong"})
        elif isinstance(msg, ws.View):
            self.view = msg
            if self.phase == "held":
                self._queue.put_nowait({"kind": "retry", "msg": msg})
        elif isinstance(msg, ws.Utterance):
            wav = audio or (__import__("base64").b64decode(msg.audio_wav_b64) if msg.audio_wav_b64 else None)
            if msg.audio_wav_b64 and wav:
                self.rec.audio(msg.utterance_id, wav)
            task = asyncio.ensure_future(self._on_utterance(msg, wav))
            self._voice.add(task)
            task.add_done_callback(self._voice.discard)
        elif isinstance(msg, ws.Tap | ws.PlatePhoto | ws.JobReady):
            item: dict[str, Any] = {"kind": msg.type, "msg": msg, "wav": audio}
            if isinstance(msg, ws.Tap) and msg.action in STEP_TAPS:
                item["step_at"] = self._seen_step(msg)
                if msg.action == "did_it" and self.run is not None and item["step_at"] == self.run.step.id:
                    self._cut_check_short("did_it tap")
            self._queue.put_nowait(item)

    async def _on_frame(self, m: ws.Frame) -> None:
        f = decode(m.frame_id, m.t_ms, m.jpeg_1024, m.phash, m.width, m.height)
        self.frames.add(f)
        self.rec.frame(f.frame_id, f.jpeg)  # every frame, so replay can show each one and a re-run can use it
        if self._g1_pause_at is not None and f.t_ms > self._g1_pause_at + 1000:
            self._g1_pause_at = None
            if await self._clear("g1", "fresh frames after a G1 pause"):
                await self.notice("resumed")
        await self._frame_retry(f.t_ms)

    async def _frame_retry(self, t_ms: float) -> None:
        """A step held for want of a frame (fresh_frame) or an outline (zone_outlined) tries again when frames come:
        the job starts at hello, before the camera's first frame. An outline costs SAM, so that retry waits
        FRAME_RETRY_MS between tries; one retry is queued at a time."""
        run = self.run
        if self.phase != "held" or run is None or self._retry_queued:
            return
        if run.asked == "fresh_frame" or run.asked == "zone_outlined" and t_ms - self._retry_at >= FRAME_RETRY_MS:
            self._retry_queued, self._retry_at = True, t_ms
            self._queue.put_nowait({"kind": "retry", "msg": None})

    async def _on_g1(self, g: ws.G1) -> None:
        if self.mode == "capture":
            return  # capture draws no zones; G1 runs on the phone and the event is recorded above
        rule_id = self.zone_rules.get(g.zone_id)
        dist = "" if g.dist_cm is None else f" at {g.dist_cm:g} cm"
        reason = f"{g.reason or g.kind}{dist}, zone {g.zone_id}"
        before = self.safety.state
        self.safety.on_g1(g.kind, reason if g.kind == "stop" else (g.reason or "pause"), g.t_ms, rule_id)
        await self._safety_sync()
        if g.kind == "pause" and self.safety.state != before and isinstance(self.safety.state, Hold):
            self._g1_pause_at = g.t_ms
            await self.notice("hand_lost" if g.reason == "hand_lost" else "paused")

    # ---- the job ------------------------------------------------------------------------------------------

    @property
    def job(self) -> Job | None:
        if self.card is None or self.job_id is None:
            return None
        return next((j for j in self.card.jobs if j.id == self.job_id), None)

    def part(self, part_id: str) -> Part | None:
        return next((p for p in self.card.parts if p.id == part_id), None) if self.card else None

    def gas_zones(self, step: Step) -> list[tuple[str, Part]]:
        """(rule_id, part) for the step's red zones whose rule is a gas rule."""
        if self.card is None:
            return []
        zones = {z.id: z for z in self.card.red_zones}
        out = []
        for zid in step.zones:
            z = zones.get(zid.root)
            p = self.part(z.part_id) if z is not None else None
            if z is not None and p is not None and z.rule_id.split(".")[1] == "gas":
                out.append((z.rule_id, p))
        return out

    def gas_parts(self, step: Step) -> list[Part]:
        return [p for _, p in self.gas_zones(step)]

    async def _gas_hold(self) -> None:
        """HOLD user_confirm with the gas rule's id: the phone's one-tap ask (confirm_gas_tap / no) keys on it."""
        assert self.run is not None
        gas = self.gas_zones(self.run.step)
        if not gas or self.stopped:
            return
        h = Hold(state="HOLD", precondition="user_confirm", rule_id=gas[0][0], detail=GAS_DETAIL)
        self.safety.apply(SafetyEvent(t_ms=self.clock(), source="code", kind="raise", to=h, reason=GAS_DETAIL))
        await self._safety_sync()

    def _gas_held(self) -> bool:
        h = self.safety.state
        return isinstance(h, Hold) and h.precondition == "user_confirm" and h.detail == GAS_DETAIL

    def evidence(self, step: Step) -> rules.Evidence:
        """What code has seen, for the rules' step gate. Never the model's word."""
        zone_ids = [z.root for z in step.zones]
        latest = self.frames.latest()
        return rules.Evidence(
            plug_out_seen=self.power_off_seen,
            ha_power_w=self.ha_power_w,
            view=self.view.kind if self.view is not None and self.view.ok else None,
            zone_outlined=all(z in self.zone_rules for z in zone_ids),
            view_scale_ok=self.view is not None and self.view.ok and self.view.scale_cm_per_px is not None,
            frame_fresh=self.models.pick("frame_fresh", latest is not None and self.clock() - latest.t_ms < 2000),
            ring_warning_confirmed=self.ring_warned,
            breaker_trips=self.safety.breaker_trips,
        )

    async def greet(self) -> None:
        """Said once, first, in every session that talks: the fixed AI disclosure (rules' text, never model text). A
        job that opens with its preview says it last in the job's opening instead (start_job)."""
        if self.mode == "capture" or self.opening and self.preview and self.job is not None:
            return
        await self._disclose()

    async def _disclose(self) -> None:
        if not self._disclosed:
            self._disclosed = True
            await self.say(rules.disclaimer("spoken", self.lang), whole=True)

    def opening_lines(self, job: Job, preview: dict[str, Any]) -> list[str]:
        """The job's opening from its preview, line by line: the job's title, its steps and minutes, what to get (the
        needs the preview shows), then read the steps and tap the ready button. The card's own lines (title, needs)
        are said whole, as the card has them, never inside a sentence of ours; a held one is left out."""
        words, held = in_lang(OPENING, self.lang), in_lang(HELD_STEP, self.lang)
        n = len(preview["steps"])
        m = round(job.duration_min.value) if job.duration_min is not None else None
        lines = [_stop(preview["title"])] if preview["title"] != held else []
        key = "steps" + ("_1" if n == 1 else "") + ("" if m else "_no_time")
        lines.append(words[key].format(n=n, m=max(1, m or 1)))
        needs = [_stop(x["what"]) for x in preview["needs"] if x["what"] != held]
        if needs:
            lines += [words["needs"], *needs]
        lines.append(words["read" if needs else "read_no_needs"])
        return lines

    async def start_job(self) -> None:
        job = self.job
        if job is None or self.mode == "capture":
            return
        self.safety.start_job(job.id, self.clock())
        await self._safety_sync()
        if self.stopped:
            if self.opening and self.preview:
                await self._disclose()  # no opening to end with it
            return
        self.hooks.job_started(self)
        self.job_checked = False
        self.by_person = set()
        self.stood = set()
        self.resume_at = None
        self.rec.state(self.clock(), "job", job_id=job.id)
        if self.preview:
            self.run = None
            self.phase = "preview"
            preview = await self.emit(await self._job_preview(job))
            if self.opening:
                self.rec.state(self.clock(), "opening", job_id=job.id)
                for line in self.opening_lines(job, preview):
                    await self.say(line, whole=True)
                await self._disclose()  # the AI disclosure closes the opening
            return
        await self.begin_step(0)

    async def _job_preview(self, job: Job) -> dict[str, Any]:
        """The job up front: what to have ready and every step, each text through G0 (a held one gives the held
        step line, as on the step card). Read only: each step's checks and gates run at the step as always."""
        assert self.card is not None
        lang = self.lang
        held = in_lang(HELD_STEP, lang)

        def own(t: Any) -> str:
            return t.root.get(lang) or next(iter(t.root.values()))

        needs: list[dict[str, Any]] = []
        quotes = {q.id: q for q in self.card.quotes or []}
        for n in job.needs or []:
            q = quotes.get(n.quote_ref)
            if q is None:
                continue
            item: dict[str, Any] = {"id": n.id, "what": await self.gate(own(n.what)) or held, "page": q.page,
                                    "quote": await self.gate(_tidy(q.text)) or held}
            if n.kind:
                item["kind"] = n.kind
            if q.en and (en := await self.gate(_tidy(q.en))):
                item["quote_en"] = en
            needs.append(item)
        steps: list[dict[str, Any]] = []
        for st in job.steps:
            warnings: list[dict[str, Any]] = []
            if st.power in ("on", "off"):
                warnings.append({"kind": "power", "text": await self.gate(in_lang(POWER_LINE, lang)[st.power])
                                 or held})
            for zid in st.zones:
                z = next((z for z in self.card.red_zones if z.id == zid.root), None)
                if z is not None:
                    text = await self.gate(_tidy(z.quote.text)) or await self.gate(in_lang(ZONE_LINE, lang)) or held
                    warnings.append({"kind": "zone", "text": text, "rule_id": z.rule_id})
            row: dict[str, Any] = {"step_id": st.id, "text": await self.gate(own(st.text)) or held,
                                   "power": st.power, "camera": camera(st), "warnings": warnings}
            if st.origin == "fixit":
                row["origin"] = "fixit"
            else:
                row["page"] = st.page
                if st.quote is not None:
                    row["quote"] = await self.gate(_tidy(st.quote.text)) or held
                    if st.quote.en and (en := await self.gate(_tidy(st.quote.en))):
                        row["quote_en"] = en
            steps.append(row)
        return {"type": "job_preview", "job_id": job.id, "title": await self.gate(own(job.title)) or held,
                "needs": needs, "steps": steps}

    async def job_ready(self, m: ws.JobReady) -> None:
        """The person's "I'm ready" after the preview: recorded as their word (what they have, what they go without),
        then step 0. Only once, only for this job, and not while a hold or stop stands (after a pause the person
        says ready again); it clears nothing."""
        job = self.job
        if (job is None or self.phase != "preview" or m.job_id != job.id or self.stopped
                or isinstance(self.safety.state, Hold)):
            return
        ids = [n.id for n in job.needs or []]
        self.rec.state(self.clock(), "job_ready", job_id=job.id, by_person=True,
                       ready=[i for i in ids if i in m.ready], without=[i for i in ids if i not in m.ready])
        without = [i for i in ids if i not in m.ready]
        self.events.append("the person says they are ready" + (f", going ahead without {', '.join(without)}"
                                                                if without else ""))
        self.phase = "idle"
        await self.begin_step(0)

    def pick_frame(self, what: str, f: StoredFrame | None) -> StoredFrame | None:
        fid = self.models.pick(what, None if f is None else f.frame_id)
        got = self.frames.get(fid) if fid else None
        if got is not None:
            self.rec.frame(got.frame_id, got.jpeg)
        return got

    async def begin_step(self, index: int) -> None:
        job = self.job
        if job is None or self.stopped:
            return
        step = job.steps[index]
        self.run = StepRun(index, step)
        self.ring_warned = False
        self._step_rounds_from = self.guide.rounds if self.guide is not None else 0
        self._waiting_on = None
        self.rec.state(self.clock(), "phase", to="step", step_id=step.id, index=index)
        gas = self.gas_parts(step)
        if gas and not self.gas_confirmed:
            self.phase = "gas_confirm"
            await self._gas_hold()
            for p in gas:
                await self.outline_part(p.id)
            await self.say(GAS_CONFIRM.get(self.lang, GAS_CONFIRM["en"]), step_id=self._tag(step))
            return
        await self._instruct()

    async def _instruct(self) -> None:
        assert self.run is not None and self.card is not None
        step = self.run.step
        for zid in step.zones:  # the zones are outlined first: the gate needs them drawn
            z = next((z for z in self.card.red_zones if z.id == zid.root), None)
            if z is not None and z.id not in self.zone_rules:
                await self.outline_part(z.part_id)
        g = self.safety.on_step(step, self.evidence(step), self.clock())
        await self._safety_sync()
        if isinstance(g, rules.StepStop):
            return
        if isinstance(g, rules.StepHold):
            self.phase = "held"
            self.rec.state(self.clock(), "phase", to="held", step_id=step.id, precondition=g.precondition)
            if self.run.asked != g.precondition:
                self.run.asked = g.precondition
                await self.say(in_lang(ASK_FOR, self.lang)[g.precondition], step_id=self._tag(step))
            return
        self.run.asked = None
        text = step.text.root.get(self.lang) or next(iter(step.text.root.values()))
        shown = await self.gate(text, step_id=step.id)
        quote = await self.gate(_tidy(step.quote.text), step_id=step.id) if step.quote is not None else None
        quote_en = (await self.gate(_tidy(step.quote.en), step_id=step.id)
                    if step.quote is not None and step.quote.en else None)
        job = self.job
        assert job is not None
        msg: dict[str, Any] = {
            "type": "step", "job_id": job.id, "step_id": step.id, "index": self.run.index, "of": len(job.steps),
            "text": shown if shown is not None else in_lang(HELD_STEP, self.lang),
            "view": step.view, "power": step.power}
        if camera(step) != "check":
            msg["camera"] = camera(step)  # absent means check
        if step.origin == "fixit":
            msg["origin"] = "fixit"  # our own step: no page and no quote; the phone marks it as not the manual's
        else:
            msg["page"] = step.page
        if quote is not None:
            msg["quote"] = quote
            if quote_en is not None:
                msg["quote_en"] = quote_en  # under the manual's words, for a reader without the manual's language
        self.run.msg = await self.emit(msg)
        if shown is not None:
            await self.say(shown, whole=True, step_id=step.id)  # the step's words are said too, not only shown
        self.run.before = self.pick_frame("before", self.frames.latest())
        if self.run.before is not None:
            self.frames.pin(self.run.before)
        self.phase = "instructed"
        if camera(step) != "none" and step.parts:  # a step the camera has no part in: nothing to find or outline
            task = asyncio.ensure_future(self._outline_step(self.run, [p.root for p in step.parts]))
            self._outlining.add(task)
            task.add_done_callback(self._outlining.discard)
        self.events.append(f"step {self.run.index + 1} of {len(job.steps)} shown: {step.id}")

    async def _outline_step(self, run: StepRun, part_ids: list[str]) -> None:
        """The step's parts, outlined after the step is shown, on their own stream: the worker goes on at once."""
        STREAM.set("outline")
        for pid in part_ids:
            if self.run is not run or self.stopped:
                return
            await self.outline_part(pid, run=run)

    async def outline_part(self, part_id: str, frame: StoredFrame | None = None, kind: str | None = None,
                           run: StepRun | None = None) -> locate.Outlined | None:
        """Locate and outline one card part on the newest frame (or the one named); red zones also go to G1. With
        `run`, the outline is for that step: once the job has left it (the person confirmed it while locate and SAM
        worked), nothing is drawn or kept."""
        part = self.part(part_id)
        f = frame or self.pick_frame("latest", self.frames.latest())
        if part is None or f is None or self.card is None or self.mode == "capture":
            return None
        missed = self.models.missed
        out, found = await locate.outline(self.models, part, f, self.lang)
        if run is not None and self.run is not run:
            self.rec.state(self.clock(), "outline_dropped", part_id=part.id, step_id=run.step.id)
            self.events.append(f"outline of {part.id} dropped: {run.step.id} is over")
            return None
        cue = bool(found and found.cue_seen)
        if found is not None:
            await self.rail("locate", trace.name(part.names, self.lang, part.id),
                            "found it" if found.found else "not in view", found.ms, part_id=part.id,
                            box=list(found.box) if found.found and found.box else None)
        slow = (found is not None and found.error in ("timeout", "failed")) or out.failed
        if slow and self.models.missed == missed:  # a missing fixture already said no_recording
            await self.slow("slow_outline")
        prev = self.regions.get(part.id)
        if slow and out.region is None and prev is not None:
            # The call failed, not the view: the phone keeps tracking the last outline (optical flow), and the zone
            # keeps its rule. Nothing new is drawn.
            await self.rail("segment", trace.name(part.names, self.lang, part.id), "kept the tracked outline", 0,
                            part_id=part.id, source="tracked")
            return locate.Outlined(prev, 1, "box" if prev.source == "box" else "sam", 0.0, "kept the tracked outline")
        if found is not None and not found.found and out.region is None:
            self.events.append(f"locate {part.id}: no box; ask for a better view (nothing drawn)")
            return out
        if out.source == "none" and part.kind == "red_zone" and found is None:
            return None
        await self.rail("segment", trace.name(part.names, self.lang, part.id),
                        "couldn't outline it" if out.region is None else
                        "it fills the view" if part.kind != "red_zone" and out.region.spans_view() else "outlined it",
                        out.ms, part_id=part.id,
                        source=out.source, count=out.count, conf=out.region.conf if out.region else None)
        if out.region is None:
            return out
        if run is not None and self.run is not run:
            self.rec.state(self.clock(), "outline_dropped", part_id=part.id, step_id=run.step.id)
            return None
        self.regions[part.id] = out.region
        self.cue_seen[part.id] = cue
        red = part.kind == "red_zone"
        if not red and out.region.spans_view():
            # the part fills the view: the check reads the region, but nothing is drawn around the screen's edges
            # (a red zone is always drawn)
            self.events.append(f"{part.id} fills the view: no outline drawn")
            return out
        label = await self.gate(locate.outline_label(self.card, part, self.lang, cue),
                                step_id=self.run.step.id if self.run is not None else None)
        o: dict[str, Any] = {"type": "outline", "part_id": part.id,
                             "kind": kind or ("do_not_touch" if red else "target"), "mask_svg": out.region.svg(),
                             "frame_id": out.region.frame_id, "conf": out.region.conf}
        if label is not None:
            o["label"] = label
        await self.emit(o)
        if red:
            z = next((z for z in self.card.red_zones if z.part_id == part.id), None)
            if z is not None:
                rings = (self.run.step.zone_rings or []) if self.run is not None else []
                ring = max(3.0, z.ring_cm, *[r.ring_cm for r in rings if r.zone_id == z.id])
                self.zone_rules[z.id] = z.rule_id
                await self.emit({"type": "zone", "zone_id": z.id, "rule_id": z.rule_id, "ring_cm": ring,
                                 "mask_svg": out.region.svg(), "frame_id": out.region.frame_id})
        return out

    async def region_on(self, part_id: str, f: StoredFrame) -> Region | None:
        """The part's outline on frame f: reused if the view has not moved, else SAM from its last box."""
        prev = self.regions.get(part_id)
        if prev is not None:
            pf = self.frames.get(prev.frame_id)
            if prev.frame_id == f.frame_id or (pf is not None and hamming(pf.phash, f.phash) <= 5):
                return prev
            out = await locate.segment(self.models, f, box=prev.box)
            if out.region is not None:
                self.regions[part_id] = out.region
                return out.region
            return None
        out2 = await self.outline_part(part_id, f)
        return out2.region if out2 is not None else None

    # ---- the check -------------------------------------------------------------------------------------------

    async def check(self, reason: str) -> Verdict | None:
        run = self.run
        if run is None or self.phase != "instructed" or self.stopped or self.card is None:
            return None
        if run.step.origin != "fixit" and camera(run.step) != "check":
            self.events.append(f"{run.step.id} has no camera check ({camera(run.step)}): the person confirms it"
                               " with \"I did it, continue\"")
            return None
        if run.step.origin == "fixit":
            # Our own step (plug it back in) has nothing for the camera to check: no clause, part or witness. It ends
            # on the user's word, in a verdict that says so (by_person) and names no judge or witness.
            self._n_check += 1
            run.attempts += 1
            v = Verdict(step_id=run.step.id, check_id=f"k{self._n_check}", attempt=run.attempts, result="done",
                        clause_ids=[], witnesses=Witnesses(sam="none", by_person=True), latency_ms=0)
            self.rec.state(self.clock(), "our_step_ended", step_id=run.step.id, reason=reason)
            self.events.append(f"our own step {run.step.id} ended on the user's word, not checked by the camera")
            await self._verdict(v)
            return v
        self.phase = "checking"
        self._n_check += 1
        check_id = f"k{self._n_check}"
        run.attempts += 1
        step = run.step
        await self.emit({"type": "checking", "step_id": step.id, "check_id": check_id})  # at once
        t0 = time.perf_counter()
        if self._outlining:  # the step's own outline, still on its way: the check reads that, not a second locate
            await asyncio.wait(set(self._outlining))
        pair = self.frames.now_pair()
        n1 = self.pick_frame("now1", pair[0] if pair else None)
        n2 = self.pick_frame("now2", pair[1] if pair else None)
        before = run.before or n1
        if n1 is None or n2 is None or before is None:
            self.phase = "instructed"
            v = combine(step.id, check_id, run.attempts, JudgeFinding("cant_see", [c.id for c in step.done_when],
                                                                        "closer", "no frames"),
                        SamWitness("none", None), None, 0)
            await self._verdict(v)
            return v
        spec = witness.spec_of(step)
        focus = (spec.anchor_id or spec.part_id) if spec else (step.parts[0].root if step.parts else None)
        if spec is not None and judge.out_for_cleaning(step):
            # A part being cleaned may be out of its seat (in a hand, at the sink): outline it where it is now, so the
            # judge's points and SAM's track are read there, not at the seat it was outlined in.
            fresh = await self.outline_part(spec.part_id, n1)
            region = fresh.region if fresh is not None else None
            if region is None:
                self.regions.pop(spec.part_id, None)
        else:
            region = await self.region_on(focus, n1) if focus else None
        ev: power.PowerEvidence | None = None
        provider = self.witness_provider
        if step.power == "off" and (focus is None or not self.power_off_seen):
            # once the camera has seen the plug out, a later step at the filter need not show it again (the rules'
            # gate still reads HA at each step's start)
            ev, region, provider = await stepcheck.power_ground(self.models, n1, focus, region, provider,
                                                                self.ha or _no_ha(), self.alias)
            self.ha_power_w = ev.ha_w
            await self.rail("power", "plug and socket", ev.rail, 0)
        part_region = self.regions.get(spec.part_id) if spec else None
        anchor_region = self.regions.get(spec.anchor_id) if spec and spec.anchor_id else None
        clip_ids = self.models.pick("clip", [f.frame_id for f in self.frames.window(config.CLIP_S)])
        clip = [f for f in (self.frames.get(i) for i in clip_ids) if f is not None]
        for f in clip:
            self.rec.frame(f.frame_id, f.jpeg)
        sam_part = self.part(spec.part_id) if spec else None
        jr, finding, sam = await stepcheck.judge_and_witness(
            self.models, step, self.lang, stepcheck.Frames(before, n1, n2, clip),
            stepcheck.Regions(region, part_region, anchor_region), sam_part.sam_text if sam_part else "",
            provider)
        pw: PowerWitness | None = None
        if ev is not None:
            pw = ev.witness()
            if pw.result == "done":
                self.power_off_seen = True
        if jr.timed_out:
            await self.slow("slow_check")
        ms = (time.perf_counter() - t0) * 1000
        v = combine(step.id, check_id, run.attempts, finding, sam, pw, ms)
        await self.rail("check_step", trace.step(run.index), trace.VERDICT.get(v.result, v.result), jr.ms,
                        step_id=step.id, result=v.result, judge=finding.result, sam_ran=sam.ran, sam=sam.result)
        self.phase = "instructed"
        await self._verdict(v)
        return v

    async def did_it(self) -> Verdict | None:
        """The person says they did the step ("I did it, continue", on every step): it ends in a by_person verdict,
        confirmed by them, never checked. Never while a safety hold or stop stands or a check runs (phase other than
        instructed), and not on a step reopened because the camera saw it undone before the start."""
        run = self.run
        if (run is None or run.reopened or self.phase != "instructed" or self.stopped
                or isinstance(self.safety.state, Hold)):
            return None
        self._n_check += 1
        run.attempts += 1
        v = Verdict(step_id=run.step.id, check_id=f"k{self._n_check}", attempt=run.attempts, result="done",
                    clause_ids=[], witnesses=Witnesses(sam="none", by_person=True), latency_ms=0)
        if run.step.origin != "fixit":
            self.by_person.add(run.step.id)
        self.rec.state(self.clock(), "confirmed_by_person", step_id=run.step.id)
        self.events.append(f"the person says they did {run.step.id}: confirmed by them, not checked by the camera")
        await self._verdict(v)
        return v

    async def _verdict(self, v: Verdict) -> None:
        assert self.run is not None
        self.run.last_verdict = v
        await self.emit({"type": "verdict", "verdict": v.model_dump(exclude_none=True)})
        self.events.append(f"verdict for {v.step_id}: {v.result}" + (f", ask for {v.ask_for}" if v.ask_for else "")
                           + f" (attempt {v.attempt})")
        if v.result != "done":
            if self.run.attempts >= config.MAX_CHECK_ATTEMPTS and v.result == "cant_see":
                if not self.run.did_it_offered:
                    self.run.did_it_offered = True
                    self.rec.state(self.clock(), "did_it_offered", step_id=v.step_id, attempts=self.run.attempts)
                    await self.notice("did_it_offered")
                self.events.append(f"{self.run.attempts} checks could not see it: the person now has \"I did it,"
                                   " continue\"; say so, and do not call the step checked if they use it")
            elif self.run.attempts >= config.MAX_CHECK_ATTEMPTS:
                self.events.append("three checks without done: offer a closer look, then say it needs service")
            return
        await self._next_step()

    async def _next_step(self) -> None:
        """After a step: the next one, or the job's end. The job counts as done (job_done_checked) when its last
        manual step passes the check; only our own steps (origin fixit, which the camera cannot check) may follow."""
        job, run = self.job, self.run
        assert job is not None and run is not None
        nxt = run.index + 1
        if run.reopened and self.resume_at is not None:  # done now: back to the start it interrupted
            nxt, self.resume_at = self.resume_at, None
        if self.before_start(nxt):
            back = await self.recheck_confirmed(nxt)
            if back is not None:
                self.resume_at = nxt
                await self.reopen(back)
                return
        rest = job.steps[nxt:]
        if (job.steps[nxt - 1].origin != "fixit" and all(st.origin == "fixit" for st in rest)
                and not self.job_checked):
            self.job_checked = True
            if self.by_person:  # done, on the person's word for some steps: recorded as confirmed by them
                self.hooks.job_done_by_person(self)
                self.events.append("every manual step done, some confirmed by the person and not checked: the job"
                                   " is recorded as done, confirmed by you")
            else:
                self.hooks.job_done_checked(self)
                self.events.append("every manual step checked done: the job is recorded as done")
        if rest:
            await self.begin_step(nxt)
        else:
            self.phase = "job_done"
            self.rec.state(self.clock(), "phase", to="job_done")
            self.events.append("every step done: the job is done")

    # ---- the person's word, seen again before the start -------------------------------------------------------

    def before_start(self, index: int) -> bool:
        """Is step `index` the start: the job's last manual step (the program, the machine on), a step that needs the
        power on, our own step (plug it back in), or the job's end?"""
        job = self.job
        if job is None:
            return False
        manual = [i for i, st in enumerate(job.steps) if st.origin != "fixit"]
        return (index >= len(job.steps) or (bool(manual) and index == manual[-1])
                or job.steps[index].power == "on" or job.steps[index].origin == "fixit")

    def rechecked(self, step: Step) -> bool:
        """A step the person confirmed that is checked again before the start: one whose part should still be
        where it was left (a witness that expects it present). A step that takes a part out is left out (a later
        step puts it back), and so is one a later step of ours restores (the plug goes back in)."""
        w = step.witness
        job = self.job
        restored = job is not None and any(st.restores == step.id for st in job.steps)
        return (step.id in self.by_person and step.origin != "fixit" and camera(step) == "check" and not restored
                and w is not None and w.expect == "present")

    def unchecked(self, step: Step) -> bool:
        """A manual step the camera does not check (camera find or none): Done means the person's word."""
        return step.origin != "fixit" and camera(step) != "check"

    async def recheck_confirmed(self, upto: int) -> int | None:
        """Before the start, each step before `upto` the person confirmed is checked again on the view now, once: a
        step that stands is not looked at again at a later start. The first one the camera sees undone is returned:
        its witness says not done, or the judge says not done and the witness did not see it done. None when all
        stand (a view that shows nothing either way lets the person's word stand)."""
        job = self.job
        assert job is not None
        for i, st in enumerate(job.steps[:upto]):
            if not self.rechecked(st) or st.id in self.stood:
                continue
            if upto < len(job.steps):  # the job's end has no step to show it on: the phone shows the job done
                self._n_check += 1
                await self.emit({"type": "checking", "step_id": st.id, "check_id": f"k{self._n_check}"})
            finding, sam = await self._look_again(st)
            undone = sam.result == "not_done" or (finding.result == "not_done" and sam.result != "done")
            self.rec.state(self.clock(), "recheck", step_id=st.id, judge=finding.result, sam=sam.result,
                           undone=undone)
            seen = "not done after all" if undone else "still done" if "done" in (finding.result, sam.result) \
                else "could not see it: their word stands"
            await self.rail("check_step", trace.step(i), seen, 0,
                            step_id=st.id, recheck=True, judge=finding.result, sam_ran=sam.ran, sam=sam.result)
            if undone:
                return i
            self.stood.add(st.id)
        return None

    async def _look_again(self, step: Step) -> tuple[JudgeFinding, SamWitness]:
        pair = self.frames.now_pair()
        n1 = self.pick_frame("now1", pair[0] if pair else None)
        n2 = self.pick_frame("now2", pair[1] if pair else None)
        if n1 is None or n2 is None:
            return JudgeFinding("cant_see", [], "closer", "no frames"), SamWitness("none", None)
        spec = witness.spec_of(step)
        assert spec is not None
        region = await self.region_on(spec.anchor_id or spec.part_id, n1)
        part_region = self.regions.get(spec.part_id)
        anchor_region = self.regions.get(spec.anchor_id) if spec.anchor_id else None
        clip_ids = self.models.pick("clip", [f.frame_id for f in self.frames.window(config.CLIP_S)])
        clip = [f for f in (self.frames.get(i) for i in clip_ids) if f is not None]
        sam_part = self.part(spec.part_id)
        _, finding, sam = await stepcheck.judge_and_witness(
            self.models, step, self.lang, stepcheck.Frames(n1, n1, n2, clip),
            stepcheck.Regions(region, part_region, anchor_region), sam_part.sam_text if sam_part else "",
            self.witness_provider)
        return finding, sam

    async def reopen(self, index: int) -> None:
        """The camera saw a step the person confirmed undone: the job goes back to it, it is no longer confirmed,
        and only a check that sees it done ends it now ("I did it" is not offered on it again)."""
        job = self.job
        assert job is not None
        step = job.steps[index]
        self.by_person.discard(step.id)
        self.rec.state(self.clock(), "reopened", step_id=step.id)
        self.events.append(f"before the start, the camera sees {step.id} not done after all: the job goes back to it;"
                           " say what you see and that it must be done before the machine starts; only the camera's"
                           " check ends it now")
        # said first and with no step_id: the phone has left this step, and drops a line about a step it left until
        # the step's message comes, which its own gate may hold back
        await self.say(in_lang(REOPENED, self.lang))
        self._reopening = True
        try:
            await self.begin_step(index)
        finally:
            self._reopening = False
        if self.run is not None and self.run.step.id == step.id:
            self.run.reopened = True

    # ---- the worker's events ---------------------------------------------------------------------------------

    async def _handle_slow(self, item: dict[str, Any]) -> None:
        msg = item["msg"]
        if item["kind"] == "retry":
            self._retry_queued = False
            await self.retry_gate()
        elif item["kind"] == "check":
            await self.check(item["msg"])
            if not item.get("auto"):
                await self._guide_turn(None)
        elif isinstance(msg, ws.PlatePhoto):
            from app import plates

            await plates.on_plate_photo(self, msg, self.plate_reader)
        elif isinstance(msg, ws.Tap):
            if msg.action in STEP_TAPS and await self._stale(item, msg.action):
                return
            await self._on_tap(msg)
        elif isinstance(msg, ws.JobReady):
            await self.job_ready(msg)
        elif item["kind"] == "heard":
            text = item["text"]
            if self.job is None:
                self.hooks.history(self, "voice_note", text)
                await self.rail("voice_note", "", "kept it in your history", 0, utterance_id=msg.utterance_id)
                return
            said = said_done(text) if self.phase == "instructed" else None
            if said is not None and await self._stale(item, said):
                said = None  # the words still reach the guide
            if said == "did_it" or said == "done" and self.run is not None and self.run.did_it_offered:
                await self.did_it()  # the words of the "I did it, continue" tap (and "done" once it is offered)
            elif said == "done" and self.run is not None and self.unchecked(self.run.step):
                await self.did_it()  # nothing for the camera to check: the person's word ends it
            elif said == "done":
                await self.check("the user said done")  # the Done tap's word: the camera checks
            await self._guide_turn(text)

    async def _on_utterance(self, msg: ws.Utterance, wav: bytes | None) -> None:
        """An utterance at once, never behind the worker or a guide turn: its words, then the hazard check (a gas
        smell, a CO alarm, smoke: STOP first). What the words ask for (a check, the guide's answer) queues after."""
        from app import voice

        STREAM.set("voice")
        async with self._voice_lock:
            text = await voice.utterance_text(self, msg, wav)
            if text is None:
                return
            for lang in dict.fromkeys((msg.lang, self.lang)):  # the words' language, then the app's (and English)
                self.safety.on_utterance(text, self.clock(), lang)
                if self.stopped:
                    break
            await self._safety_sync()  # a stop says its script there
            if self.stopped:
                return
            if self.phase == "checking" and said_done(text) == "did_it":
                self._cut_check_short("said I did it")
            self._queue.put_nowait({"kind": "heard", "msg": msg, "text": text,
                                    "step_at": self.run.step.id if self.run is not None else None})

    async def _on_tap(self, t: ws.Tap) -> None:
        if t.action == "confirm_plate":
            from app import plates

            await plates.on_confirm(self, t.arg)
        elif t.action == "done" and self.run is not None and self.unchecked(self.run.step):
            await self.did_it()
            await self._guide_turn(None)
        elif t.action == "done":
            await self.check("tap done")
            await self._guide_turn(None)
        elif (t.action in ("confirm_gas_tap", "yes") and self.phase == "gas_confirm" and self.run is not None
              and (self._gas_held() or not isinstance(self.safety.state, Hold))):  # a user stop resumes first
            if self._gas_held() and not await self._clear("user", "the user confirmed the outline"):
                return
            self.gas_confirmed = True
            self.rec.state(self.clock(), "gas_tap_confirmed", step_id=self.run.step.id)
            await self._instruct()
        elif t.action == "no" and self.phase == "gas_confirm" and self.run is not None:
            self.rec.state(self.clock(), "gas_tap_rejected", step_id=self.run.step.id)
            await self.say(GAS_AGAIN.get(self.lang, GAS_AGAIN["en"]), step_id=self.run.step.id)
            await self.begin_step(self.run.index)
        elif t.action == "repeat" and self.run is not None and self.run.msg is not None:
            await self.emit({k: v for k, v in self.run.msg.items() if k not in ("seq", "t_ms")})
            if self.run.msg["text"] != in_lang(HELD_STEP, self.lang):
                await self.say(self.run.msg["text"], whole=True, step_id=self.run.step.id)
        elif t.action == "back" and self.run is not None and self.run.index > 0 and self.phase == "instructed":
            await self.begin_step(self.run.index - 1)
        elif t.action == "did_it":
            await self.did_it()
        elif t.action == "stop":
            await self._user_hold("stopped by the user")
            await self.notice("paused")
        elif t.action == "yes":
            await self._resolve_hold()

    async def _resolve_hold(self) -> None:
        h = self.safety.state
        if isinstance(h, Hold) and h.precondition == "user_confirm" and h.detail == "stopped by the user":
            if await self._clear("user", "the user resumed"):
                await self.notice("resumed")
                if self.phase == "gas_confirm":
                    await self._gas_hold()  # the confirm is still owed
            return
        if self.phase != "held" or self.run is None:
            return
        if self.run.asked == "user_confirm":
            self.ring_warned = True
        elif self.run.asked == "power_off_seen":
            f = self.pick_frame("latest", self.frames.latest())
            if f is None:
                return
            ev = await power.power_evidence(self.models, f, self.ha or _no_ha(), self.alias)
            self.ha_power_w = ev.ha_w
            await self.rail("power", "plug and socket", ev.rail, 0)
            self.power_off_seen = ev.witness().result == "done"
            if not self.power_off_seen:  # the look did not see it: say so, the step stays held
                await self.say(in_lang(PLUG_UNSEEN, self.lang), step_id=self.run.step.id)
        await self.retry_gate()

    async def retry_gate(self) -> None:
        """New evidence (a view check, an outline, a yes): try the held step's gate again."""
        if self.phase == "held" and self.run is not None and not self.stopped:
            await self._instruct()

    async def _guide_turn(self, user_text: str | None) -> None:
        """Queue a guide turn on the guide's own task; the caller does not wait for it. A turn nobody asked for waits
        while the guide waits on the person, and once the step has had its rounds."""
        if self.guide is None or self.mode == "capture" or self.stopped and user_text is None:
            return
        if user_text is None:
            used = self.guide.rounds - self._step_rounds_from
            why = ("waiting for the person" if self._waiting_on is not None
                   else "the step's guide rounds are used" if used >= config.GUIDE_STEP_ROUNDS else None)
            if why is not None:
                self.rec.state(self.clock(), "guide_quiet", reason=why, rounds=used)
                return
        self._guide_q.put_nowait({"text": user_text})

    def _person_acted(self, msg: Any) -> None:
        """Words, a tap or a changed view end the guide's wait on the person."""
        if self._waiting_on is None:
            return
        if isinstance(msg, ws.Frame):
            if _bits_apart(msg.phash, self._waiting_on) < config.VIEW_CHANGED_BITS:
                return
        elif not isinstance(msg, ws.Utterance | ws.Tap | ws.View):
            return
        self._waiting_on = None


def _bits_apart(a: str, b: str) -> int:
    """Hamming distance of two 64-bit phashes in hex; an unreadable one counts as all bits apart."""
    try:
        return bin(int(a, 16) ^ int(b, 16)).count("1")
    except ValueError:
        return 64


def camera(step: Step) -> Literal["check", "find", "none"]:
    """What the camera does on the step (the card leaves it out: check)."""
    return step.camera or "check"


def _stop(line: str) -> str:
    """A card line said on its own: ends like a sentence."""
    line = line.strip()
    return line if line.endswith((".", "!", "?", ":")) else line + "."


def _tidy(text: str) -> str:
    """A quote as drawn: the manual's words, without the space a PDF's text layer leaves before a full stop."""
    return re.sub(r"\s+([.,;:!?])", r"\1", text)


SAFETY_STREAM = frozenset({"safety", "verdict", "checking", "step"})  # the stop and step path, compared in strict order


def stream_of(msg: dict[str, Any]) -> str:
    """The stream an s2c message belongs to: the stop and step path by its type, else the task that emitted it."""
    return "safety" if msg["type"] in SAFETY_STREAM else STREAM.get()


def _no_ha() -> HomeAssistant:
    from app.ports import NoHomeAssistant

    return NoHomeAssistant()

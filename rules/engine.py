"""The safety state machine: OK | HOLD{precondition} | STOP{rule_id, evidence, script} (contracts/safety.schema.json).

It runs before and after every model turn and on every frame event. Who may move it:

- the model may only raise (`report_hazard`): there is no path from the model to a clear, and `apply` refuses one;
- G0 holds sentences but never stops the repair by itself: the held sentence's place is the rule's line;
- check_step raises STOP or HOLD; code clears a HOLD once its precondition is seen;
- G1 raises a hand STOP or a pause, and clears only what G1 raised, when the hand is back clear;
- a STOP from the rules ends that job: the user may start a different job. An emergency (gas smell, CO alarm, fire)
  is never cleared in the session.

There is no event, flag or setting that turns any of this off.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from contracts.models.card import Card, Step
from contracts.models.safety import Hold, Ok, SafetyEvent, Stop
from rules import g0
from rules.country import ALIASES, UTTERANCE_ZONES, Country, load_country, no_country_line, shipped
from rules.steps import Evidence, StepHold, StepOk, StepResult, StepStop, check_step
from rules.text import find_part, normalise

State = Ok | Hold | Stop
Source = Literal["model", "g0", "g1", "rules", "ha", "user", "code"]
EMERGENCY_ZONES = frozenset({"smell", "alarm", "fire"})


class SafetyError(PermissionError):
    """A move the engine refuses, such as the model clearing a stop."""


def stop_script(country: str | Country | None, rule_id: str, lang: str) -> str:
    """The fixed stop script for a rule, in the answer language; English plus the international advice otherwise."""
    c = country if isinstance(country, Country) or country is None else load_country(country)
    if c is None:
        return no_country_line(lang)
    rule = g0.resolve(c, rule_id) or c.rule(f"{c.code}.mechanical.hazard")
    if rule is None:
        return c.emergency(lang)
    texts = rule.script.root
    if lang in texts:
        return texts[lang]
    return f"{texts.get('en') or next(iter(texts.values()))} {c.emergency('en')}"


@dataclass
class SafetyEngine:
    country: str
    lang: str
    card: Card | None = None
    job_id: str | None = None
    state: State = field(default_factory=lambda: Ok(state="OK"))
    events: list[SafetyEvent] = field(default_factory=list)
    _raised_by: Source | None = None
    _stopped_job: str | None = None
    _breaker_trips: int = 0
    _said: str = ""

    def __post_init__(self) -> None:
        self.country = ALIASES.get(self.country, self.country)

    @property
    def _c(self) -> Country | None:
        return load_country(self.country)

    # ---- moves -------------------------------------------------------------------------------------------------

    def apply(self, event: SafetyEvent) -> State:
        """The one place the state changes. Every move is logged, refused moves too."""
        if event.source == "model" and (event.kind == "clear" or isinstance(event.to, Ok)):
            self._log(event.t_ms, "model", "raise", self.state, f"refused: the model cannot clear ({event.reason})")
            raise SafetyError("the model can raise a stop but never clear one")
        if event.kind == "clear":
            self._check_clear(event)
        elif isinstance(self.state, Stop) and not isinstance(event.to, Stop):
            # Nothing lowers a STOP by raising.
            self._log(event.t_ms, event.source, "raise", self.state, f"kept STOP over {event.reason}")
            return self.state
        elif isinstance(self.state, Stop) and isinstance(event.to, Stop) and self._is_emergency(self.state.rule_id):
            return self.state  # an emergency is never replaced by a lesser stop
        self.events.append(event)
        self.state = event.to
        self._raised_by = None if isinstance(event.to, Ok) else event.source
        if isinstance(event.to, Stop) and event.kind == "raise":
            self._stopped_job = self.job_id
        return self.state

    def _check_clear(self, event: SafetyEvent) -> None:
        s = self.state
        if isinstance(s, Ok):
            return
        if isinstance(s, Stop) and self._is_emergency(s.rule_id):
            raise SafetyError(f"{s.rule_id} is an emergency and is not cleared in this session")
        if event.source == "g1" and self._raised_by != "g1":
            raise SafetyError("G1 clears only what G1 raised")
        if isinstance(s, Stop) and self._raised_by != "g1" and event.source != "user":
            raise SafetyError("a rules STOP ends the job; only the user starting a different job clears it")

    def _stop(self, t_ms: float, source: Source, rule_id: str, evidence: str) -> State:
        to = Stop(state="STOP", rule_id=rule_id, evidence=evidence[:300], script=stop_script(self.country, rule_id,
                                                                                               self.lang))
        return self.apply(SafetyEvent(t_ms=t_ms, source=source, kind="raise", to=to, reason=evidence[:300]))

    def _hold(self, t_ms: float, source: Source, h: StepHold) -> State:
        to = Hold(state="HOLD", precondition=h.precondition, rule_id=h.rule_id, detail=h.detail or None)
        return self.apply(SafetyEvent(t_ms=t_ms, source=source, kind="raise", to=to, reason=h.detail or h.precondition))

    def _clear(self, t_ms: float, source: Source, reason: str) -> State:
        if isinstance(self.state, Ok):
            return self.state
        return self.apply(SafetyEvent(t_ms=t_ms, source=source, kind="clear", to=Ok(state="OK"), reason=reason))

    def _log(self, t_ms: float, source: Source, kind: Literal["raise", "clear"], to: State, reason: str) -> None:
        self.events.append(SafetyEvent(t_ms=t_ms, source=source, kind=kind, to=to, reason=reason[:300]))

    def _is_emergency(self, rule_id: str) -> bool:
        parts = rule_id.split(".")
        return len(parts) == 3 and parts[2] in EMERGENCY_ZONES

    # ---- inputs ------------------------------------------------------------------------------------------------

    def report_hazard(self, rule_id: str | None, evidence: str, t_ms: float) -> State:
        """The model's tool. It always stops; with no rule (or one this country lacks) the generic hazard rule."""
        c = self._c
        rid = None
        if c is not None and rule_id:
            rule = g0.resolve(c, rule_id)
            rid = rule.id if rule and rule.action != "ALLOW" else None
        if rid is None:
            rid = f"{self.country}.mechanical.hazard" if c is not None else f"{self.country}.mechanical.no_rules_file"
        return self._stop(t_ms, "model", rid, evidence)

    def on_utterance(self, text: str, t_ms: float, lang: str | None = None) -> State:
        """What the user said. A gas smell, CO alarm, smoke, water on the socket or a second trip stops at once."""
        lang = lang or self.lang
        c = self._c
        norm = normalise(text)
        tables = [c] if c is not None else [x for x in (load_country(code) for code in shipped()) if x is not None]
        for table in tables:
            for rule in table.rules.rules:
                if rule.zone not in UTTERANCE_ZONES or rule.action != "STOP":
                    continue
                for check_lang in dict.fromkeys((lang, "en")):
                    for p in rule.parts.root.get(check_lang, []):
                        if find_part(norm, p.root, check_lang) >= 0:
                            rid = rule.id if c is not None else f"{self.country}.{rule.family}.{rule.zone}"
                            return self._stop(t_ms, "rules", rid, f"user said: {p.root}")
        return self.state

    def on_sentence(self, text: str) -> g0.Result:
        """G0 on one sentence the agent wrote, under the current state and after the sentence before it."""
        r = g0.check_sentence(text, self.lang, self.card, self.state, country=self.country, before=self._said)
        self._said = text
        return r

    def on_step(self, step: Step, evidence: Evidence, t_ms: float) -> StepResult:
        if self.card is None:
            raise ValueError("on_step needs the card")
        result = check_step(step, self.card, evidence, country=self.country)
        if isinstance(result, StepStop):
            self._stop(t_ms, "rules", result.rule_id, result.evidence)
        elif isinstance(result, StepHold):
            self._hold(t_ms, "rules", result)
        elif isinstance(result, StepOk) and isinstance(self.state, Hold) and self._raised_by == "rules":
            self._clear(t_ms, "code", f"step {step.id}: preconditions seen")
        return result

    def on_g1(self, kind: Literal["stop", "pause"], reason: str, t_ms: float, rule_id: str | None = None) -> State:
        """A G1 event from the phone. A stop names the zone's rule; a pause holds until the hand or frame is back."""
        if kind == "stop":
            rid = rule_id or f"{self.country}.mechanical.hazard"
            return self._stop(t_ms, "g1", rid, f"G1 {reason}")
        pre: Literal["hand_visible", "fresh_frame"] = "fresh_frame" if reason == "stale_frame" else "hand_visible"
        return self._hold(t_ms, "g1", StepHold(pre, rule_id, f"G1 {reason}"))

    def g1_clear(self, t_ms: float) -> State:
        """The hand is back clear of the zone, on a fresh frame."""
        return self._clear(t_ms, "g1", "G1: hand clear, frame fresh")

    def breaker_tripped(self, t_ms: float) -> State:
        """Once, the app guides switching it back on. A second trip is a STOP."""
        self._breaker_trips += 1
        if self._breaker_trips >= 2:
            return self._stop(t_ms, "rules", f"{self.country}.mains.breaker_repeat",
                              f"breaker tripped {self._breaker_trips} times")
        return self.state

    @property
    def breaker_trips(self) -> int:
        return self._breaker_trips

    def start_job(self, job_id: str, t_ms: float) -> State:
        """The user starts a job. After a rules STOP, only a different job clears it; an emergency stays."""
        if isinstance(self.state, Stop):
            if self._is_emergency(self.state.rule_id) or job_id == self._stopped_job:
                return self.state
            self._clear(t_ms, "user", f"user started a different job: {job_id}")
        self.job_id = job_id
        return self.state

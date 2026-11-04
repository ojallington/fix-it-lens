// The screen's state, folded from s2c messages. Pure, so live, replay and tests drive the same code.
// What may be drawn as words: a `sentence` (G0 passed it), the step card (G0 passed it), and fixed text from code or
// the rules (the stop script, the held bar's rule line, notices). A `held` message never carries the held sentence.

import type {
  Held,
  JobPreview,
  Notice,
  Outline,
  PlateRead,
  Rail,
  S2C,
  Sentence,
  StepMsg,
  Verdict,
  ZoneMsg,
} from "../../contracts/ts/src/ws.ts";
import type { SafetyState } from "../../contracts/ts/src/safety.ts";
import { parseSvgPolygons, type Pt } from "./geom.ts";

export const RAIL_LEN = 4;
export const SENTENCES_SHOWN = 3;

export interface Shape {
  id: string;
  kind: Outline["kind"] | "zone";
  polys: Pt[][];
  frameId: string;
  label: string | null;
  /** Set once when a step passes, so the outline pulses. */
  pulse: boolean;
}

export interface ZoneState {
  msg: ZoneMsg;
  polys: Pt[][];
}

export interface SessionState {
  sessionId: string | null;
  mode: "api" | "local" | "replay" | null;
  replayId: string | null;
  step: StepMsg | null;
  checking: boolean;
  verdict: Verdict | null;
  outlines: Map<string, Shape>;
  zones: Map<string, ZoneState>;
  sentences: Sentence[];
  /** The newest sentence spoken about this step since it began: the step card says it in place of the card's own line. */
  said: Sentence | null;
  /** The guide's reply to the person's spoken question: its own row on the card, never the step's title. */
  answer: { turn: string; text: string } | null;
  /** A question was sent and its reply has not started yet. */
  asking: boolean;
  held: Held | null;
  /** Sentences held back on this step. */
  heldCount: number;
  safety: SafetyState;
  rail: Rail[];
  notice: Notice | null;
  /** The step the camera could not see after the server's last check: the person may say they did it (tap did_it). */
  didIt: string | null;
  plate: PlateRead | null;
  /** The job up front, before it starts: shown until the person says they are ready (or the first step comes). */
  preview: JobPreview | null;
  /** The job's steps from its preview, kept once it starts: a hold for a step before its own message shows its card. */
  plan: JobPreview | null;
  /** The step a hold's ask names before that step's own message came (the gate holds its card back). */
  heldFor: string | null;
  /** The steps of this job the phone has moved on from (a step taken up again leaves the list). */
  left: string[];
}

export function initial(): SessionState {
  return {
    sessionId: null,
    mode: null,
    replayId: null,
    step: null,
    checking: false,
    verdict: null,
    outlines: new Map(),
    zones: new Map(),
    sentences: [],
    said: null,
    answer: null,
    asking: false,
    held: null,
    heldCount: 0,
    safety: { state: "OK" },
    rail: [],
    notice: null,
    didIt: null,
    plate: null,
    preview: null,
    plan: null,
    heldFor: null,
    left: [],
  };
}

/** A line about a step the job has moved on from: late, so neither shown nor said. A line about the step coming next
 *  (its ask, said before the step itself) is not passed, nor is a line with no step (the disclosure, a hazard script). */
export function passed(s: SessionState, m: { step_id?: string }): boolean {
  return m.step_id !== undefined && s.left.includes(m.step_id);
}

/** Folds one message into the state (mutates and returns it, so a 30 fps loop allocates little). */
export function reduce(s: SessionState, m: S2C): SessionState {
  switch (m.type) {
    case "hello_ok":
      s.sessionId = m.session_id;
      if (s.mode !== "replay") s.mode = m.mode; // the REPLAY badge is permanent once a replay starts
      break;
    case "replay":
      s.replayId = m.replay_id;
      s.mode = "replay";
      break;
    case "step":
      s.preview = null;
      if (s.step?.step_id !== m.step_id) {
        if (s.step && s.step.job_id !== m.job_id) s.left = [];
        else if (s.step) s.left = [...s.left.filter((x) => x !== s.step!.step_id), s.step.step_id];
        s.left = s.left.filter((x) => x !== m.step_id);
        s.verdict = null;
        s.held = null;
        s.heldCount = 0;
        s.said = null;
        s.answer = null;
        s.didIt = null;
        for (const o of s.outlines.values()) o.pulse = false;
      }
      // Any step message ends a check, the same step reopened too.
      s.checking = false;
      s.heldFor = null;
      // A tap that came for the step before is old news once a step comes.
      if (s.notice?.kind === "tap_not_current") s.notice = null;
      s.step = m;
      break;
    case "checking":
      s.checking = true;
      s.verdict = null;
      break;
    case "verdict":
      s.checking = false;
      s.verdict = m.verdict;
      if (m.verdict.result === "done") for (const o of s.outlines.values()) if (o.kind === "target") o.pulse = true;
      break;
    case "outline":
      s.outlines.set(m.part_id, {
        id: m.part_id,
        kind: m.kind,
        polys: parseSvgPolygons(m.mask_svg),
        frameId: m.frame_id,
        label: m.label ?? null,
        pulse: false,
      });
      break;
    case "zone":
      s.zones.set(m.zone_id, { msg: m, polys: parseSvgPolygons(m.mask_svg) });
      break;
    case "sentence":
      if (passed(s, m)) break;
      s.sentences = [...s.sentences, m].slice(-SENTENCES_SHOWN);
      // Under a hold, a line about a step other than the one on screen is that step's ask: the hold is for it.
      if (s.safety.state !== "OK" && m.step_id !== undefined && m.step_id !== s.step?.step_id) s.heldFor = m.step_id;
      {
        // A reply is the guide's turn after a question: its sentences share the turn (`u3` in `u3.s1`).
        const turn = m.id.split(".")[0]!;
        if (s.asking) {
          s.asking = false;
          s.answer = { turn, text: m.text };
        } else if (s.answer?.turn === turn) s.answer = { turn, text: `${s.answer.text} ${m.text}` };
        else if (s.step) s.said = m;
      }
      break;
    case "held":
      if (passed(s, m)) break;
      if (!m.sentence_id || s.held?.sentence_id !== m.sentence_id) s.heldCount++;
      s.held = m;
      break;
    case "safety":
      s.safety = m.state;
      if (m.state.state === "OK") s.heldFor = null;
      break;
    case "rail":
      s.rail = [...s.rail, m].slice(-RAIL_LEN);
      break;
    case "notice":
      s.notice = m;
      if (m.kind === "did_it_offered") s.didIt = s.step?.step_id ?? null;
      break;
    case "plate_read":
      s.plate = m;
      break;
    case "pong":
      break;
    case "job_preview":
      s.preview = m;
      s.plan = m;
      break;
    default: {
      const never: never = m;
      void never;
    }
  }
  return s;
}

/** The person asked something out loud: the guide's next turn is the answer. */
export function asked(s: SessionState): void {
  s.asking = true;
}

/** The step a hold stands on when it is not the one on screen: the step after a finished one (or one taken up again)
 *  is held before its own message comes, and the person should see that step, not the last one. Built from the job's
 *  preview, so its words are the same G0-passed lines. Null when the hold is on the step shown, or the step is unknown. */
export function heldStep(s: SessionState): StepMsg | null {
  const cur = s.step;
  if (s.safety.state === "OK" || cur === null || s.heldFor === null || s.plan?.job_id !== cur.job_id) return null;
  const i = s.plan.steps.findIndex((x) => x.step_id === s.heldFor);
  const p = s.plan.steps[i];
  if (!p) return null;
  return {
    type: "step",
    seq: cur.seq,
    t_ms: cur.t_ms,
    job_id: cur.job_id,
    view: cur.view,
    step_id: p.step_id,
    index: i,
    of: s.plan.steps.length,
    text: p.text,
    power: p.power,
    camera: p.camera,
    ...(p.page !== undefined ? { page: p.page } : {}),
    ...(p.origin !== undefined ? { origin: p.origin } : {}),
    ...(p.quote !== undefined ? { quote: p.quote } : {}),
    ...(p.quote_en !== undefined ? { quote_en: p.quote_en } : {}),
  };
}

/** The step on screen has been checked done (or passed on the person's word): what comes next is not about it. */
export function stepFinished(s: SessionState): boolean {
  return s.step !== null && s.verdict?.step_id === s.step.step_id && s.verdict.result === "done";
}

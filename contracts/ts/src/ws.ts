/* Generated from contracts/*.schema.json by `make contracts`. Do not edit. */

/**
 * Phone <-> server WebSocket protocol, JSON text frames. Every message has a seq (per direction, from 0) and t_ms. Heartbeat 2 s; resume by session_id and last_seq (the server keeps a 10-minute ring of s2c messages).
 */
export type WsProtocol = C2S | S2C;
export type C2S = Hello | Frame | G1 | Utterance | Tap | JobReady | View | PlatePhoto | Ping | Timings;
export type S2C =
  | HelloOk
  | Outline
  | ZoneMsg
  | JobPreview
  | StepMsg
  | Checking
  | VerdictMsg
  | Sentence
  | Held
  | SafetyMsg
  | Rail
  | Notice
  | Replay
  | Pong
  | PlateRead;
export type RuleId = string;

/**
 * First message. With session_id and last_seq it resumes: the server re-sends every s2c message after last_seq.
 */
export interface Hello {
  type: "hello";
  seq: number;
  t_ms: number;
  session_id: string | null;
  last_seq: number | null;
  household?: string | null;
  client: "phone" | "quest" | "spectator";
  lang: string;
  /**
   * A recording to play; the literal golden plays the curated one (the hello is refused while none is curated).
   */
  replay_id?: string | null;
  /**
   * Set when a reminder opened the app: the session starts on this appliance
   */
  appliance_id?: string | null;
  /**
   * Set with appliance_id: the job to start
   */
  job_id?: string | null;
}
/**
 * Latest-wins: the phone drops frames rather than queue them. t_ms is the capture time.
 */
export interface Frame {
  type: "frame";
  seq: number;
  t_ms: number;
  frame_id: string;
  /**
   * JPEG, long edge <= 1024 px
   */
  jpeg_1024: string;
  /**
   * 64-bit perceptual hash, hex
   */
  phash: string;
  width?: number;
  height?: number;
}
/**
 * The phone fired G1 on the device; the server records it and moves the safety state.
 */
export interface G1 {
  type: "g1";
  seq: number;
  t_ms: number;
  zone_id: string;
  kind: "stop" | "pause";
  ttc_ms: number | null;
  dist_cm: number | null;
  frame_id: string;
  reason?: "ttc" | "distance" | "covered" | "hand_lost" | "stale_frame";
}
/**
 * Typed text, or a WAV (mono 16-bit, 16 or 24 kHz) for the server to transcribe.
 */
export interface Utterance {
  type: "utterance";
  seq: number;
  t_ms: number;
  utterance_id: string;
  text: string | null;
  audio_wav_b64?: string | null;
  lang: string;
}
export interface Tap {
  type: "tap";
  seq: number;
  t_ms: number;
  action:
    | "done"
    | "repeat"
    | "next"
    | "back"
    | "stop"
    | "yes"
    | "no"
    | "closer"
    | "share_note"
    | "confirm_gas_tap"
    | "confirm_plate"
    | "end"
    | "did_it";
  /**
   * The step the person saw when they tapped (did_it, done, repeat, next, back, closer): the server refuses a tap whose step is not the current one and says why (notice tap_not_current), so a tap never lands on a step the person has not seen. Absent: the current step (phones before 1.50), or a tap not about a step (end, stop, confirm_plate)
   */
  step_id?: string;
  /**
   * confirm_plate: JSON of the chosen options, {field name: [option per uncertain index]}. end: the person ends the session (the server closes it and records the outcome). did_it: after the camera could not check a step, the person says they did it; recorded as confirmed by the person, never as checked.
   */
  arg?: string | null;
}
/**
 * The person's 'I'm ready', answering job_preview: recorded as said by the person, never as checked. The server then sends the first step. It never clears a hold, a stop or a safety rule, and never changes a step's power, zones or camera
 */
export interface JobReady {
  type: "job_ready";
  seq: number;
  t_ms: number;
  job_id: string;
  /**
   * The ids of the preview's needs the person says they have ready; an id left out is one they go ahead without
   */
  ready: string[];
}
/**
 * The phone's view check result.
 */
export interface View {
  type: "view";
  seq: number;
  t_ms: number;
  ok: boolean;
  kind: "front" | "side";
  scale_cm_per_px: number | null;
}
export interface PlatePhoto {
  type: "plate_photo";
  seq: number;
  t_ms: number;
  photo_id: string;
  jpeg: string;
}
/**
 * Heartbeat, every 2 s, both ways. 5 s of silence means reconnect.
 */
export interface Ping {
  type: "ping";
  seq: number;
  t_ms: number;
}
/**
 * The device's own timings, for the acceptance numbers (landmarks ms per frame, frame to STOP): sent every 5 s while there are new ones and when the session ends. Numbers and frame ids only. The server records it and does nothing else with it.
 */
export interface Timings {
  type: "timings";
  seq: number;
  t_ms: number;
  /**
   * Hand landmarks on the device, ms per frame, for the frames since the last timings message
   */
  hands_ms: number[];
  /**
   * Each G1 stop since the last timings message: its frame (the id in its g1 message) and frame capture to the STOP painted, in ms
   */
  stops: {
    frame_id: string;
    ms: number;
  }[];
}
export interface HelloOk {
  type: "hello_ok";
  seq: number;
  t_ms: number;
  session_id: string;
  resumed: boolean;
  mode: "api" | "local" | "replay";
}
export interface Outline {
  type: "outline";
  seq: number;
  t_ms: number;
  part_id: string;
  kind: "target" | "do_not_touch" | "context";
  mask_svg: string;
  frame_id: string;
  conf: number | null;
  label?: string;
}
/**
 * A red zone for G1 to enforce on the device.
 */
export interface ZoneMsg {
  type: "zone";
  seq: number;
  t_ms: number;
  zone_id: string;
  rule_id: RuleId;
  ring_cm: number;
  ring_step?: boolean;
  mask_svg: string;
  frame_id: string;
  /**
   * The part's real width, from the card or rules; the view check uses it for scale
   */
  known_width_cm?: number;
  /**
   * Set when SAM measured a tool in the hand; the phone grows the ring by it
   */
  tool_len_cm?: number;
}
/**
 * The job up front, before it starts: what you need and what you are going to do. Sent after hello_ok when hello names a job (and again when the job restarts), before step 0; the first step follows the person's job_ready. Read only: it starts nothing, and a step's checks and safety gates run at the step as always
 */
export interface JobPreview {
  type: "job_preview";
  seq: number;
  t_ms: number;
  job_id: string;
  /**
   * The job's title, already passed by G0
   */
  title: string;
  /**
   * What to have ready, each with the manual's page and quote; [] when the card lists none
   */
  needs: NeedItem[];
  /**
   * Every step of the job, in order
   *
   * @minItems 1
   */
  steps: [PreviewStep, ...PreviewStep[]];
}
export interface NeedItem {
  id: string;
  /**
   * Already passed by G0
   */
  what: string;
  kind?: "product" | "tool" | "consumable";
  page: number;
  /**
   * The manual's words that call for it, verbatim
   */
  quote: string;
  /**
   * English translation of the quote, shown under it (the quote stays verbatim)
   */
  quote_en?: string;
}
export interface PreviewStep {
  step_id: string;
  /**
   * The step's own line from the card, already passed by G0 (what the step card says)
   */
  text: string;
  /**
   * Absent means manual. fixit: the phone shows its own fixed words, no page or quote
   */
  origin?: "manual" | "fixit";
  /**
   * Absent only with origin fixit
   */
  page?: number;
  /**
   * The manual's full words for the step, verbatim
   */
  quote?: string;
  quote_en?: string;
  power: "on" | "off" | "any";
  /**
   * The card step's camera mode (check when the card leaves it out)
   */
  camera: "check" | "find" | "none";
  warnings: PreviewWarning[];
}
export interface PreviewWarning {
  /**
   * zone: a red zone active on the step; power: the step's power precondition
   */
  kind: "zone" | "power";
  /**
   * Already passed by G0: a zone's manual quote, or the fixed power line from code
   */
  text: string;
  /**
   * With zone: the rule that governs it
   */
  rule_id?: string;
}
export interface StepMsg {
  type: "step";
  seq: number;
  t_ms: number;
  job_id: string;
  step_id: string;
  index: number;
  of: number;
  /**
   * Already passed by G0
   */
  text: string;
  /**
   * The manual's page; absent only with origin fixit
   */
  page?: number;
  /**
   * Absent means manual. fixit: the phone shows its own fixed words (our step, not from the manual), never text from the server, and no page
   */
  origin?: "manual" | "fixit";
  quote?: string;
  view: "front" | "side";
  power: "on" | "off" | "any";
  /**
   * English translation of the manual's quote, shown under it on the step card (the quote stays verbatim)
   */
  quote_en?: string;
  /**
   * Absent means check. find: outlines, no done check; none: no camera; the person confirms either. A power-off step still waits for the plug seen out
   */
  camera?: "check" | "find" | "none";
}
/**
 * Shown at once when a check starts.
 */
export interface Checking {
  type: "checking";
  seq: number;
  t_ms: number;
  step_id: string;
  check_id?: string;
}
export interface VerdictMsg {
  type: "verdict";
  seq: number;
  t_ms: number;
  verdict: Verdict;
}
export interface Verdict {
  step_id?: string;
  check_id?: string;
  attempt?: number;
  result: "done" | "not_done" | "cant_see";
  /**
   * The clauses that decided the result (missing or unclear ones for not_done / cant_see)
   */
  clause_ids: string[];
  witnesses: Witnesses;
  latency_ms: number;
  ask_for?: "closer" | "side" | "light" | null;
}
export interface Witnesses {
  /**
   * The judge's finding; absent only when by_person is true
   */
  judge?: "done" | "not_done" | "cant_see";
  /**
   * Which second witness ran: SAM on video or a frame, none, or (local mode beside Glimmer) track = a separate Glimmer locate on NOW_2 plus the phone's motion track, no SAM
   */
  sam: "video" | "frame" | "none" | "track";
  sam_result?: null | ("done" | "not_done" | "cant_see");
  /**
   * Absent means false. True only on a fixit step (ours, with nothing for a camera to check): the person ended it with a tap or by saying done, and no judge or witness ran. Never on a manual step
   */
  by_person?: true;
}
/**
 * Only sentences G0 passed. Drawn and spoken.
 */
export interface Sentence {
  type: "sentence";
  seq: number;
  t_ms: number;
  id: string;
  /**
   * The step this line is about: the step current when the guide turn began, or the step a fixed line belongs to. Absent: not tied to a step (the disclosure, a hazard script). The phone hides a line whose step is no longer the current one
   */
  step_id?: string;
  text: string;
}
/**
 * A sentence G0 held. The held text is never sent; the amber bar shows the rule id and line.
 */
export interface Held {
  type: "held";
  seq: number;
  t_ms: number;
  sentence_id?: string;
  /**
   * The step this line is about: the step current when the guide turn began, or the step a fixed line belongs to. Absent: not tied to a step (the disclosure, a hazard script). The phone hides a line whose step is no longer the current one
   */
  step_id?: string;
  rule_id: RuleId;
  line: string;
  /**
   * How many words were held
   */
  words: number;
}
export interface SafetyMsg {
  type: "safety";
  seq: number;
  t_ms: number;
  state: Ok | Hold | Stop;
}
export interface Ok {
  state: "OK";
}
export interface Hold {
  state: "HOLD";
  precondition:
    "side_view" | "power_off_seen" | "hand_visible" | "fresh_frame" | "zone_outlined" | "view_scale" | "user_confirm";
  rule_id?: null | string;
  detail?: string;
}
export interface Stop {
  state: "STOP";
  rule_id: string;
  evidence: string;
  /**
   * The country's fixed stop script, in the answer language
   */
  script: string;
}
export interface Rail {
  type: "rail";
  seq: number;
  t_ms: number;
  tool: string;
  args_short: string;
  result_short: string;
  ms: number;
}
export interface Notice {
  type: "notice";
  seq: number;
  t_ms: number;
  kind:
    | "slow_connection"
    | "hand_lost"
    | "paused"
    | "resumed"
    | "reconnected"
    | "local_mode"
    | "cap_reached"
    | "lookup_failed"
    | "ingest_failed"
    | "job_refused"
    | "no_recording"
    | "transcribe_failed"
    | "no_rules_for_country"
    | "did_it_offered"
    | "tap_not_current";
  text?: string;
}
/**
 * Sent first in a replay; the UI shows a permanent REPLAY badge.
 */
export interface Replay {
  type: "replay";
  seq: number;
  t_ms: number;
  badge: true;
  replay_id: string;
}
export interface Pong {
  type: "pong";
  seq: number;
  t_ms: number;
}
/**
 * What the plate read found, for the plate card ("Letter O or zero?": one tap answers with Tap confirm_plate). Lives only in the session; never in a card or the repo.
 */
export interface PlateRead {
  type: "plate_read";
  seq: number;
  t_ms: number;
  photo_id: string;
  /**
   * Lower-case slug, null if unread
   */
  brand: string | null;
  /**
   * The public model code, null if unread
   */
  model: string | null;
  fields: PlateField[];
}
export interface PlateField {
  name: string;
  value: string;
  uncertain: PlateUncertain[];
}
export interface PlateUncertain {
  /**
   * Character index in value
   */
  index: number;
  /**
   * @minItems 2
   */
  options: [string, string, ...string[]];
}

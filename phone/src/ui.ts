// The DOM half of the screen: home, step card, sentences, amber bar, notices, the stop, badges and the asks.
// Every server string goes in through textContent; nothing here builds HTML from a message.

import type { Held, JobPreview, Notice, Rail } from "../../contracts/ts/src/ws.ts";
import { etaText, FINAL, httpsUrl, manualLine, noticeText, quotesLine, readyFacts, refusedLines, STAGE_STEPS, STAGE_TEXT, stageRows, statusText, titleIn, wantsManual, type Appliance } from "./appliance.ts";
import { disclaimer, type Disclaimers } from "./disclaimer.ts";
import type { InstallKind } from "./install.ts";
import { LANGS } from "./lang.ts";
import { langOf, tr, trf, translateSkeleton } from "./words.ts";
import { accessOf, button, el, joinNodes, peopleKey, peopleNodes, type JoinState, type PeopleState, type Person } from "./people.ts";
import { connKey, connNodes, type ConnState } from "./connections.ts";
import { fbKey, fbNodes, fbText, type FbState } from "./feedback.ts";
import type { GuardState } from "./guard.ts";
import { question, resolved, type Choices } from "./plate.ts";
import { heldStep, stepFinished, type SessionState } from "./session.ts";
import { chevron, homeNodes, kindGlyph } from "./home.ts";
import type { DueItem } from "./schedule.ts";
import { sheetKey, sheetNodes, type SheetState } from "./sheet.ts";
import { applianceKey, applianceNodes, devicesNodes, makerPage, stateLine, unscheduledJobs, type CardView, type DevicesState, type ManualRef } from "./devices.ts";
import lockupSvg from "./brand/lockup.svg?raw";

export type { DueItem } from "./schedule.ts";
export { LAST_DONE_TEXT } from "./sheet.ts";

/** The amber bar's wording: plain words (a held count, the word count and the rule's own line) for everyone; the rule
 * id only in the debug view. It changes the words only; whether a sentence is held is decided on the server by G0 and
 * is not touched here. */
export type BarLayout = "rule" | "words";

export interface AddState {
  photoId: string | null;
  /** Brand and model from the plate read, as read. */
  label: string | null;
  appliance: Appliance | null;
  error: string | null;
  busy: boolean;
  nextPollAt: number;
  /** The checked card's jobs, to start one; null until the card is fetched. */
  jobs: { id: string; title: string }[] | null;
  /** Adding a manual to an appliance already in the home (a shared PDF), not adding the appliance. */
  existing?: boolean;
  /** The PDF on its way to the server: the share of it sent so far (0..1). */
  upload: { sent: number } | null;
  /** The manual's first page, fetched with the bearer: its thumb_url and the blob's object URL. */
  thumb: { url: string; src: string } | null;
  /** The server asked for the brand (422 brand_needed): the typed model alone could be a maker that needs it. */
  needBrand?: boolean;
}

export interface Landing {
  /** The reminder's key, for snoozing it. */
  key: string;
  /** Only a push reminder can come again; a calendar entry stays where it is. */
  canSnooze: boolean;
  /** After "Remind me tomorrow": done, or why it failed. */
  snooze: "busy" | "done" | "failed" | null;
  title: string;
  alias: string | null;
  why: string | null;
  minutes: number | null;
}

export interface LocalState {
  /** The phone's own G1 guard: a latched stop, or a pause while its cause lasts. */
  guard: GuardState;
  slow: boolean;
  testZone: boolean;
  viewMsg: string | null;
  plateChoices: Choices | null;
  /** A stop happened this session, so the technician note is offered. */
  noteOffered: boolean;
  /** The reminder landing: the job, its appliance, why it is due, while Start waits for a tap. */
  landing: Landing | null;
  live: boolean;
  /** The first-run screen's text (18+ and the AI line), shown until accepted; null once accepted or with no file. */
  firstRun: string | null;
  /** The fixed disclaimer texts, for the step card's citation and the terms. */
  disclaimers: Disclaimers | null;
  lang: string;
  /** Why joining failed, or why the phone must join again (fixed text). */
  joinError: string | null;
  /** The door is shut or capped: it offers the recorded run beside the reason. */
  doorReplay: boolean;
  /** GET /households/door said shut or capped: no consent is asked, only the reason and the replay. */
  doorShut: boolean;
  /** A spend cap reached (REST 429 or WS close 4429), in the phone's kind words; the home says it at the top. */
  cap: string | null;
  /** About > Open on another phone: the return link once made (single use, 15 minutes), or why it couldn't be. */
  another: { busy: boolean; url: string | null; until: string | null; copied: boolean; error: string | null } | null;
  /** The invite's join step: the consent line, its draft tag while unsigned, and whether it is ticked. */
  consent: { text: string; status: string | null; heading: string | null; body: string[]; ticked: boolean } | null;
  /** Adding an appliance: from a settled plate read (photoId) or a typed model; then its ingest, polled. */
  add: AddState | null;
  /** Hold to talk: recording now, and why the microphone didn't start. */
  talking: boolean;
  /** The needs on the job's preview the person has ticked as ready. */
  ticked: string[];
  /** Hands-free talk is on: the microphone stays open and each turn goes up by itself. */
  listening: boolean;
  /** This phone can listen hands-free (a microphone the page may open). */
  canListen: boolean;
  /** When the hold started (now()), for the seconds shown while recording. */
  talkSince: number | null;
  talkError: string | null;
  /** The server refused the session (WS close 1008) without saying why: the phone's own line. */
  refused: string | null;
  /** "Delete my household" asks for a second tap before it sends the DELETE. */
  deleteArmed: boolean;
  /** This app's address, and the household token this phone holds (null: no home yet). Never shown: Connections makes
   * Home Assistant and agents keys of their own. */
  ha: { origin: string; token: string | null };
  /** The cited manual page on screen (its image URL), or null. */
  pageUrl: string | null;
  /** A page of the home's own uploaded manual, in the app: fetched with the bearer, shown as an object URL. */
  viewer: { applianceId: string; page: number; pages: number; src: string | null; failed: boolean } | null;
  /** Your appliances (#devices) or one appliance's page (#devices/<id>); null when closed. */
  devices: DevicesState | null;
  /** The people in this home, once the server answers GET /household/people; null before, and on a server without
   * people, where the app stays as it was (no People screen, no assignee, every write offered). */
  folk: Person[] | null;
  /** People and alerts (#people); null when closed. */
  people: PeopleState | null;
  /** Joining a home from a people invite (#join/<code>): the name, then POST /household/join. */
  join: JoinState | null;
  /** Connections (#connections): the calendar address and the home's keys; null when closed. */
  conn: ConnState | null;
  /** The feedback form (#feedback), for a pilot home; null when closed. */
  fb: FbState | null;
  /** Settings (#settings), from the header: language, another phone, about, delete. */
  settings: boolean;
  /** Job done offers the form: a live session's end, in a pilot home, until it is opened or put off. */
  fbOffer: boolean;
  /** Each checked appliance's card, as far as the phone shows it, by appliance id. */
  cards: Record<string, CardView>;
  /** The live session's appliance and its manual, so the step's citation opens the maker's PDF at its page. */
  sessionManual: ManualRef | null;
  /** Local mode had no on-device voice for the language, so sentences are shown and not spoken (operator view only). */
  noVoice: boolean;
  /** ?debug=1: the tool rail and the rule ids are shown. */
  debug: boolean;
  /** The household's appliances and what is due, for the home screen; null until fetched (or with no household). */
  home: { appliances: Appliance[]; due: DueItem[] } | null;
  /** One job's sheet (#job/<appliance>/<job>), over home; null when closed. */
  sheet: SheetState | null;
  /** A step just passed: its number, shown as a tick until `until`; byYou when the person ended our own step (no check
   * ran: contracts 1.26 witnesses.by_person). */
  passed: { index: number; until: number; byYou: boolean } | null;
  /** The newest spoken line shows as a caption until `until`, then fades; keyed by the sentence's id. */
  caption: { id: string; until: number } | null;
  /** Every step of the job done: byYou of them ended by the person (our own steps), the rest checked by the camera. */
  complete: { of: number; byYou: number; nextDue: string | null; steps?: DoneStep[]; min?: number | null } | null;
  /** Keeping the app on the home screen, offered on Job done: what this browser allows, and how far it got. */
  install: { kind: InstallKind; phase: "offer" | "added" | "copied"; url: string } | null;
  /** A PDF shared to the app (Android's share sheet), held on the phone until the person says which appliance. */
  shared: { name: string } | null;
  /** Replay: the appliance and the job, from the recording's card ("Dishwasher · Clean the filters"). */
  replayTitle: string | null;
  /** The camera's header: the appliance as a kicker ("DISHWASHER · DEMO") over the job's title; null shows the
   * wordmark (adding an appliance, or a recording without a card). */
  head: { kicker: string | null; title: string } | null;
  /** A curated recording is there to watch from home. */
  golden: boolean;
  /** The camera, hand tracking or a replay could not start (fixed text). */
  camError: string | null;
  handsError: string | null;
  /** The hand-tracking banner shows until then (now()); after it, a small chip in the header says so. */
  handsUntil: number;
  /** The person tucked the text away to see the camera alone (a tap on the picture or the card's arrow). */
  clear: boolean;
  /** They have used it once, so the first-time hint stays away. */
  clearUsed: boolean;
  /** What came in while the text was tucked away: shown as a badge on the button that brings it back. */
  clearNews: "step" | "said" | null;
  /** Camera only chosen during a hold (the arrow), for that hold alone. */
  clearHold: boolean;
  /** The step the person tapped I did it on, until the next step comes (or until waitUntil, now()): the server may
   * look at earlier steps again first, and a second tap meanwhile must not land on a step they have not seen. */
  waitStep: string | null;
  waitUntil: number;
  /** Yes was tapped on a plug hold: the server is looking at the plug, until its answer comes (or this time, now()). */
  yesUntil: number;
  /** A tap_not_current line shows until this time (now()), or until the next step. */
  staleTapUntil: number;
  replayMissing: boolean;
  /** The emergency and service numbers the rules' texts use (rules/numbers.json), longest first. */
  numbers: PhoneNumber[];
  /** Each stop script's short lead, the rule's own line (rules/stops.json): shown first, the script under the calls. */
  stopLeads: Record<string, string>;
}

export interface PhoneNumber {
  /** Exactly as the texts write it ("0800-9009", "0800 111 999"). */
  display: string;
  tel: string;
}

export function localInitial(): LocalState {
  return {
    guard: { kind: "clear" },
    slow: false,
    testZone: false,
    viewMsg: null,
    plateChoices: null,
    noteOffered: false,
    landing: null,
    live: false,
    firstRun: null,
    disclaimers: null,
    lang: "en",
    joinError: null,
    doorReplay: false,
    doorShut: false,
    cap: null,
    another: null,
    consent: null,
    deleteArmed: false,
    refused: null,
    talking: false,
    listening: false,
    ticked: [],
    canListen: false,
    talkSince: null,
    talkError: null,
    add: null,
    ha: { origin: "", token: null },
    pageUrl: null,
    viewer: null,
    devices: null,
    folk: null,
    people: null,
    join: null,
    conn: null,
    fb: null,
    settings: false,
    fbOffer: false,
    cards: {},
    sessionManual: null,
    debug: false,
    home: null,
    sheet: null,
    passed: null,
    caption: null,
    complete: null,
    install: null,
    shared: null,
    replayTitle: null,
    head: null,
    golden: false,
    camError: null,
    handsError: null,
    handsUntil: 0,
    clear: false,
    clearUsed: false,
    clearNews: null,
    clearHold: false,
    waitStep: null,
    waitUntil: 0,
    yesUntil: 0,
    staleTapUntil: 0,
    replayMissing: false,
    numbers: [],
    stopLeads: {},
    noVoice: false,
  };
}

/** The detail of the hold the session raises when the user taps stop (app/session.py). */
export const STOPPED_BY_USER = "stopped by the user";

/** How long a spoken line stays up as a caption: time to read it, then a fade. */
export const CAPTION_FADE_MS = 600;
export function captionMs(text: string): number {
  return Math.min(9000, 2500 + 55 * text.length);
}

/** A gas rule id: "nl.gas.tap" and the like. */
export function isGasRule(ruleId: string | null | undefined): boolean {
  return ruleId?.split(".")[1] === "gas";
}

export const REFUSED_TEXT = "This session couldn't start. Go back and pick the appliance again.";
export const OFFLINE_TEXT = "Offline — reconnecting…";
export const CAMERA_TEXT = "Can't open the camera. Allow camera access in your browser's site settings, or type the model instead.";
export const HANDS_TEXT = "Hand tracking didn't start on this phone, so I can't watch your hands near a red outline. Keep clear of red outlines.";
/** Why keep it on the home screen. On an iPhone it is also the only way reminders arrive: Safari sends web
 * notifications only to an app opened from the home screen. */
export const INSTALL_WHY = {
  any: "Next time, open Fix-It Lens from your home screen like an app. Nothing to download from a store.",
  ios: "On an iPhone, reminders reach you only when Fix-It Lens is on your home screen. Add it there, then open it from the home screen like an app. Nothing to download from a store.",
} as const;

export const SNOOZED_TEXT = "I'll remind you again tomorrow.";
export const SNOOZE_FAILED_TEXT = "Couldn't move the reminder. It will come again as planned.";
/** A step of our own (1.26 origin fixit: putting back what a power-off step took away) has no page and no quote. The
 * card says so in fixed words where the page goes; its text is the server's, passed by G0 like any step's. */
/** Job done, in words that stay true: a step the person ended (our own, nothing for a camera to check) is never called
 * checked against the manual. */
/** A step as Job done lists it: what it said, its page, and whether the person ended it rather than a check. */
export interface DoneStep {
  n: number;
  text: string;
  page: number | null;
  byYou: boolean;
}

export function completeText(of: number, byYou: number, whose: "the" | "your", lang = "en"): string {
  const checked = of - byYou;
  if (langOf(lang) === "nl") {
    const m = whose === "the" ? "de handleiding" : "je handleiding";
    if (byYou <= 0) return of === 1 ? `Gecontroleerd met ${m}.` : `Alle ${of} stappen gecontroleerd met ${m}.`;
    if (byYou >= of) return of === 1 ? "Klaar. Je hebt de stap zelf bevestigd." : `Alle ${of} stappen klaar. Je hebt ze zelf bevestigd.`;
    return `Alle ${of} stappen klaar: ${checked} gecontroleerd met ${m}, ${byYou} door jou bevestigd.`;
  }
  if (byYou <= 0) return of === 1 ? `Checked against ${whose} manual.` : `All ${of} steps checked against ${whose} manual.`;
  if (byYou >= of) return of === 1 ? "Done. You confirmed the step yourself." : `All ${of} steps done. You confirmed them yourself.`;
  return `All ${of} steps done: ${checked} checked against ${whose} manual, ${byYou} confirmed by you.`;
}

/** What Share sends after a job: the count written out, never "all 1 steps"; a step the person confirmed is never
 * called checked. */
export function shareText(of: number, byYou = 0, lang = "en"): string {
  const checked = of - byYou;
  if (byYou > 0) {
    if (langOf(lang) === "nl") return checked <= 0 ? "Klaar met Fix-It Lens: door mij bevestigd." : `Klaar met Fix-It Lens: ${checked} van ${of} stappen gecontroleerd met de handleiding, de rest door mij bevestigd.`;
    return checked <= 0 ? "Done with Fix-It Lens: confirmed by me." : `Done with Fix-It Lens: ${checked} of ${of} steps checked against the manual, the rest confirmed by me.`;
  }
  if (langOf(lang) === "nl") return of === 1 ? "Klaar met Fix-It Lens: de stap is gecontroleerd met de handleiding." : `Klaar met Fix-It Lens: alle ${of} stappen gecontroleerd met de handleiding.`;
  return of === 1 ? "Done with Fix-It Lens: the step was checked against the manual." : `Done with Fix-It Lens: all ${of} steps checked against the manual.`;
}

export const OUR_STEP_TEXT = { en: "Our step, not from the manual", nl: "Onze stap, niet uit de handleiding" } as const;

export function ourStep(st: { page?: number; origin?: string }): boolean {
  return st.origin === "fixit" || typeof st.page !== "number";
}

export const REPLAY_MISSING_TEXT = "This replay isn't available. The link may be mistyped or the recording removed.";
export const NO_HOUSEHOLD_TEXT = "Open your invite link on this phone to add appliances.";

/** In a replay the controls are hidden, so a notice never names one of them. */
export const REPLAY_NOTICE_TEXT: Partial<Record<string, string>> = {
  lookup_failed: "I couldn't find the manual online.",
};

/** The notices that leave the person with no next step, and the way on each one offers in the card. */
const WAY_ON: Partial<Record<Notice["kind"], "watch" | "type">> = {
  cap_reached: "watch",
  no_rules_for_country: "watch",
  job_refused: "watch",
  lookup_failed: "type",
};

export const NOTICE_TEXT: Record<Notice["kind"], string> = {
  slow_connection: "Slow connection…",
  hand_lost: "I can't see your hand, so I've paused.",
  paused: "Paused.",
  resumed: "Carrying on.",
  reconnected: "Reconnected.",
  local_mode: "Local mode: running on this network.",
  cap_reached: "Today's limit is reached.",
  lookup_failed: "I couldn't find the manual online. Tap Type the model to check the code, or add a link to its PDF.",
  ingest_failed: "I kept the manual as plain pages, with no card.",
  job_refused: "The safety rules don't allow this job yet. The other jobs on this appliance are fine.",
  no_recording: "This part wasn't recorded.",
  transcribe_failed: "I didn't catch that. Say it again, or tap.",
  no_rules_for_country: "No safety rules for your country yet, so I won't guide repairs.",
  did_it_offered: "I still can't see it. If you've done it, tap I did it.",
  tap_not_current: "That tap was for the step before. You're on this step now.",
};

export const ASK_TEXT: Record<"closer" | "side" | "light", string> = {
  closer: "Move closer.",
  side: "Show it from the side.",
  light: "Add more light.",
};

export const HOLD_TEXT: Record<string, string> = {
  side_view: "Prop the phone side-on, beside the part.",
  power_off_seen: "Show me the plug out of the socket.",
  hand_visible: "I can't see your hand, so I've paused.",
  fresh_frame: "Hold the camera still.",
  zone_outlined: "Hold still: finding the part again.",
  view_scale: "Move so both outlines are in view.",
  user_confirm: "Is this the gas tap?",
};

/** The talk button while it records: how long, and how to send. */
export function talkText(since: number | null, now: number, lang = "en"): string {
  const sec = since === null ? 0 : Math.max(0, Math.floor((now - since) / 1000));
  const t = `${Math.floor(sec / 60)}:${String(sec % 60).padStart(2, "0")}`;
  return langOf(lang) === "nl" ? `Luistert ${t} · laat los om te sturen` : `Listening ${t} · release to send`;
}

/** An appliance's kind in Dutch, for an alias that is only its English kind. A name the home gave it stays as typed. */
const KIND_NL: Record<string, string> = {
  dishwasher: "vaatwasser",
  "washing machine": "wasmachine",
  washer: "wasmachine",
  dryer: "droger",
  "tumble dryer": "droger",
  "washer dryer": "was-droogcombinatie",
  boiler: "cv-ketel",
  fridge: "koelkast",
  freezer: "vriezer",
  "fridge freezer": "koel-vriescombinatie",
  oven: "oven",
  hob: "kookplaat",
  "cooker hood": "afzuigkap",
};

/** The replay header's line: "Dishwasher · Clean the filters", from whatever the card gave; in Dutch, a bare kind is
 * named in Dutch ("Vaatwasser · Zeven reinigen"). */
export function replayTitle(sum: { appliance_alias: string | null; job_title: string | null } | null | undefined, lang = "en"): string | null {
  const raw = sum?.appliance_alias ? (langOf(lang) === "nl" ? (KIND_NL[sum.appliance_alias.trim().toLowerCase()] ?? sum.appliance_alias) : sum.appliance_alias) : null;
  const alias = raw ? raw.charAt(0).toUpperCase() + raw.slice(1) : null;
  const parts = [alias, sum?.job_title ?? null].filter((x): x is string => !!x);
  return parts.length > 0 ? parts.join(" · ") : null;
}

/** The plate's field names as a person reads them. */
const PLATE_LABEL: Record<string, string> = {
  model: "Model",
  "E-Nr": "E-Nr (product number)",
  FD: "FD (production date)",
};

export const NO_VOICE_TEXT = "No on-device voice for this language: not spoken (local mode).";

/** What each tool did, in words, for the debug rail. */
const TOOL_VERB: Record<string, string> = {
  check_step: "Checked the step",
  locate: "Found the part",
  segment: "Outlined the part",
  outline: "Outlined the part",
  cite: "Checked the manual quote",
  quote_check: "Checked the manual quote",
  lookup_manual: "Looked in the manual",
  plate: "Read the model label",
  transcribe: "Listened",
  guide: "Worked out the next step",
  reasoning: "Thought it through",
  power: "Checked the power",
  voice_note: "Saved a voice note",
};

export function railText(r: Rail): string {
  const verb = TOOL_VERB[r.tool] ?? r.tool;
  const args = r.args_short ? ` (${r.args_short})` : "";
  const ms = r.ms > 0 ? ` · ${(r.ms / 1000).toFixed(1)} s` : "";
  return `${verb}${args}: ${r.result_short}${ms}`;
}

export function barText(h: Held, layout: BarLayout, count = 1, lang = "en"): string {
  if (layout === "rule") return `Held back · rule ${h.rule_id} · ${h.line}`;
  const nl = langOf(lang) === "nl";
  // The guide's own voice: it left a line out, and the rule's line, as the rules give it, says why. Nothing here claims
  // that every unsafe line is caught; it says only what happened to this one.
  const left = nl
    ? count === 1 ? "Ik heb een zin weggelaten:" : `Ik heb ${count} zinnen weggelaten:`
    : count === 1 ? "I left out a line:" : `I left out ${count} lines:`;
  return `${left} ${h.line}`;
}

/** A stop script's rows: split only where one of its own sentences ends (". " or "! " before a capital), so the rows
 * joined by spaces are the script again. A question stays with the answer that follows it. */
export function stopRows(script: string): string[] {
  return script === "" ? [] : script.split(/(?<=[.!])\s+(?=\p{Lu})/u);
}

/** The stop's line icon, from its rule's family (nl.gas.smell → gas): drawing only, it says nothing the text doesn't. */
export function stopIcon(ruleId: string | null | undefined): string {
  const family = ruleId?.split(".")[1] ?? "";
  return family === "gas" ? "gas" : family === "mains" ? "mains" : "hand";
}

const STOP_LINE = `viewBox="0 0 48 48" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"`;
const STOP_ICON: Record<string, string> = {
  gas: `<svg ${STOP_LINE}><path d="M24 6c2 7 10 11 10 21a10 10 0 0 1-20 0c0-5 3-8 5-11 1 3 2 5 4 6 0-6 1-11 1-16z"/><path d="M8 8l32 32"/></svg>`,
  mains: `<svg ${STOP_LINE}><path d="M17 8v8M31 8v8M12 16h24v8a12 12 0 0 1-24 0zM24 36v6"/><path d="M8 8l32 32"/></svg>`,
  hand: `<svg ${STOP_LINE}><path d="M16 26V12a3 3 0 0 1 6 0v10V9a3 3 0 0 1 6 0v13V12a3 3 0 0 1 6 0v16c0 8-5 14-12 14-5 0-8-3-11-8l-4-7a3 3 0 0 1 5-3l2 3"/></svg>`,
  call: `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 3h4l2 5-2.5 1.5a11 11 0 0 0 6 6L16 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 5a2 2 0 0 1 2-2z"/></svg>`,
};

/** The stop script on screen: the big STOP already says "Stop", so a leading "Stop." on its own is not repeated (speech
 * keeps it). A sentence that only begins with the word ("Stop using it.") is the rule's own line and stays whole. */
export function stopScript(script: string): string {
  return script.replace(/^\s*stop[.!]\s+/i, "");
}

/** The rules' numbers a text names, in the order it names them. */
export function phoneNumbers(text: string, numbers: PhoneNumber[]): PhoneNumber[] {
  return splitOn(text, numbers).flatMap((p) => (typeof p === "string" ? [] : [p])).filter((n, i, a) => a.findIndex((m) => m.tel === n.tel) === i);
}

/** The text cut at each number it names (longest match first), the numbers kept whole. */
function splitOn(text: string, numbers: PhoneNumber[]): (string | PhoneNumber)[] {
  const sorted = [...numbers].sort((a, b) => b.display.length - a.display.length);
  const out: (string | PhoneNumber)[] = [];
  let rest = text;
  for (;;) {
    let best: { at: number; n: PhoneNumber } | null = null;
    for (const n of sorted) {
      const re = new RegExp(`(?<![\\d])${n.display.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}(?![\\d])`);
      const m = re.exec(rest);
      if (m && (best === null || m.index < best.at)) best = { at: m.index, n };
    }
    if (!best) break;
    out.push(rest.slice(0, best.at), best.n);
    rest = rest.slice(best.at + best.n.display.length);
  }
  out.push(rest);
  return out;
}

// The lockup (brand/lockup.svg, as built), in the app's own ink and blue so it follows the theme and the camera.
const LOCKUP = lockupSvg
  .replace("<svg ", '<svg class="lockup" role="img" aria-label="Fix-It Lens" ')
  .replace(/<title>[^<]*<\/title>/, "")
  .replace(/#f1ece0/g, "currentColor")
  .replace(/#93a8ff/g, "var(--blue)");

export interface Els {
  root: HTMLElement;
  top: HTMLElement;
  card: HTMLElement;
  cardIndex: HTMLElement;
  cardText: HTMLElement;
  cardStep: HTMLElement;
  cardSaid: HTMLElement;
  listen: HTMLElement;
  preview: HTMLElement;
  tuck: HTMLElement;
  clearHint: HTMLElement;
  unclear: HTMLElement;
  unclearNews: HTMLElement;
  unclearText: HTMLElement;
  cardAnswer: HTMLElement;
  cardAnswerText: HTMLElement;
  cardQuote: HTMLElement;
  cardOrig: HTMLElement;
  cardQuoteOrig: HTMLElement;
  cardProgress: HTMLElement;
  moreRow: HTMLElement;
  cardPage: HTMLElement;
  cardPause: HTMLElement;
  showPage: HTMLElement;
  cardMaker: HTMLAnchorElement;
  pageBar: HTMLElement;
  pageCap: HTMLElement;
  pagePrev: HTMLButtonElement;
  pageNext: HTMLButtonElement;
  pageFailed: HTMLElement;
  devices: HTMLElement;
  devicesLink: HTMLElement;
  people: HTMLElement;
  peopleLink: HTMLElement;
  join: HTMLElement;
  conn: HTMLElement;
  connLink: HTMLElement;
  fb: HTMLElement;
  fbOffer: HTMLElement;
  pageView: HTMLElement;
  pageImg: HTMLImageElement;
  checking: HTMLElement;
  verdict: HTMLElement;
  cardState: HTMLElement;
  state: HTMLElement;
  done: HTMLElement;
  didIt: HTMLElement;
  passed: HTMLElement;
  complete: HTMLElement;
  completeText: HTMLElement;
  completeDue: HTMLElement;
  completeNext: HTMLElement;
  completeSteps: HTMLElement;
  install: HTMLElement;
  installPrompt: HTMLElement;
  installIos: HTMLElement;
  installOther: HTMLElement;
  installCopy: HTMLElement;
  installAdded: HTMLElement;
  installWhy: HTMLElement;
  installUrl: HTMLElement;
  installLater: HTMLElement;
  replayTitle: HTMLElement;
  camBack: HTMLElement;
  camHead: HTMLElement;
  camKicker: HTMLElement;
  camTitle: HTMLElement;
  camSteps: HTMLElement;
  camSegs: HTMLElement;
  camCount: HTMLElement;
  watch: HTMLElement;
  handsChip: HTMLElement;
  sentences: HTMLElement;
  bar: HTMLElement;
  rail: HTMLElement;
  saveTimings: HTMLElement;
  notice: HTMLElement;
  stop: HTMLElement;
  stopText: HTMLElement;
  stopCalls: HTMLElement;
  stopMore: HTMLElement;
  stopIcon: HTMLElement;
  stopWho: HTMLElement;
  stopWays: HTMLElement;
  stopAgain: HTMLButtonElement;
  stopBack: HTMLButtonElement;
  pause: HTMLElement;
  pauseText: HTMLElement;
  replay: HTMLElement;
  testZone: HTMLElement;
  gasTap: HTMLElement;
  confirm: HTMLElement;
  confirmBtn: HTMLElement;
  plate: HTMLElement;
  plateFields: HTMLElement;
  note: HTMLElement;
  landing: HTMLElement;
  landingTitle: HTMLElement;
  landingAlias: HTMLElement;
  landingWhy: HTMLElement;
  landingMin: HTMLElement;
  landingSnooze: HTMLButtonElement;
  landingSnoozed: HTMLElement;
  home: HTMLElement;
  homeList: HTMLElement;
  sheet: HTMLElement;
  homeIntro: HTMLElement;
  homeHow: HTMLElement;
  homeType: HTMLElement;
  hint: HTMLElement;
  missing: HTMLElement;
  controls: HTMLElement;
  typeModel: HTMLElement;
  settings: HTMLElement;
  tabs: HTMLElement;
  homeTab: HTMLElement;
  gear: HTMLElement;
  affiliation: HTMLElement;
  terms: HTMLElement;
  firstRun: HTMLElement;
  firstRunText: HTMLElement;
  firstRunMisread: HTMLElement;
  firstRunTerms: HTMLElement;
  firstRunGo: HTMLButtonElement;
  joinError: HTMLElement;
  doorWatch: HTMLElement;
  capLine: HTMLElement;
  consent: HTMLElement;
  consentStatus: HTMLElement;
  consentText: HTMLElement;
  consentBody: HTMLElement;
  firstRunTitle: HTMLElement;
  consentBox: HTMLInputElement;
  deleteHh: HTMLElement;
  another: HTMLElement;
  anotherMake: HTMLButtonElement;
  anotherUrl: HTMLElement;
  anotherCopy: HTMLElement;
  anotherNote: HTMLElement;
  lang: HTMLSelectElement;
  talk: HTMLElement;
  add: HTMLElement;
  addHead: HTMLElement;
  addLabel: HTMLElement;
  addModelRow: HTMLElement;
  addModel: HTMLInputElement;
  addNameRow: HTMLElement;
  addName: HTMLInputElement;
  addBrandRow: HTMLElement;
  addBrand: HTMLInputElement;
  addGo: HTMLElement;
  addStatus: HTMLElement;
  addStages: HTMLElement;
  addNotice: HTMLElement;
  addMaker: HTMLAnchorElement;
  addPdfRow: HTMLElement;
  addPdf: HTMLInputElement;
  shared: HTMLElement;
  sharedAsk: HTMLElement;
  sharedName: HTMLElement;
  sharedList: HTMLElement;
  addManualRow: HTMLElement;
  addFile: HTMLInputElement;
  addWait: HTMLElement;
  addMeter: HTMLElement;
  addEta: HTMLElement;
  addManual: HTMLElement;
  addThumb: HTMLImageElement;
  addTitle: HTMLElement;
  addPages: HTMLElement;
  addQuotes: HTMLElement;
  addReadyHead: HTMLElement;
  addReady: HTMLElement;
  addRefused: HTMLElement;
  addStart: HTMLElement;
  addJobs: HTMLElement;
  start: HTMLElement;
}

// The first run's three promises, each with a small drawn mark in the plate's line.
const PROMISE_ICON = {
  manual: `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M4 5.5c2.5-1 5-1 8 1 3-2 5.5-2 8-1V19c-2.5-1-5-1-8 1-3-2-5.5-2-8-1z"/><path d="M12 6.5V20"/></svg>`,
  part: `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M3 8V3h5M16 3h5v5M21 16v5h-5M8 21H3v-5"/><rect x="7.5" y="9" width="9" height="6" rx="2" stroke="var(--blue)" stroke-width="2.2"/></svg>`,
  stop: `<svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"><path d="M8.3 3h7.4L21 8.3v7.4L15.7 21H8.3L3 15.7V8.3z" stroke="var(--red)"/><path d="M8 12h8" stroke="var(--red)" stroke-width="2.2" stroke-linecap="round"/></svg>`,
};

const HOW_FIG = `<svg class="how-fig" viewBox="0 0 320 214" aria-hidden="true" fill="none" stroke-linecap="round" stroke-linejoin="round">
  <g stroke="var(--fg)" stroke-width="2.5" stroke-linecap="square" stroke-linejoin="miter"><path d="M6 58V6h52M314 156v52h-52"/></g>
  <rect x="98" y="12" width="124" height="190" rx="16" stroke="var(--fg)" stroke-width="2"/>
  <rect x="106" y="26" width="108" height="164" rx="5" stroke="var(--faint)" stroke-width="1"/>
  <g stroke="var(--fg)" stroke-width="1.5"><rect x="116" y="36" width="88" height="104" rx="3"/><path d="M116 52h88"/><circle cx="192" cy="44" r="3.5"/><path d="M124 44h26"/><circle cx="160" cy="92" r="27"/><circle cx="160" cy="92" r="19" stroke="var(--faint)"/></g>
  <rect x="121" y="121" width="24" height="13" rx="4" stroke="#fff" stroke-width="5"/>
  <rect x="121" y="121" width="24" height="13" rx="4" stroke="var(--accent)" stroke-width="2.5" fill="color-mix(in srgb, var(--accent) 16%, transparent)"/>
  <path d="M116 124v-8h8M150 131v8h-8" stroke="var(--fg)" stroke-width="1.5" stroke-linecap="square" stroke-linejoin="miter"/>
  <rect x="110" y="148" width="100" height="36" rx="3" fill="var(--surface-2)" stroke="var(--line)"/>
  <g stroke-width="2.5"><path d="M117 157h22" stroke="var(--accent)"/><path d="M117 166h62M117 174h44" stroke="var(--fg)"/></g>
  <circle cx="198" cy="170" r="7" fill="var(--green)"/><path d="M194.5 170l2.5 2.5 4.5-5" stroke="var(--done-fg)" stroke-width="1.8"/>
  <g stroke="var(--faint)" stroke-width="1" stroke-dasharray="3 3"><path d="M46 166h62M46 128h72M274 170h-66"/></g>
  <g font-family="var(--mono)" font-size="11" font-weight="500" text-anchor="middle"><rect x="26" y="156" width="20" height="20" fill="var(--accent)"/><text x="36" y="169.8" fill="var(--accent-fg)">1</text><rect x="26" y="118" width="20" height="20" fill="var(--accent)"/><text x="36" y="131.8" fill="var(--accent-fg)">2</text><rect x="274" y="160" width="20" height="20" fill="var(--accent)"/><text x="284" y="173.8" fill="var(--accent-fg)">3</text></g>
</svg>`;

/** Settings, opened from the header. */
export const SETTINGS_HASH = "#settings";

export const SKELETON = `
<header id="top" class="top">
  <button id="cam-back" class="cam-back" data-act="finish" aria-label="End the job" data-t-aria hidden><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg><span data-t>End</span></button>
  <span id="cam-head" class="cam-head" hidden><span id="cam-kicker" class="cam-kicker"></span><span id="cam-title" class="cam-title"></span></span>
  <span id="cam-steps" class="cam-steps" aria-hidden="true" hidden><span id="cam-segs" class="cam-segs"></span><span id="cam-count" class="cam-count"></span></span>
  <span class="brand"><span class="names">${LOCKUP}<span id="replay-title" class="replay-title" hidden></span></span></span>
  <span class="chips"><span id="replay-badge" class="chip replay" data-t hidden>Recorded demo</span><span id="test-zone" class="chip test" data-t hidden>TEST ZONE</span><button id="gear" class="gear" data-act="settings" aria-label="Settings" data-t-aria hidden><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M4 7h9M17 7h3M4 17h3M11 17h9"/><circle cx="15" cy="7" r="2"/><circle cx="9" cy="17" r="2"/></svg></button><button id="hands-chip" class="chip-button" data-act="hands-info" aria-label="Hand tracking is off. Tap for details." data-t-aria hidden><span class="chip warn" data-t>✋ Hand tracking off</span></button></span>
</header>
<div class="status" data-t-lang>
  <div id="notice" class="notice" role="status" hidden></div>
  <div id="bar" class="bar amber" role="status" hidden></div>
</div>
<div id="sentences" class="caption" aria-live="polite"></div>
<section id="preview" class="panel preview" data-t-lang hidden></section>
<section id="first-run" class="panel first-run" data-t-lang hidden>
  <h1 id="first-run-title">Before you start</h1>
  <ul class="promise">
    <li>${PROMISE_ICON.manual}<span data-t>Reads your appliance's own manual</span></li>
    <li>${PROMISE_ICON.part}<span data-t>Shows you the part, one step at a time</span></li>
    <li>${PROMISE_ICON.stop}<span data-t>Stops at gas, mains and anything risky, and says who to call</span></li>
  </ul>
  <p id="first-run-misread" class="sub misread" hidden></p>
  <span id="consent-status" class="tag" hidden></span>
  <ul id="consent-body" class="consent-body"></ul>
  <details id="first-run-more" class="terms-more">
    <summary data-t>The full terms</summary>
    <div class="terms-sheet">
      <ul id="first-run-text" class="facts"></ul>
      <p id="first-run-terms" class="sub"></p>
      <button data-act="terms-close" data-t>Close</button>
    </div>
  </details>
  <p id="join-error" class="error" role="alert" hidden></p>
  <a id="door-watch" class="button watch" href="?replay=golden" data-t hidden>Watch a recorded repair</a>
  <div class="first-foot">
    <div id="consent" class="consent" hidden>
      <label class="consent-row"><input id="consent-box" type="checkbox" data-act="consent"><span id="consent-text"></span></label>
    </div>
    <button id="first-run-go" data-act="accept-first-run" class="ok big">I'm 18 or older: continue</button>
  </div>
</section>
<section id="landing" class="panel landing" hidden>
  <p class="kicker" data-t>Reminder</p>
  <h1 id="landing-title"></h1>
  <p id="landing-alias" class="sub"></p>
  <p id="landing-why"></p>
  <p id="landing-min" class="sub"></p>
  <div class="row"><button data-act="start" class="ok big" data-t>Start</button><button data-act="landing-dismiss" data-t>Not now</button></div>
  <button id="landing-snooze" data-act="landing-snooze" class="link" data-t>Remind me tomorrow</button>
  <button id="landing-sheet" data-act="landing-sheet" class="link" data-t>Schedule and history</button>
  <p id="landing-snoozed" class="sub" role="status" hidden></p>
</section>
<section id="home" class="panel home" hidden>
  <p id="cap-line" class="notice-line cap-line" role="status" hidden></p>
  <div id="shared" class="shared" hidden>
    <h2 data-t>A PDF shared with Fix-It Lens</h2>
    <p id="shared-name" class="shared-name"></p>
    <p id="shared-ask"></p>
    <div id="shared-list" class="jobs"></div>
    <button data-act="shared-new" data-t>A new appliance</button>
    <button data-act="shared-drop" class="link" data-t>Not now</button>
  </div>
  <h1 id="home-intro" class="home-intro" data-t>Point it at an appliance</h1>
  <div id="home-how" class="how">
    <figure class="how-plate">${HOW_FIG}</figure>
    <ol class="callouts"><li data-t>Each step, with its page in your manual</li><li data-t>The part, outlined on your camera</li><li data-t>Checked before you go on</li></ol>
  </div>
  <div id="home-list" class="list"></div>
  <button id="start" data-act="start" class="ok big">Point at an appliance</button>
  <a id="watch" class="button watch" href="?replay=golden" data-t hidden>Watch a recorded repair</a>
  <button id="home-type" data-act="type-model" class="text-act home-type" data-t hidden>Type the model code instead</button>
</section>
<section id="job-sheet" class="panel job-sheet" hidden></section>
<section id="devices" class="panel devices" hidden></section>
<section id="people" class="panel people-panel" hidden></section>
<button id="conn-link" data-act="connections" class="nav-row" data-t hidden>Connections</button>
<section id="join" class="panel join" hidden></section>
<section id="connections" class="panel people-panel" hidden></section>
<section id="feedback" class="panel people-panel feedback" hidden></section>
<section id="missing" class="panel" hidden>
  <h1 data-t>Replay not found</h1>
  <p></p>
  <a class="button" href="./" data-t>Go to the start</a>
</section>
<div class="sheet">
  <p id="hint" class="hint" data-t-lang hidden></p>
  <p id="clear-hint" class="clear-hint" data-t hidden>Tap the picture to show only the camera</p>
  <button id="unclear" data-act="unclear" class="unclear" data-t-lang hidden><span id="unclear-news" class="unclear-news" hidden></span><span id="unclear-text"></span></button>
  <ol id="rail" class="rail" hidden></ol>
  <button id="save-timings" data-act="save-timings" class="link" data-t hidden>Save the timings</button>
  <div id="pause" class="pause" data-t-lang hidden><p id="pause-text"></p></div>
  <div id="passed" class="passed" data-t-lang hidden></div>
  <section id="card" class="card" data-t-lang hidden>
    <div class="card-progress" aria-hidden="true"><span id="card-progress"></span></div>
    <div class="card-head"><span id="card-index" class="step-no"></span><button id="tuck" data-act="tuck" class="tuck" aria-label="Camera only" data-t-aria><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg></button><span class="cite"><span id="card-page"></span><button id="show-page" data-act="show-page" class="page-chip" hidden></button><a id="card-maker" class="page-chip" target="_blank" rel="noopener noreferrer" hidden></a></span></div>
    <div id="card-pause" class="card-pause" hidden></div>
    <p id="card-text" class="card-text"></p>
    <p id="card-said" class="card-said" aria-live="polite"></p>
    <button id="card-answer" data-act="answer-clear" class="card-answer" hidden><span class="answer-k" data-t>Answer</span><span id="card-answer-text"></span></button>
    <details class="from-manual"><summary data-t>From the manual</summary><p id="card-step" class="card-step"></p><blockquote><p id="card-quote" translate="no"></p></blockquote><details id="card-orig" class="orig"><summary data-t>The manual's own words</summary><p id="card-quote-orig" translate="no"></p></details></details>
    <div id="checking" class="checking" hidden><span class="spinner"></span><span data-t>Checking…</span></div>
    <p id="verdict" class="verdict" hidden></p>
    <div id="card-state" class="state" hidden></div>
  </section>
  <section id="state" class="card state" data-t-lang hidden></section>
  <section id="complete" class="panel complete" data-t-lang hidden>
    <svg class="done-mark" viewBox="0 0 64 64" aria-hidden="true" fill="none" stroke-linecap="square"><path class="dm-a" d="M4 22V4h18"/><path class="dm-b" d="M60 42v18H42"/><path class="dm-tick" pathLength="1" d="M18 33l9 9 18-20"/></svg>
    <h1 data-t>Job done</h1>
    <p id="complete-text" class="sub"></p>
    <ol id="complete-steps" class="proof"></ol>
    <div id="complete-next" class="next-time" hidden><span class="kicker" data-t>Next time</span><strong id="complete-due"></strong></div>
    <button data-act="finish" class="ok big" data-t>Finish</button><button data-act="share-done" class="text-act" data-t>Share what was done</button>
    <div id="fb-offer" class="install fb-offer" hidden></div>
    <div id="install" class="install" hidden>
      <h2 data-t>Keep it on your home screen</h2>
      <p id="install-why" class="sub"></p>
      <button id="install-prompt" data-act="install" data-t hidden>Add to home screen</button>
      <ol id="install-ios" class="install-steps" hidden>
        <li><svg class="install-glyph" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3v12"/><path d="M8 7l4-4 4 4"/><path d="M8 11H6v10h12V11h-2"/></svg><span data-t>Tap <b>Share</b> in Safari. On newer iPhones, tap <b>⋯</b> first.</span></li>
        <li><svg class="install-glyph" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><rect x="4" y="4" width="16" height="16" rx="3"/><path d="M12 8v8M8 12h8"/></svg><span data-t>Tap <b>Add to Home Screen</b>, then <b>Add</b>.</span></li>
      </ol>
      <div id="install-other" class="install-other" hidden>
        <p data-t>To add it to your Home Screen, open this page in Safari.</p>
        <p id="install-url" class="install-url"></p>
        <button id="install-copy" data-act="install-copy">Copy the link</button>
      </div>
      <p id="install-added" data-t hidden>Added. Fix-It Lens is on your home screen.</p>
      <button id="install-later" data-act="install-later" class="link" data-t>Not now</button>
    </div>
  </section>
  <div id="gas-tap" class="ask" data-t-lang hidden>
    <p data-t>Is the red outline on the gas tap?</p><p class="sub" data-t>Don't touch it.</p><div class="gas-acts"><button data-tap="confirm_gas_tap" class="yes-gas" data-t>Yes, that's it</button><button data-tap="no" data-t>No</button></div>
  </div>
  <div id="confirm" class="ask" data-t-lang hidden><button id="confirm-btn" data-tap="yes" class="ok big"></button></div>
  <section id="plate" class="ask plate" data-t-lang hidden>
    <p data-t>From the model label</p><div id="plate-fields"></div>
    <div class="row"><button data-act="confirm-plate" class="ok" data-t>✓ That's right</button><button data-act="plate-retake" class="plate-no" data-t>Not this one</button></div>
  </section>
  <section id="add" class="ask add" hidden>
    <button type="button" class="page-back" data-act="add-close"><svg class="back-mark" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"/></svg><span data-t>Back</span></button>
    <p id="add-head" class="add-head">Add this appliance</p>
    <p id="add-label" class="sub"></p>
    <label id="add-model-row" hidden><span data-t>Model code</span> <input id="add-model" enterkeyhint="go" maxlength="60" autocomplete="off" autocapitalize="characters" placeholder="e.g. SMV4HAX40N" data-t-ph></label>
    <label id="add-brand-row" hidden><span data-t>Brand</span> <input id="add-brand" enterkeyhint="go" maxlength="40" autocomplete="off" placeholder="as on the model label" data-t-ph></label>
    <label id="add-name-row"><span data-t>Name it (optional)</span> <input id="add-name" enterkeyhint="go" maxlength="40" placeholder="dishwasher" data-t-ph></label>
    <p id="add-status" class="status-line"></p>
    <ol id="add-stages" class="stages" hidden></ol>
    <div id="add-wait" class="add-wait" hidden>
      <div id="add-meter" class="meter" role="progressbar" aria-label="Progress" data-t-aria aria-valuemin="0" aria-valuemax="100" hidden><span></span></div>
      <p id="add-eta" class="add-eta"></p>
      <div id="add-manual" class="add-manual" hidden><img id="add-thumb" alt="The manual's first page" data-t-aria hidden><div><p id="add-title" class="add-title"></p><p id="add-pages" class="sub"></p></div></div>
      <p id="add-quotes" class="sub"></p>
      <h2 id="add-ready-head" data-t hidden>Jobs ready so far</h2>
      <ul id="add-ready" class="ready"></ul>
      <ul id="add-refused" class="refused"></ul>
    </div>
    <p id="add-notice" class="error"></p>
    <div id="add-pdf-row" hidden><label><span data-t>Manual link</span> <input id="add-pdf" enterkeyhint="go" type="url" placeholder="https://"></label><button data-act="add-pdf" data-t>Use this link</button></div>
    <div id="add-manual-row" class="row add-manual-row" hidden><label id="add-file-pick" class="button file-pick"><span data-t>Add the PDF</span><input id="add-file" type="file" accept="application/pdf,.pdf" data-act="add-file"></label><a id="add-maker" class="button" target="_blank" rel="noopener noreferrer" data-t hidden>Open the maker's page</a></div>
    <div id="add-jobs" class="jobs"></div>
    <div class="row add-acts"><button id="add-go" data-act="add-go" class="ok" data-t>Add</button><button id="add-start" data-act="add-start" class="ok" data-t hidden>Start</button><button data-act="add-close" class="dismiss add-close" data-t>Close</button></div>
  </section>
  <nav id="controls" class="controls" data-t-lang hidden>
    <div class="row act-row"><button id="talk" data-act="talk" class="talk">Hold to talk</button><button id="listen" data-act="listen" class="listen" aria-pressed="false" hidden></button><button id="done" data-tap="done" class="ok done" data-t hidden>Done</button></div>
    <button id="did-it" data-tap="did_it" class="did-it" data-t hidden>I did it — continue</button>
    <div id="more-row" class="row">
      <label class="plate-btn button"><span data-t>Photo of the label</span><input data-act="plate-photo" type="file" accept="image/*" capture="environment" hidden></label>
      <button id="type-model" data-act="type-model" data-t>Type the model</button>
    </div>
    <button id="note" data-act="note" data-t hidden>Send a note to a technician</button>
  </nav>
</div>
<section id="settings" class="panel settings" data-t-lang hidden>
  <h1 data-t>Settings</h1>
  <label class="lang-pick"><span data-t>Language</span> <select id="lang" data-act="lang" autocomplete="off">${LANGS.map(([v, name]) => `<option value="${v}" lang="${v}">${name}</option>`).join("")}</select></label>
  <div id="another" class="another" hidden>
    <h3 data-t>Open on another phone</h3>
    <p class="sub" data-t>Make a link, then open it on the other phone. It works once, within 15 minutes, and opens this household there too.</p>
    <button id="another-make" data-act="another-make">Make a link</button>
    <p id="another-url" class="another-url" hidden></p>
    <button id="another-copy" data-act="another-copy" data-t hidden>Copy the link</button>
    <p id="another-note" class="sub" role="status" hidden></p>
  </div>
  <h2 data-t>About</h2>
  <p><span data-t>Fix-It Lens is a prototype.</span> <span id="affiliation" data-t>Not affiliated with Bosch, Samsung, the boiler makers or Meta.</span></p>
  <p id="terms" class="sub"></p>
  <button id="delete-household" data-act="delete-household" class="danger" hidden></button>
</section>
<nav id="tabs" class="tabs" data-t-lang hidden>
  <button id="home-tab" class="tab" data-act="home-tab"><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M4 11l8-7 8 7"/><path d="M6 9.5V20h12V9.5"/><path d="M10 20v-5.5h4V20"/></svg><span data-t>Home</span></button>
  <button id="devices-link" class="tab" data-act="devices"><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><rect x="5" y="3" width="14" height="18" rx="2"/><circle cx="12" cy="13" r="4"/><path d="M8 6.5h3"/></svg><span data-t>Appliances</span></button>
  <button id="people-link" class="tab" data-act="people" hidden><svg viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><circle cx="9" cy="8" r="3"/><path d="M3.5 19.5c.6-3.2 2.8-5 5.5-5s4.9 1.8 5.5 5"/><circle cx="16.5" cy="9" r="2.5"/><path d="M16 14.3c2.4-.2 4 1.3 4.6 4.2"/></svg><span data-t>Household</span></button>
</nav>
<section id="page-view" class="page-view" hidden>
  <div id="page-bar" class="page-bar" data-t-lang hidden><button id="page-prev" data-act="page-prev" aria-label="Previous page" data-t-aria>‹</button><p id="page-cap" role="status"></p><button id="page-next" data-act="page-next" aria-label="Next page" data-t-aria>›</button></div>
  <p id="page-failed" class="sub" data-t hidden>Couldn't load that page. Check the connection and try again.</p>
  <img id="page-img" alt="The manual page" data-t-aria><button data-act="close-page" class="ok" data-t>Close</button>
</section>
<div id="stop" class="stop" data-t-lang hidden><span id="stop-icon" class="stop-icon" aria-hidden="true"></span><p class="stop-word">STOP</p><p id="stop-text"></p><div id="stop-calls" class="calls"></div><ol id="stop-more" class="stop-steps"></ol><p id="stop-who" class="stop-who" hidden></p><div id="stop-ways" class="stop-ways" hidden><button id="stop-again" data-act="replay-again" class="stop-again" hidden></button><button id="stop-back" data-act="finish" class="stop-back"></button></div></div>
`;

export function mount(root: HTMLElement): Els {
  root.innerHTML = SKELETON; // fixed markup only
  const q = (id: string): HTMLElement => {
    const el = root.querySelector<HTMLElement>(`#${id}`);
    if (!el) throw new Error(`missing #${id}`);
    return el;
  };
  return {
    root,
    top: q("top"),
    card: q("card"),
    cardIndex: q("card-index"),
    cardText: q("card-text"),
    cardStep: q("card-step"),
    cardSaid: q("card-said"),
    listen: q("listen"),
    preview: q("preview"),
    tuck: q("tuck"),
    clearHint: q("clear-hint"),
    unclear: q("unclear"),
    unclearNews: q("unclear-news"),
    unclearText: q("unclear-text"),
    cardAnswer: q("card-answer"),
    cardAnswerText: q("card-answer-text"),
    cardQuote: q("card-quote"),
    cardOrig: q("card-orig"),
    cardQuoteOrig: q("card-quote-orig"),
    cardProgress: q("card-progress"),
    moreRow: q("more-row"),
    cardPage: q("card-page"),
    cardPause: q("card-pause"),
    showPage: q("show-page"),
    cardMaker: q("card-maker") as HTMLAnchorElement,
    pageBar: q("page-bar"),
    pageCap: q("page-cap"),
    pagePrev: q("page-prev") as HTMLButtonElement,
    pageNext: q("page-next") as HTMLButtonElement,
    pageFailed: q("page-failed"),
    devices: q("devices"),
    devicesLink: q("devices-link"),
    people: q("people"),
    peopleLink: q("people-link"),
    join: q("join"),
    conn: q("connections"),
    connLink: q("conn-link"),
    fb: q("feedback"),
    fbOffer: q("fb-offer"),
    pageView: q("page-view"),
    pageImg: q("page-img") as HTMLImageElement,
    checking: q("checking"),
    verdict: q("verdict"),
    cardState: q("card-state"),
    state: q("state"),
    done: q("done"),
    didIt: q("did-it"),
    passed: q("passed"),
    complete: q("complete"),
    completeText: q("complete-text"),
    completeDue: q("complete-due"),
    completeNext: q("complete-next"),
    completeSteps: q("complete-steps"),
    install: q("install"),
    installPrompt: q("install-prompt"),
    installIos: q("install-ios"),
    installOther: q("install-other"),
    installCopy: q("install-copy"),
    installAdded: q("install-added"),
    installWhy: q("install-why"),
    installUrl: q("install-url"),
    installLater: q("install-later"),
    replayTitle: q("replay-title"),
    camBack: q("cam-back"),
    camHead: q("cam-head"),
    camKicker: q("cam-kicker"),
    camTitle: q("cam-title"),
    camSteps: q("cam-steps"),
    camSegs: q("cam-segs"),
    camCount: q("cam-count"),
    watch: q("watch"),
    handsChip: q("hands-chip"),
    sentences: q("sentences"),
    bar: q("bar"),
    rail: q("rail"),
    saveTimings: q("save-timings"),
    notice: q("notice"),
    stop: q("stop"),
    stopText: q("stop-text"),
    stopCalls: q("stop-calls"),
    stopMore: q("stop-more"),
    stopIcon: q("stop-icon"),
    stopWho: q("stop-who"),
    stopWays: q("stop-ways"),
    stopAgain: q("stop-again") as HTMLButtonElement,
    stopBack: q("stop-back") as HTMLButtonElement,
    pause: q("pause"),
    pauseText: q("pause-text"),
    replay: q("replay-badge"),
    testZone: q("test-zone"),
    gasTap: q("gas-tap"),
    confirm: q("confirm"),
    confirmBtn: q("confirm-btn"),
    plate: q("plate"),
    plateFields: q("plate-fields"),
    note: q("note"),
    landing: q("landing"),
    landingTitle: q("landing-title"),
    landingAlias: q("landing-alias"),
    landingWhy: q("landing-why"),
    landingMin: q("landing-min"),
    landingSnooze: q("landing-snooze") as HTMLButtonElement,
    landingSnoozed: q("landing-snoozed"),
    home: q("home"),
    homeList: q("home-list"),
    sheet: q("job-sheet"),
    homeIntro: q("home-intro"),
    homeHow: q("home-how"),
    homeType: q("home-type"),
    hint: q("hint"),
    missing: q("missing"),
    controls: q("controls"),
    typeModel: q("type-model"),
    settings: q("settings"),
    tabs: q("tabs"),
    homeTab: q("home-tab"),
    gear: q("gear"),
    affiliation: q("affiliation"),
    terms: q("terms"),
    firstRun: q("first-run"),
    firstRunText: q("first-run-text"),
    firstRunMisread: q("first-run-misread"),
    firstRunTerms: q("first-run-terms"),
    firstRunGo: q("first-run-go") as HTMLButtonElement,
    joinError: q("join-error"),
    doorWatch: q("door-watch"),
    capLine: q("cap-line"),
    consent: q("consent"),
    consentStatus: q("consent-status"),
    consentText: q("consent-text"),
    consentBody: q("consent-body"),
    firstRunTitle: q("first-run-title"),
    consentBox: q("consent-box") as HTMLInputElement,
    deleteHh: q("delete-household"),
    another: q("another"),
    anotherMake: q("another-make") as HTMLButtonElement,
    anotherUrl: q("another-url"),
    anotherCopy: q("another-copy"),
    anotherNote: q("another-note"),
    lang: q("lang") as HTMLSelectElement,
    talk: q("talk"),
    add: q("add"),
    addHead: q("add-head"),
    addLabel: q("add-label"),
    addModelRow: q("add-model-row"),
    addModel: q("add-model") as HTMLInputElement,
    addNameRow: q("add-name-row"),
    addName: q("add-name") as HTMLInputElement,
    addBrandRow: q("add-brand-row"),
    addBrand: q("add-brand") as HTMLInputElement,
    addGo: q("add-go"),
    addStatus: q("add-status"),
    addStages: q("add-stages"),
    addNotice: q("add-notice"),
    addMaker: q("add-maker") as HTMLAnchorElement,
    addPdfRow: q("add-pdf-row"),
    addPdf: q("add-pdf") as HTMLInputElement,
    shared: q("shared"),
    sharedAsk: q("shared-ask"),
    sharedName: q("shared-name"),
    sharedList: q("shared-list"),
    addManualRow: q("add-manual-row"),
    addFile: q("add-file") as HTMLInputElement,
    addWait: q("add-wait"),
    addMeter: q("add-meter"),
    addEta: q("add-eta"),
    addManual: q("add-manual"),
    addThumb: q("add-thumb") as HTMLImageElement,
    addTitle: q("add-title"),
    addPages: q("add-pages"),
    addQuotes: q("add-quotes"),
    addReadyHead: q("add-ready-head"),
    addReady: q("add-ready"),
    addRefused: q("add-refused"),
    addStart: q("add-start"),
    addJobs: q("add-jobs"),
    start: q("start"),
  };
}

function set(el: HTMLElement, text: string | null): void {
  el.hidden = text === null || text === "";
  if (el.textContent !== (text ?? "")) el.textContent = text ?? "";
}

/** Replaces an element's children only when their texts (the key) changed. */
function list(el: HTMLElement, key: string, make: () => Node[]): void {
  if (el.dataset.key === key) return;
  el.dataset.key = key;
  el.replaceChildren(...make());
}

/** " eruit nemen ." → " eruit nemen." (a stray space before punctuation in the quotes). */
function tidy(q: string): string {
  return q.replace(/\s+([.,;:!?])/g, "$1");
}

export function render(e: Els, s: SessionState, l: LocalState, layout: BarLayout, now = 0): void {
  const doc = e.root.ownerDocument;
  const t = (en: string) => tr(l.lang, en);
  translateSkeleton(e.root, l.lang);
  const replay = s.mode === "replay";
  const cam = l.live || replay;
  doc.body?.classList.toggle("cam", cam);
  e.replay.hidden = !replay;
  e.testZone.hidden = !l.testZone;

  // Over the camera the header is one bar: back, the appliance and the job, and how far through it is. It takes the
  // wordmark's place (the app's icon already says whose app it is); a recording says DEMO in the kicker.
  const head = cam ? l.head : null;
  e.top.classList.toggle("has-head", head !== null);
  e.camBack.hidden = !cam;
  e.camHead.hidden = head === null;
  set(e.camKicker, head ? [head.kicker, replay ? t("Demo") : null].filter(Boolean).join(" · ") || null : null);
  set(e.camTitle, head?.title ?? null);
  // At Job done every segment is filled; a step whose check came back done fills while the next one is on its way.
  const at = head ? (heldStep(s) ?? s.step) : null;
  const all = l.complete;
  const passedNow = at !== null && s.verdict?.step_id === at.step_id && s.verdict.result === "done";
  e.camSteps.hidden = at === null;
  set(e.camCount, at ? `${all ? at.of : at.index + 1} / ${at.of}` : null);
  list(e.camSegs, at ? `${at.of}:${at.index}:${all}:${passedNow}` : "", () =>
    at && at.of <= 16
      ? Array.from({ length: at.of }, (_, i) => Object.assign(doc.createElement("span"), { className: all || i < at.index || (passedNow && i === at.index) ? "done" : i === at.index ? "now" : "" }))
      : [],
  );
  const checking = s.checking || (s.step !== null && s.step.step_id === l.waitStep && now < l.waitUntil && s.safety.state === "OK");
  e.top.classList.toggle("is-checking", checking);

  // The stop wins over everything: a local G1 stop at once, the server's STOP with its fixed script.
  const serverStop = s.safety.state === "STOP" ? s.safety : null;
  const g = l.guard;
  e.stop.hidden = !(serverStop || g.kind === "stop");
  e.root.classList.toggle("red", !e.stop.hidden);
  const script = serverStop ? stopScript(serverStop.script) : g.kind === "stop" ? t("Hands back.") : "";
  // A known script leads with its rule's one line; the calls follow, then the whole script. Unknown: the script leads.
  const lead = (serverStop && l.stopLeads[serverStop.script]) || script;
  const more = lead === script ? "" : script;
  list(e.stopText, lead, () => keepNumbers(doc, lead, l.numbers));
  // The script, one numbered row per sentence of its own, in its order; the spaces between rows keep its text whole.
  list(e.stopMore, more, () =>
    stopRows(more).flatMap((row, i) => {
      const li = doc.createElement("li");
      li.append(doc.createElement("span"));
      li.firstElementChild!.append(...keepNumbers(doc, row, l.numbers));
      return i === 0 ? [li] : [doc.createTextNode(" "), li];
    }),
  );
  e.stopMore.hidden = more === "";
  const icon = serverStop ? stopIcon(serverStop.rule_id) : g.kind === "stop" ? "hand" : "";
  if (e.stopIcon.dataset.icon !== icon) {
    e.stopIcon.dataset.icon = icon;
    e.stopIcon.innerHTML = STOP_ICON[icon] ?? "";
  }
  const calls = serverStop ? phoneNumbers(serverStop.script, l.numbers) : [];
  list(e.stopCalls, calls.map((n) => n.tel).join(","), () =>
    calls.map((n) => {
      const a = doc.createElement("a");
      a.className = "call";
      a.href = `tel:${n.tel}`;
      a.innerHTML = STOP_ICON.call ?? "";
      a.append(`${langOf(l.lang) === "nl" ? "Bel" : "Call"} ${n.display}`);
      return a;
    }),
  );
  // The server's STOP is never a dead end: a script with no number to call says who can do the job, and a way out
  // ends the session (it never clears the stop). A recording offers itself again.
  set(e.stopWho, serverStop && calls.length === 0 ? t("Who to call: the maker's service, or a qualified technician.") : null);
  e.stopWays.hidden = !serverStop;
  set(e.stopAgain, replay ? t("Watch again") : null);
  set(e.stopBack, t(replay ? "Go to the start" : "Back to my jobs"));

  const pause =
    !e.stop.hidden
      ? null
      : g.kind === "pause"
        ? g.why === "hand_lost"
          ? t("I can't see your hand, so I've paused.")
          : g.why === "zone_lost"
            ? langOf(l.lang) === "nl"
              ? t("Hold still: finding the part again.")
              : trf(l.lang, "Hold still: finding the {part} again.", { part: g.label })
            : t("The picture is late, so I've paused.")
        : s.safety.state === "HOLD" && s.safety.precondition !== "user_confirm"
          ? t(HOLD_TEXT[s.safety.precondition] ?? "Paused.")
          : null;

  // A hold for the step after a finished one shows that step's card: its gate holds the step's own message back, and
  // the finished step's card under the hold reads as that step not done. A step the phone cannot name shows no card.
  const st = l.complete ? null : s.safety.state === "OK" ? s.step : (heldStep(s) ?? (stepFinished(s) ? null : s.step));
  e.card.hidden = st === null;
  if (st) {
    set(e.cardIndex, langOf(l.lang) === "nl" ? `Stap ${st.index + 1} van ${st.of}` : `Step ${st.index + 1} of ${st.of}`);
    const own = ourStep(st);
    e.card.classList.toggle("our-step", own);
    set(
      e.cardPage,
      own ? OUR_STEP_TEXT[langOf(l.lang)] : (disclaimer(l.disclaimers, "step_citation", l.lang, st.page) ?? `p. ${st.page}`),
    );
    // The card's title is always the step itself, the line the camera checks. What the guide says about it is a
    // smaller row under it, so a "move closer" or a page note never takes the step's place.
    const said = s.said && s.said.text !== pause && s.said.text !== st.text ? s.said.text : null;
    set(e.cardText, st.text);
    set(e.cardSaid, said);
    set(e.cardStep, null);
    set(e.cardAnswerText, s.answer?.text ?? null);
    e.cardAnswer.hidden = !s.answer;
    // A new step replays the slide-in; the same step redrawn does not.
    const key = `${st.index}:${st.step_id}`;
    if (e.card.dataset.step !== key) {
      if (e.card.dataset.step !== undefined) {
        e.cardText.classList.remove("step-in");
        void e.cardText.offsetWidth;
        e.cardText.classList.add("step-in");
      }
      e.card.dataset.step = key;
    }
    e.cardProgress.style.width = `${Math.round(((st.index + 1) / Math.max(1, st.of)) * 100)}%`;
    // One language: the phone's. The manual's own words, when they are in another, wait behind a tap.
    const q = own ? { shown: undefined, orig: undefined } : quoteIn(st.quote, st.quote_en, l.lang);
    set(e.cardQuote, q.shown ? `“${tidy(q.shown)}”` : null);
    set(e.cardQuoteOrig, q.orig ? `“${tidy(q.orig)}”` : null);
    e.cardOrig.hidden = !q.orig;
  }
  // A pause shows on the step card (dimming it), never over it; with no card it stands on its own.
  set(e.cardPause, st ? pause : null);
  e.card.classList.toggle("paused", st !== null && pause !== null);
  set(e.pauseText, st ? null : pause);
  e.pause.hidden = st !== null || !pause;
  // A maker's card: the citation opens the maker's own PDF at the page. Otherwise the page image comes from the live
  // session (served only for pages its card cites). Replay calls no API for it and opens nothing.
  const cited = st !== null && !ourStep(st);
  const maker = st && cited && !replay && st.page !== undefined ? makerPage(l.sessionManual, st.page) : null;
  e.cardMaker.hidden = maker === null;
  if (maker && e.cardMaker.getAttribute("href") !== maker) e.cardMaker.href = maker;
  e.showPage.hidden = !cited || s.sessionId === null || replay || maker !== null;
  // When the page can open, the citation itself is the button: one chip, not a tag and a link beside it.
  const opens = !e.showPage.hidden || maker !== null;
  if (st) e.cardPage.hidden = opens || !e.cardPage.textContent;
  const chip = opens ? `${e.cardPage.textContent} ›` : "";
  if (!e.showPage.hidden && e.showPage.textContent !== chip) e.showPage.textContent = chip;
  if (maker && e.cardMaker.textContent !== e.cardPage.textContent) e.cardMaker.textContent = e.cardPage.textContent;
  const pv = l.viewer;
  const src = pv ? pv.src : l.pageUrl;
  e.pageView.hidden = l.pageUrl === null && pv === null;
  e.pageBar.hidden = pv === null;
  e.pageFailed.hidden = !pv?.failed;
  e.pageImg.hidden = src === null;
  if (pv) {
    const pg = langOf(l.lang) === "nl" ? ["Pagina", "van"] : ["Page", "of"];
    set(e.pageCap, pv.pages ? `${pg[0]} ${pv.page} ${pg[1]} ${pv.pages}` : `${pg[0]} ${pv.page}`);
    e.pagePrev.disabled = pv.page <= 1;
    e.pageNext.disabled = pv.pages > 0 && pv.page >= pv.pages;
  }
  if (src !== null && e.pageImg.getAttribute("src") !== src) e.pageImg.src = src;
  e.checking.hidden = !checking;
  e.card.classList.toggle("checking-now", checking);
  // The check's result, in the phone's own words from the verdict's fixed fields; a done moves to the next step.
  const v = st && s.verdict?.step_id === st.step_id && s.verdict.result !== "done" ? s.verdict : null;
  set(e.verdict, v ? (v.result === "cant_see" ? `${t("I can't see it.")} ${t(ASK_TEXT[v.ask_for ?? "closer"])}` : t("Not done yet. Have another look at the step.")) : null);
  e.verdict.className = `verdict ${v?.result ?? ""}`;
  // The check's result already says what to do next: the guide's line, which says it again, waits until it is gone.
  if (v) set(e.cardSaid, null);
  const acts = st === null || checking || replay || !l.live || pause !== null;
  // On a step the camera does not check (find, none), the person's word is the only way on: one button, I did it.
  e.done.hidden = acts || st.camera === "find" || st.camera === "none";
  // The person can always let a step pass on their word (confirmed by them, never checked): on every step the camera
  // could check, except while a safety hold or stop stands.
  e.didIt.hidden = acts || s.safety.state !== "OK";
  const passed = l.passed && now < l.passed.until ? l.passed : null;
  set(
    e.passed,
    !passed
      ? null
      : langOf(l.lang) === "nl"
        ? `✓ Stap ${passed.index + 1} ${passed.byYou ? "door jou bevestigd" : "klaar"}`
        : `✓ Step ${passed.index + 1} ${passed.byYou ? "confirmed by you" : "done"}`,
  );

  e.complete.hidden = l.complete === null;
  if (l.complete) {
    const whose = s.mode === "replay" ? "the" : "your";
    // How long it took, only when this phone saw the job from its first step (never a replay's).
    const took = l.complete.min ? ` · ${l.complete.min} min` : "";
    const said = completeText(l.complete.of, l.complete.byYou, whose, l.lang);
    set(e.completeText, took ? said.replace(/\.$/, "") + took : said);
    set(e.completeDue, l.complete.nextDue);
    e.completeNext.hidden = !l.complete.nextDue;
    // The proof: each step and its page. A step the person ended has no tick, since nothing checked it.
    // Only a whole list: a replay joined part-way would show some steps as if they were all.
    const steps = l.complete.steps?.length === l.complete.of ? l.complete.steps : [];
    const shown = steps.length > 8 ? steps.slice(0, 6) : steps;
    list(e.completeSteps, JSON.stringify([steps.length, shown, l.lang]), () => {
      const rows: Node[] = shown.map((x) => {
        const li = el(doc, "li", x.byYou ? "by-you" : undefined);
        li.append(el(doc, "span", "n", String(x.n)), el(doc, "span", "mark", x.byYou ? t("you") : "✓"), el(doc, "span", "t", x.text), el(doc, "span", "pg", x.page === null ? "" : `p. ${x.page}`));
        return li;
      });
      if (shown.length < steps.length) rows.push(el(doc, "li", "more", `+ ${steps.length - shown.length} ${langOf(l.lang) === "nl" ? "meer" : "more"}`));
      return rows;
    });
  }
  e.top.classList.toggle("is-complete", l.complete !== null);
  // The form is asked about before the home screen is: what the person thought fades faster than an icon is needed.
  e.fbOffer.hidden = !l.complete || replay || !l.fbOffer;
  if (!e.fbOffer.hidden) {
    const ft = fbText(l.lang);
    list(e.fbOffer, ft.offer, () => [
      el(doc, "h2", undefined, ft.offer),
      el(doc, "p", "sub", ft.offerLead),
      button(doc, "fb-open", ft.open, "ok"),
      button(doc, "fb-later", ft.notNow, "link"),
    ]);
  }
  const inst = l.complete && s.mode !== "replay" ? l.install : null;
  e.install.hidden = inst === null;
  if (inst) {
    const added = inst.phase === "added";
    e.installPrompt.hidden = added || inst.kind !== "prompt";
    e.installIos.hidden = added || inst.kind !== "ios-safari";
    e.installOther.hidden = added || inst.kind !== "ios-other";
    set(e.installCopy, t(inst.phase === "copied" ? "Link copied" : "Copy the link"));
    e.installAdded.hidden = !added;
    e.installWhy.hidden = added;
    set(e.installWhy, t(inst.kind === "prompt" ? INSTALL_WHY.any : INSTALL_WHY.ios));
    set(e.installUrl, inst.url);
    e.installLater.hidden = added;
  }

  // The job up front: what you need, every step in full, then "Are you ready?". The camera waits for the answer.
  const job = !replay && l.live ? s.preview : null;
  e.preview.hidden = job === null;
  list(e.preview, job ? JSON.stringify([job.seq, job.job_id, l.ticked, l.lang]) : "", () => (job ? previewNodes(doc, job, l.ticked, l.lang) : []));

  // Sentences: the only model prose drawn, and only what G0 passed. With a step card up, the card says them; with none
  // (before the first step, or after the last), the newest is a caption at the top that fades once it has been read.
  // A line the pause already says is not repeated.
  const last = st ? undefined : s.sentences.filter((x) => x.text !== pause).at(-1);
  const read = last && l.caption?.id === last.id ? l.caption.until : null;
  const cap = last && (read === null || now < read) ? last.text : null;
  list(e.sentences, cap ?? "", () => (cap ? [Object.assign(doc.createElement("p"), { textContent: cap })] : []));
  e.sentences.classList.toggle("fading", read !== null && cap !== null && read - now < CAPTION_FADE_MS);

  const bar = s.held ? barText(s.held, l.debug ? "rule" : layout, s.heldCount, l.lang) : "";
  e.bar.hidden = !bar;
  list(e.bar, bar, () => keepNumbers(doc, bar, l.numbers));

  // The tool rail is a debugging view (?debug=1), in words.
  const rail = l.debug ? [...(l.noVoice ? [NO_VOICE_TEXT] : []), ...s.rail.map(railText)] : [];
  e.rail.hidden = rail.length === 0;
  // ?debug=1 only: the on-device timings of this run as a file, for the build log and the eval rows.
  e.saveTimings.hidden = !l.debug || !l.live;
  list(e.rail, rail.join("\u0000"), () =>
    rail.map((t) => {
      const li = doc.createElement("li");
      li.textContent = t;
      return li;
    }),
  );

  // A tap for the step before is passing news: it goes after a few seconds, or when the next step comes.
  const notice = s.notice?.kind === "tap_not_current" && now >= l.staleTapUntil ? null : s.notice;
  const said = notice ? (notice.text ?? t((replay ? REPLAY_NOTICE_TEXT[notice.kind] : undefined) ?? NOTICE_TEXT[notice.kind])) : null;
  // A notice that ends the way forward is the card's state line, with a way on; only passing news floats on the camera.
  const way = notice ? WAY_ON[notice.kind] : undefined;
  const n =
    l.refused ??
    l.talkError ??
    l.camError ??
    (l.slow ? t(OFFLINE_TEXT) : null) ??
    (way ? null : said) ??
    (now < l.handsUntil ? l.handsError : null);
  const stateKey = way && said ? `${way}\u0000${said}\u0000${l.golden}` : "";
  const make = (): Node[] => {
    if (!way || !said) return [];
    const p = doc.createElement("p");
    p.className = "state-line";
    p.textContent = said;
    const acts = doc.createElement("div");
    acts.className = "row";
    if (way === "type" && !replay) {
      const b = doc.createElement("button");
      b.dataset.act = "type-model";
      b.textContent = t("Type the model");
      acts.append(b);
    } else if (l.golden) {
      const a = doc.createElement("a");
      a.className = "button";
      a.href = "?replay=golden";
      a.textContent = t("Watch a recorded repair");
      acts.append(a);
    }
    // A dead end in a live session always has a way out of it: End closes the session and goes home.
    if (l.live && !replay) {
      const b = doc.createElement("button");
      b.dataset.act = "finish";
      b.className = "text-act";
      b.textContent = t("End");
      acts.append(b);
    }
    return acts.childElementCount ? [p, acts] : [p];
  };
  const inCard = !e.card.hidden;
  e.cardState.hidden = !stateKey || !inCard;
  e.state.hidden = !stateKey || inCard || !e.complete.hidden;
  list(e.cardState, inCard ? stateKey : "", inCard ? make : () => []);
  list(e.state, inCard ? "" : stateKey, inCard ? () => [] : make);
  e.handsChip.hidden = l.handsError === null || now < l.handsUntil;
  set(e.notice, l.viewMsg ?? n);
  e.notice.classList.toggle("wait", !l.viewMsg && n !== null && n === t(OFFLINE_TEXT));

  // A user_confirm hold asks for one tap. On a gas rule it is the gas-tap confirm; otherwise the session's own ask
  // (the ring step's "tap yes when ready", or resuming after the user's stop) and the tap is "yes". A power_off_seen
  // hold asks for the same yes once the plug is shown: the yes only asks the server to look at the plug, it clears nothing.
  const ask = s.safety.state === "HOLD" && (s.safety.precondition === "user_confirm" || s.safety.precondition === "power_off_seen") ? s.safety : null;
  const gas = ask !== null && ask.precondition === "user_confirm" && isGasRule(ask.rule_id);
  e.gasTap.hidden = !gas;
  e.confirm.hidden = ask === null || gas;

  // Camera only: the text tucks away and the outline has the screen. Nothing that keeps the person safe ever tucks
  // away: a stop, a hold, a pause, a held line or an ask brings everything back at once, whatever was chosen.
  const holds = !e.stop.hidden || s.safety.state !== "OK" || pause !== null || s.held !== null;
  const clear = cam && l.clear && !holds;
  doc.body?.classList.toggle("clear", clear);
  // Under a hold the arrow still clears the view the hold asks for (the plug, the part): the step's own words tuck
  // away, and what the hold says, the held bar, the notices and the controls stay. A stop always shows everything.
  // It lasts for that hold alone: a hold that ends lets it go, and the next one starts with everything shown.
  if (!holds) l.clearHold = false;
  const clearHeld = cam && l.clearHold && holds && e.stop.hidden && st !== null;
  doc.body?.classList.toggle("clear-hold", clearHeld);
  e.tuck.hidden = !cam || st === null;
  e.tuck.dataset.mode = holds ? "hold" : "clear";
  e.tuck.setAttribute("aria-pressed", String(clearHeld));
  e.clearHint.hidden = !cam || st === null || l.clearUsed || clear || holds;
  e.unclear.hidden = !clear;
  set(e.unclearNews, clear && l.clearNews ? t(l.clearNews === "step" ? "Next step" : "New from the guide") : null);
  set(e.unclearText, clear ? t("Show the text") : null);
  // A yes on a plug hold has the server look at the plug: Checking until it answers, and no second yes meanwhile.
  const looking = ask?.precondition === "power_off_seen" && now < l.yesUntil;
  if (ask && !gas) set(e.confirmBtn, t(looking ? "Checking…" : ask.detail === STOPPED_BY_USER ? "Resume" : "Yes"));
  (e.confirmBtn as HTMLButtonElement).disabled = looking;
  e.confirmBtn.setAttribute("aria-busy", String(looking));

  // The screens before the camera: first run, the reminder landing, home.
  e.firstRun.hidden = l.firstRun === null;
  const facts = (l.firstRun ?? "").split(/(?<=\.)\s+(?=[A-Z])/).filter((x) => x && !/^(?:Not affiliated|Niet verbonden)/.test(x));
  // The whole disclaimer folds under The full terms; its warning that the guide can misread stays in view.
  set(e.firstRunMisread, facts.find((x) => /^(?:It can misread|Hij kan zich vergissen)/.test(x)) ?? null);
  set(e.firstRunTerms, disclaimer(l.disclaimers, "terms", l.lang));
  list(e.firstRunText, facts.join("\u0000"), () =>
    facts.map((t) => {
      const li = doc.createElement("li");
      li.textContent = t;
      return li;
    }),
  );
  const aff = (l.firstRun ?? disclaimer(l.disclaimers, "first_run", l.lang) ?? "").match(/(?:Not affiliated|Niet verbonden)[^.]*\./)?.[0];
  if (aff) set(e.affiliation, aff);
  e.consent.hidden = l.consent === null || l.doorShut;
  e.consentBody.hidden = e.consent.hidden;
  // The box says 18 or older and agrees; with no box, the button says it.
  // Until the box is ticked the button says what it is waiting for.
  set(e.firstRunGo, tr(l.lang, !l.consent ? "I'm 18 or older: continue" : l.consent.ticked ? "Continue" : "Tick the box to continue"));
  e.firstRunGo.hidden = l.doorShut;
  set(e.consentStatus, l.consent && !l.doorShut ? l.consent.status : null);
  set(e.consentText, l.consent?.text ?? null);
  // The in-app consent is a whole screen: its own heading, and its paragraphs above the box, one ruled fact each.
  e.firstRunTitle.textContent = l.consent?.heading ?? tr(l.lang, "Before you start");
  const body = l.consent?.body ?? [];
  list(e.consentBody, body.join("\u0000"), () => body.map((t) => Object.assign(doc.createElement("li"), { textContent: t })));
  if (l.consent && e.consentBox.checked !== l.consent.ticked) e.consentBox.checked = l.consent.ticked;
  e.firstRunGo.disabled = l.consent !== null && !l.consent.ticked;
  // A shut or capped door offers the recorded run; with none on the server it promises nothing.
  set(e.joinError, l.doorReplay && !l.golden && l.joinError ? l.joinError.replace(/\s*(?:You can still watch the demo replay|Je kunt nog wel de demo-opname bekijken)\.$|, (?:or watch the demo replay|of bekijk de demo-opname)(?=\.$)/, "") : l.joinError);
  e.doorWatch.hidden = l.joinError === null || !l.doorReplay || !l.golden;

  const lt = l.landing;
  e.landing.hidden = lt === null || l.firstRun !== null || cam;
  if (lt) {
    set(e.landingTitle, lt.title);
    set(e.landingAlias, lt.alias ? lt.alias.charAt(0).toUpperCase() + lt.alias.slice(1) : null);
    set(e.landingWhy, lt.why);
    set(e.landingMin, lt.minutes ? trf(l.lang, "About {n} min", { n: lt.minutes }) : null);
    e.landingSnooze.hidden = !lt.canSnooze || lt.snooze === "done";
    e.landingSnooze.disabled = lt.snooze === "busy";
    set(e.landingSnoozed, lt.snooze === "done" ? t(SNOOZED_TEXT) : lt.snooze === "failed" ? t(SNOOZE_FAILED_TEXT) : null);
  }

  e.missing.hidden = !l.replayMissing;
  set(e.missing.querySelector("p")!, t(REPLAY_MISSING_TEXT));
  const sheetWasHidden = e.sheet.hidden;
  // People and joining stand over everything else off camera.
  const over = l.people !== null || l.join !== null || l.conn !== null || l.fb !== null || l.settings;
  e.sheet.hidden = cam || l.firstRun !== null || l.sheet === null || l.replayMissing || over;
  // A sheet opened from low on the home list starts at its top.
  if (sheetWasHidden && !e.sheet.hidden) e.sheet.scrollIntoView?.({ block: "start" });
  e.devices.hidden = cam || l.firstRun !== null || l.replayMissing || l.devices === null || l.sheet !== null || l.add !== null || over;
  e.home.hidden = cam || l.firstRun !== null || lt !== null || l.replayMissing || l.sheet !== null || l.add !== null || l.devices !== null || over;
  // A person who can only view runs no repair and adds nothing: the server refuses both, so the phone doesn't offer them.
  const viewer = accessOf(l.folk) === "viewer";
  e.start.hidden = e.home.hidden || viewer;
  e.people.hidden = cam || l.firstRun !== null || l.people === null;
  e.join.hidden = cam || l.firstRun !== null || l.join === null || l.people !== null;
  e.conn.hidden = cam || l.firstRun !== null || l.conn === null;
  e.connLink.hidden = e.people.hidden || l.ha.token === null;
  e.fb.hidden = cam || l.firstRun !== null || l.fb === null;
  set(e.capLine, l.cap);
  e.watch.hidden = e.home.hidden || !l.golden;
  set(e.replayTitle, s.mode === "replay" ? l.replayTitle : null);
  renderHome(e, l);
  renderShared(e, l);
  renderSheet(e, l);
  renderDevices(e, l);
  renderPeople(e, l);
  renderConn(e, l);
  if (l.fb && !e.fb.hidden) list(e.fb, fbKey(l.fb), () => fbNodes(doc, l.fb!));

  e.controls.hidden = !l.live || s.preview !== null;
  // In a step the controls are talk and Done; the label photo and typing a model are for before one, when no job was
  // picked yet (a job started from its sheet already knows its appliance).
  e.moreRow.hidden = st !== null || head !== null;
  e.typeModel.hidden = l.add !== null;
  set(e.talk, l.talking ? talkText(l.talkSince, now, l.lang) : t("Hold to talk"));
  e.talk.classList.toggle("on", l.talking);
  e.talk.setAttribute("aria-pressed", String(l.talking));
  // Hands-free: a toggle beside the press; while on it says so in place of the press, until it is turned off.
  e.listen.hidden = !l.canListen || l.talking;
  set(e.listen, l.canListen && !l.talking ? t(l.listening ? "Listening — tap to stop" : "Hands-free") : null);
  e.listen.classList.toggle("on", l.listening);
  e.listen.setAttribute("aria-pressed", String(l.listening));
  e.talk.hidden = l.listening;
  const hint =
    l.live && !st && !l.add && !s.plate && !l.complete && !pause && e.stop.hidden
      ? head !== null
        ? t("Getting the first step from the manual…")
        : t("Point the camera at the label with the model number (the rating plate; on a dishwasher, it's on the inside edge of the door), then tap Photo of the label.")
      : null;
  set(e.hint, s.sessionId && hint && !s.preview ? hint : null);
  e.hint.classList.toggle("wait", head !== null);
  e.settings.hidden = cam || l.firstRun !== null || !l.settings;
  // The way around a home: three tabs at the foot and Settings in the header, off camera, once there is a home.
  const away = cam || l.firstRun !== null || l.replayMissing || l.join !== null || l.fb !== null || l.add !== null || l.ha.token === null;
  e.tabs.hidden = away;
  e.gear.hidden = away;
  e.peopleLink.hidden = l.folk === null;
  const on = l.people !== null || l.conn !== null ? e.peopleLink : l.devices !== null ? e.devicesLink : l.settings ? null : e.homeTab;
  for (const tab of [e.homeTab, e.devicesLink, e.peopleLink]) {
    if (tab === on) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  }
  // Another screen starts at its top, not where the last one was left; a sheet brings itself into view above.
  const screen = cam ? "cam" : l.firstRun !== null ? "first" : l.people !== null ? "people" : l.join !== null ? "join"
    : l.conn !== null ? "conn" : l.fb !== null ? "fb" : l.settings ? "settings" : l.add !== null ? "add"
    : l.devices !== null ? "devices" : l.sheet !== null ? "sheet" : "home";
  if (e.root.dataset.screen !== screen) {
    if (screen !== "sheet" && e.root.dataset.screen !== undefined) e.root.scrollTop = 0;
    e.root.dataset.screen = screen;
  }
  // The header: the lockup on Home only; a tab page's own title is its header, and a pushed page's back bar is its only one.
  const page = screen === "home" || screen === "cam" ? screen : l.sheet !== null || l.conn !== null || l.devices?.applianceId ? "pushed" : "tab";
  if (e.root.dataset.page !== page) e.root.dataset.page = page;
  if (l.settings) e.gear.setAttribute("aria-current", "page");
  else e.gear.removeAttribute("aria-current");
  set(e.terms, disclaimer(l.disclaimers, "terms", l.lang));
  // The switch shows the language in use; one it doesn't offer shows as English, which is what the screens are in. It
  // has autocomplete off, or a reload brings back the value it showed before (the old language over the new one).
  const shown = LANGS.some(([v]) => v === l.lang.slice(0, 2)) ? l.lang.slice(0, 2) : "en";
  if (e.lang.value !== shown && e.lang.ownerDocument.activeElement !== e.lang) e.lang.value = shown;
  renderAdd(e, l);
  const an = l.another;
  e.another.hidden = l.ha.token === null;
  e.anotherMake.disabled = !!an?.busy;
  set(e.anotherMake, t(an?.url ? "Make a new link" : "Make a link"));
  set(e.anotherUrl, an?.url ?? null);
  e.anotherCopy.hidden = !an?.url;
  set(e.anotherNote, an?.error ?? (an?.copied ? t("Copied.") : an?.until ? trf(l.lang, "Works once, until {time}.", { time: an.until }) : null));
  if (l.ha.token !== null) set(e.deleteHh, t(l.deleteArmed ? "Tap again to delete everything" : "Delete my household"));
  // Deleting the home is an admin's (after set, which shows what it writes); anyone else leaves, from People and alerts.
  e.deleteHh.hidden = l.ha.token === null || (l.folk !== null && accessOf(l.folk) !== "admin");
  e.note.hidden = !l.noteOffered;

  renderPlate(e, s, l);
}

/** The text with each of the rules' phone numbers kept on one line. */
function keepNumbers(doc: Document, text: string, numbers: PhoneNumber[]): Node[] {
  return splitOn(text, numbers).map((p) => {
    if (typeof p === "string") return doc.createTextNode(p);
    const span = doc.createElement("span");
    span.className = "nowrap";
    span.textContent = p.display;
    return span;
  });
}

function renderHome(e: Els, l: LocalState): void {
  const h = l.home;
  const today = new Date().toISOString().slice(0, 10);
  const items = h?.due ?? [];
  const unscheduled = unscheduledJobs(l.cards, items, h?.appliances ?? []);
  const known = new Set([...items, ...unscheduled].map((d) => d.applianceId));
  const rest = (h?.appliances ?? []).filter((a) => !known.has(a.id));
  // An appliance with no jobs listed shows on Home only when it needs something; the rest are on the Appliances tab.
  const needs = rest.filter((a) => a.card_status !== "checked");
  // An empty home shows how a repair goes, then the camera, the recorded repair (for anyone with no appliance in front
  // of them) and typing a model. With appliances, their jobs lead and adding another is a plain button.
  const empty = items.length === 0 && rest.length === 0;
  // With jobs listed, the recorded repair still sits above adding another appliance: it is how anyone sees a repair.
  e.homeList.parentElement?.classList.toggle("has-jobs", !empty);
  e.homeIntro.hidden = !empty;
  e.homeHow.hidden = !empty;
  e.homeType.hidden = !empty || e.start.hidden;
  const label = tr(l.lang, empty ? "Point at an appliance" : "Add another appliance");
  if (e.start.textContent !== label) e.start.textContent = label;
  e.start.classList.toggle("ok", empty);
  // With jobs listed, adding another is on the Appliances tab, and the recorded repair is a row in the list.
  if (items.length + unscheduled.length > 0) {
    e.start.hidden = true;
    e.watch.hidden = true;
  }
  const names = new Map((l.folk ?? []).map((p) => [p.id, p.me ? tr(l.lang, "you") : p.name]));
  const key = JSON.stringify([l.lang, items, unscheduled, (h?.appliances ?? []).map((a) => [a.id, a.alias, a.kind, a.card_status]), today, [...names], accessOf(l.folk), l.golden]);
  list(e.homeList, key, () => {
    const doc = e.root.ownerDocument;
    const out = homeNodes(doc, items, h?.appliances ?? [], today, l.lang, unscheduled, names, accessOf(l.folk) === "viewer", l.golden);
    if (needs.length) {
      const h2 = doc.createElement("h2");
      h2.className = "group needs";
      h2.textContent = tr(l.lang, "Needs you");
      out.push(h2);
    }
    for (const a of needs) {
      // An appliance with no jobs on the list yet opens its own page: why, and what to do next.
      const row = doc.createElement("div");
      row.className = "item needs";
      const open = doc.createElement("button");
      open.type = "button";
      open.className = "item-open";
      open.dataset.act = "devices-open";
      open.dataset.appliance = a.id;
      const t = doc.createElement("strong");
      t.textContent = `${a.alias.charAt(0).toUpperCase()}${a.alias.slice(1)}`;
      const sub = doc.createElement("span");
      sub.className = "sub";
      sub.textContent = [`${a.brand} ${a.model}`.trim(), stateLine(a, items, today, l.lang)].filter(Boolean).join(" · ");
      const txt = doc.createElement("span");
      txt.className = "item-text";
      txt.append(t, sub);
      open.append(kindGlyph(doc, a.kind), txt, chevron(doc));
      row.append(open);
      out.push(row);
    }
    return out;
  });
}

function renderConn(e: Els, l: LocalState): void {
  if (!l.conn || e.conn.hidden) return;
  const st = l.conn;
  list(e.conn, connKey(st, l.lang), () => connNodes(e.root.ownerDocument, st, new Date().toISOString().slice(0, 10), l.lang));
}

function renderPeople(e: Els, l: LocalState): void {
  const doc = e.root.ownerDocument;
  if (l.join && !e.join.hidden) list(e.join, JSON.stringify([l.join.kind, l.join.busy, l.join.error, l.lang]), () => joinNodes(doc, l.join!, l.lang));
  if (!l.people || e.people.hidden) return;
  const st = { ...l.people, people: l.folk };
  const apps = (l.home?.appliances ?? []).map((a) => ({ id: a.id, name: `${a.alias.charAt(0).toUpperCase()}${a.alias.slice(1)}` }));
  list(e.people, peopleKey(st, apps, l.lang), () => peopleNodes(doc, st, apps, new Date().toISOString().slice(0, 10), l.lang));
}

function renderDevices(e: Els, l: LocalState): void {
  const dv = l.devices;
  if (!dv || e.devices.hidden) return;
  const today = new Date().toISOString().slice(0, 10);
  const doc = e.root.ownerDocument;
  const appliances = l.home?.appliances ?? [];
  const due = l.home?.due ?? [];
  if (dv.applianceId === null) {
    const key = JSON.stringify(["all", appliances.map((a) => [a.id, a.alias, a.kind, a.brand, a.model, a.card_status, a.ingest?.notice?.reason]), due, today, dv.wrong, dv.adding, l.lang]);
    list(e.devices, key, () => devicesNodes(doc, appliances, due, today, dv.wrong, l.lang, dv.adding));
    return;
  }
  const a = appliances.find((x) => x.id === dv.applianceId);
  if (!a) {
    list(e.devices, JSON.stringify(["gone", dv.applianceId, l.home === null, l.lang]), () => {
      const p = doc.createElement("p");
      p.className = "sub";
      p.textContent = tr(l.lang, l.home === null ? "Getting your appliances…" : "That appliance is no longer in this home.");
      const b = doc.createElement("button");
      b.type = "button";
      b.dataset.act = "devices-back";
      b.textContent = tr(l.lang, "Your appliances");
      return [p, b];
    });
    return;
  }
  const page = { a, due, unscheduled: unscheduledJobs(l.cards, due, [a]), card: l.cards[a.id] ?? null, st: dv, today, lang: l.lang, canRemove: accessOf(l.folk) !== "viewer" };
  const had = { report: e.devices.querySelector(".report") !== null, done: e.devices.querySelector(".done-line") !== null, answer: e.devices.querySelector(".fault-answer") };
  list(e.devices, applianceKey(page), () => applianceNodes(doc, page));
  // The form just opened, what it did just showed at the top, or a code's answer just came: bring it into view.
  const form = had.report ? null : e.devices.querySelector(".report");
  const answer = e.devices.querySelector(".fault-answer");
  const wrong = dv.scroll === "fault" ? e.devices.querySelector("#something-wrong") : null;
  if (wrong) {
    dv.scroll = null;
    wrong.scrollIntoView?.({ block: "start" });
  } else if (form) form.scrollIntoView?.({ block: "start" });
  else if (answer && answer !== had.answer) answer.scrollIntoView?.({ block: "nearest" });
  else if (!had.done && e.devices.querySelector(".done-line")) e.devices.scrollIntoView?.({ block: "start" });
}

function renderSheet(e: Els, l: LocalState): void {
  const sh = l.sheet;
  if (!sh) return;
  const today = new Date().toISOString().slice(0, 10);
  const d = l.home?.due.find((x) => x.applianceId === sh.applianceId && x.jobId === sh.jobId) ?? null;
  const card = l.cards[sh.applianceId];
  const man = card?.manual ?? null;
  // A card job whose manual gives no interval and that nobody has scheduled isn't on /due: its sheet offers a schedule.
  const own = d || !l.home ? null : (unscheduledJobs(l.cards, l.home.due, l.home.appliances).find((u) => u.applianceId === sh.applianceId && u.jobId === sh.jobId) ?? null);
  const alias = d?.alias ?? own?.alias ?? null;
  const from = !l.devices ? tr(l.lang, "Home") : l.devices.applianceId && alias ? alias.charAt(0).toUpperCase() + alias.slice(1) : tr(l.lang, "Your appliances");
  list(e.sheet, sheetKey(sh, d, today, l.folk, l.lang) + JSON.stringify([man, own, from]), () => sheetNodes(e.root.ownerDocument, sh, d, today, l.lang, man, own, l.folk, from));
}

const DIGIT_WORDS: Record<string, string[]> = {
  en: ["ZERO", "ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE"],
  nl: ["NUL", "EEN", "TWEE", "DRIE", "VIER", "VIJF", "ZES", "ZEVEN", "ACHT", "NEGEN"],
};

/** A character's name under its glyph: a digit's word, or LETTER and the letter. */
export function glyphWord(c: string, lang: string): string {
  if (/^[0-9]$/.test(c)) return DIGIT_WORDS[langOf(lang) === "nl" ? "nl" : "en"]![Number(c)]!;
  return /^\p{L}$/u.test(c) ? `LETTER ${c.toUpperCase()}` : "";
}

function renderPlate(e: Els, s: SessionState, l: LocalState): void {
  const p = s.plate;
  e.plate.hidden = !p || !l.plateChoices;
  if (!p || !l.plateChoices) return;
  const doc = e.root.ownerDocument;
  const key = JSON.stringify([p.photo_id, l.plateChoices, l.lang]);
  if (e.plateFields.dataset.key === key) return;
  e.plateFields.dataset.key = key;
  e.plateFields.replaceChildren(
    ...p.fields.map((f) => {
      const row = doc.createElement("div");
      row.className = "plate-field";
      const name = doc.createElement("span");
      name.textContent = tr(l.lang, PLATE_LABEL[f.name] ?? f.name);
      // The value with each unsure character marked, so the question points at its place on the label.
      const val = doc.createElement("strong");
      const unsure = new Set(f.uncertain.map((u) => u.index));
      if (unsure.size) {
        val.className = "has-unsure";
        [...resolved(f, l.plateChoices![f.name] ?? [])].forEach((c, i) => {
          if (!unsure.has(i)) return val.append(c);
          const m = doc.createElement("mark");
          m.textContent = c;
          val.append(m);
        });
      } else val.textContent = resolved(f, l.plateChoices![f.name] ?? []);
      row.append(name, val);
      f.uncertain.forEach((u, i) => {
        const ask = doc.createElement("div");
        ask.className = "amber ask-char";
        const qn = doc.createElement("span");
        qn.textContent = question(u, l.lang);
        ask.append(qn);
        for (const o of u.options) {
          const b = doc.createElement("button");
          const chosen = (l.plateChoices![f.name] ?? [])[i] === o;
          // A big glyph, and under it the word for it (ONE, LETTER I): the two look alike, the words don't.
          const g = doc.createElement("span");
          g.className = "glyph-char";
          g.textContent = o;
          b.append(g);
          const word = glyphWord(o, l.lang);
          if (word) {
            const w = doc.createElement("span");
            w.className = "glyph-word";
            w.textContent = word;
            b.append(w);
          }
          b.setAttribute("aria-pressed", String(chosen));
          b.dataset.act = "plate-choice";
          b.dataset.field = f.name;
          b.dataset.index = String(i);
          b.dataset.option = o;
          if (chosen) b.className = "chosen";
          ask.append(b);
        }
        row.append(ask);
      });
      return row;
    }),
  );
}

function renderShared(e: Els, l: LocalState): void {
  const sh = l.shared;
  e.shared.hidden = sh === null;
  if (!sh) return;
  set(e.sharedName, sh.name);
  const apps = l.home?.appliances ?? [];
  set(e.sharedAsk, tr(l.lang, apps.length ? "Which appliance is this manual for?" : "Add the appliance it is for, and I'll read it."));
  list(e.sharedList, JSON.stringify(apps.map((a) => [a.id, a.alias])), () =>
    apps.map((a) => {
      const b = e.root.ownerDocument.createElement("button");
      b.dataset.act = "shared-pick";
      b.dataset.appliance = a.id;
      b.textContent = `${a.alias.charAt(0).toUpperCase()}${a.alias.slice(1)}`;
      return b;
    }),
  );
}

function renderAdd(e: Els, l: LocalState): void {
  const a = l.add;
  e.add.hidden = a === null;
  if (!a) return;
  const ap = a.appliance;
  const ing = ap?.ingest;
  const final = ap !== null && FINAL.includes(ap.card_status);
  set(e.addHead, a.existing && ap ? trf(l.lang, "The {name}'s manual", { name: ap.alias }) : tr(l.lang, "Add this appliance"));
  set(e.addLabel, ap ? `${ap.brand} ${ap.model}`.trim() || null : a.label);
  e.addModelRow.hidden = ap !== null || a.photoId !== null;
  e.addNameRow.hidden = ap !== null;
  e.addBrandRow.hidden = ap !== null || !a.needBrand;
  e.addGo.hidden = ap !== null || a.busy;
  // After a failed add the same button retries, and says so; asked for the brand, it still adds.
  set(e.addGo, tr(l.lang, a.error && !a.needBrand ? "Try again" : "Add"));
  // A dead end with a notice needs no second line; the dot shows only while something is under way.
  const dead = final && !!ing?.notice;
  // Under way, the wait is a list of its stages, so the screen says what it is waiting on and what comes next.
  const rows = ap && !final && !a.upload && !a.error ? stageRows(ing?.stage ?? "plate", a.photoId !== null) : [];
  e.addStages.hidden = rows.length === 0;
  list(e.addStages, JSON.stringify([l.lang, rows]), () =>
    rows.map((r) => {
      const li = e.root.ownerDocument.createElement("li");
      li.className = r.state;
      li.textContent = tr(l.lang, r.state === "now" ? STAGE_TEXT[r.stage] : STAGE_STEPS[r.stage]);
      if (r.state === "now") li.setAttribute("aria-current", "step");
      return li;
    }),
  );
  set(e.addStatus, a.upload ? tr(l.lang, "Sending the PDF…") : a.error || dead || rows.length ? null : ap ? statusText(ap, l.lang) : a.busy ? tr(l.lang, "Adding…") : null);
  e.addStatus.classList.toggle("working", a.upload !== null || (ap ? !final : a.busy));
  e.addStatus.classList.toggle("ready", !!ap && final && ap.card_status !== "failed" && !a.upload);
  const notice = a.upload ? null : (ing?.notice ?? null);
  set(e.addNotice, a.error ?? noticeText(notice, l.lang));

  // The wait: the server's code-written progress, the manual as soon as it is in, and the jobs as they pass.
  const progress = a.upload ? a.upload.sent : final ? null : (ing?.progress ?? null);
  e.addMeter.hidden = progress === null;
  if (progress !== null) {
    const pc = Math.round(progress * 100);
    e.addMeter.setAttribute("aria-valuenow", String(pc));
    (e.addMeter.firstElementChild as HTMLElement).style.width = `${pc}%`;
  }
  set(e.addEta, a.upload ? `${Math.round(a.upload.sent * 100)}%` : final ? null : etaText(ing?.eta_s, l.lang));
  const m = a.upload ? undefined : ing?.manual;
  e.addManual.hidden = !m;
  set(e.addTitle, m?.title ?? null);
  set(e.addPages, manualLine(m, l.lang));
  const thumb = m?.thumb_url && a.thumb?.url === m.thumb_url && a.thumb.src ? a.thumb.src : null;
  e.addThumb.hidden = thumb === null;
  if (thumb !== null && e.addThumb.getAttribute("src") !== thumb) e.addThumb.src = thumb;
  set(e.addQuotes, a.upload ? null : quotesLine(ing?.quotes, l.lang));
  const ready = (a.upload ? null : ing?.jobs_ready) ?? [];
  e.addReadyHead.hidden = ready.length === 0;
  list(e.addReady, JSON.stringify([l.lang, ready]), () =>
    ready.map((j) => {
      const li = e.root.ownerDocument.createElement("li");
      const t = e.root.ownerDocument.createElement("span");
      t.className = "ready-title";
      t.textContent = titleIn(j.title, l.lang);
      const f = e.root.ownerDocument.createElement("span");
      f.className = "sub";
      f.textContent = readyFacts(j, l.lang);
      li.append(t, f);
      return li;
    }),
  );
  const refused = a.upload ? [] : refusedLines(ing?.jobs_refused, l.lang);
  list(e.addRefused, JSON.stringify(refused), () =>
    refused.map((r) => {
      const li = e.root.ownerDocument.createElement("li");
      li.textContent = r;
      return li;
    }),
  );
  e.addWait.hidden = progress === null && e.addEta.textContent === "" && !m && e.addQuotes.textContent === "" && ready.length === 0 && refused.length === 0;

  // Asking for the manual: the link field right under the notice, then Add the PDF and the maker's page.
  const ask = !a.upload && wantsManual(notice);
  e.addPdfRow.hidden = !ask;
  e.addManualRow.hidden = !ask;
  const maker = httpsUrl(notice?.maker_page);
  e.addMaker.hidden = maker === null;
  if (maker !== null && e.addMaker.getAttribute("href") !== maker) e.addMaker.href = maker;
  const jobs = a.jobs ?? [];
  e.addStart.hidden = jobs.length > 0 || !(ap && final && ap.card_status !== "failed");
  list(e.addJobs, JSON.stringify(jobs), () =>
    jobs.map((j) => {
      const b = e.root.ownerDocument.createElement("button");
      b.className = "ok";
      b.dataset.act = "add-job";
      b.dataset.job = j.id;
      b.textContent = j.title;
      return b;
    }),
  );
}

/** What a step's camera does, in the phone's words. */
export const CAMERA_MODE_TEXT: Record<"check" | "find" | "none", string> = {
  check: "The camera checks it",
  find: "The camera shows where",
  none: "No camera: you say when it's done",
};

/** The preview's nodes: the title, the needs to tick, every step written out with its manual words, and the ask. */
/** The manual's words in one language, the phone's. An English phone reads the English line given beside a manual in
 *  another language, with the manual's own words kept behind a tap so the citation stays checkable; any other phone
 *  reads the manual's own words, once. */
export function quoteIn(q: string | undefined, en: string | undefined, lang: string): { shown: string | undefined; orig: string | undefined } {
  if (langOf(lang) === "en" && en && en !== q) return { shown: en, orig: q };
  return { shown: q, orig: undefined };
}

function previewNodes(doc: Document, pv: JobPreview, ticked: string[], lang: string): Node[] {
  const t = (en: string) => tr(lang, en);
  const el = <K extends keyof HTMLElementTagNameMap>(tag: K, cls: string | null, text?: string): HTMLElementTagNameMap[K] => {
    const n = doc.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };
  const quote = (q: string | undefined, en: string | undefined): Node[] => {
    const one = quoteIn(q, en, lang);
    if (!one.shown) return [];
    const b = el("blockquote", null);
    b.append(el("p", null, `“${tidy(one.shown)}”`));
    if (one.orig) {
      const fold = el("details", "orig");
      const orig = el("p", null, `“${tidy(one.orig)}”`);
      orig.translate = false;
      fold.append(el("summary", null, t("The manual's own words")), orig);
      b.append(fold);
    }
    return [b];
  };
  const out: Node[] = [el("p", "kicker", t("Before you start")), el("h1", null, pv.title)];
  out.push(el("h2", null, t("What you need")));
  if (pv.needs.length === 0) out.push(el("p", "sub", t("Nothing to get ready.")));
  else {
    out.push(el("p", "sub", t("Tick what you have ready.")));
    const ul = el("ul", "needs");
    for (const n of pv.needs) {
      const li = el("li", null);
      const lab = el("label", "need");
      const box = el("input", null);
      box.type = "checkbox";
      box.dataset.act = "need";
      box.value = n.id;
      box.checked = ticked.includes(n.id);
      const txt = el("span", null);
      txt.append(el("strong", null, n.what), el("span", "mono-page", ` p. ${n.page}`));
      lab.append(box, txt);
      li.append(lab, ...quote(n.quote, n.quote_en));
      ul.append(li);
    }
    out.push(ul);
  }
  out.push(el("h2", null, t("What you're going to do")));
  const ol = el("ol", "preview-steps");
  for (const st of pv.steps) {
    const li = el("li", null);
    li.append(el("p", "preview-step", st.text));
    const meta = el("p", "preview-meta");
    meta.append(el("span", `cam-mode ${st.camera}`, t(CAMERA_MODE_TEXT[st.camera])));
    if (st.origin !== "fixit" && st.page !== undefined) meta.append(el("span", "mono-page", `p. ${st.page}`));
    li.append(meta);
    if (st.origin !== "fixit") li.append(...quote(st.quote, st.quote_en));
    for (const w of st.warnings) li.append(el("p", `preview-warn ${w.kind}`, w.text));
    ol.append(li);
  }
  out.push(ol);
  const ask = el("div", "preview-ask");
  ask.append(el("h2", null, t("Are you ready?")));
  const go = el("button", "ok big", t("I'm ready"));
  go.dataset.act = "job-ready";
  ask.append(go);
  out.push(ask);
  return out;
}

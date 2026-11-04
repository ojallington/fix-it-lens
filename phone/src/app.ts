// Wires the phone together: the camera, hands and flow on every frame, G1 through the guard, the frame gate, the WS,
// the screen and speech. Every outside thing comes in through Deps, so replay and tests run the same code with no
// camera, no socket and no model API.

import type { S2C, ZoneMsg } from "../../contracts/ts/src/ws.ts";
import { LONG_EDGE, imageSize, type FrameTick } from "./camera.ts";
import type { FlowIn, FlowOut } from "./flow.worker.ts";
import { typing } from "./fresh.ts";
import type { Constants } from "./g1.ts";
import { bbox, type Pt } from "./geom.ts";
import { Guard, type GuardState, type ZoneNow } from "./guard.ts";
import { Metrics } from "./metrics.ts";
import type { HandsResult } from "./hands.ts";
import { NO_PAN, coverMap, draw, focusPan, readLook, type DrawShape, type DrawZone, type Look, type Pan } from "./overlay.ts";
import { FrameGate } from "./phash.ts";
import { confirmArg, initialChoices } from "./plate.ts";
import { Player, frameUrl, loadRecording, timeline } from "./replay.ts";
import { asked, initial, passed, reduce, type SessionState } from "./session.ts";
import { disclaimer, type Disclaimers } from "./disclaimer.ts";
import { micErrorText, type ListenerLike, type RecorderLike } from "./audio.ts";
import { applianceBody, FINAL, firstBytes, PDF_TEXT, pdfProblem, POLL_MS, postError, settled, uploadError, type AddInput, type Appliance } from "./appliance.ts";
import { authed, CAP_UNKNOWN_TEXT, RETURN_LINK_TEXT, capOf, capText, joinCapText, APP_CONSENT_PLACEHOLDER, doorCappedText, doorFromUrl, doorState, joinHousehold, JOIN_TEXT, type Cap, type ConsentLine, type Door, type JoinError, type TokenStore } from "./household.ts";
import { dueItem, jobPath, parseSheetHash, sheetHash, snoozeMin, sortDue, draftBody, draftOf, today, type Change, type DueNames, type DueWire } from "./schedule.ts";
import { datePickText, sheetInitial, type SheetError } from "./sheet.ts";
import { cardView, devicesHash, devicesInitial, faultAnswer, faultError, FAULT_TEXT, parseDevicesHash, reportBody, reportError, reportInitial, type CardRecord, type ReportKind } from "./devices.ts";
import { Speech, type SynthLike } from "./speech.ts";
import type { Alarm } from "./stop.ts";
import { Tracker } from "./track.ts";
import {
  SETTINGS_HASH,
  CAMERA_TEXT,
  captionMs,
  HANDS_TEXT,
  localInitial,
  mount,
  NO_HOUSEHOLD_TEXT,
  ourStep,
  type DoneStep,
  REFUSED_TEXT,
  render,
  replayTitle,
  shareText,
  type BarLayout,
  type Els,
  type LocalState,
  type PhoneNumber,
} from "./ui.ts";
import type { InstallHost } from "./install.ts";
import { VIEW_TEXT, checkView, ringWarning, type ViewResult } from "./viewcheck.ts";
import { WsClient, type SocketLike } from "./ws.ts";
import { langOf, tr } from "./words.ts";
import {
  accessOf,
  alertsBody,
  inviteBody,
  inviteDraft,
  inviteMail,
  inviteShareTitle,
  joinCode,
  joinError,
  nameError,
  parseAlerts,
  parseInvite,
  parsePeople,
  peopleError,
  peopleInitial,
  PEOPLE_HASH,
  PEOPLE_TEXT,
  roleBody,
  type RoleChoice,
} from "./people.ts";
import { CHOICES, fbBody, fbError, FB_HASH, fbInitial, isPilot, type Choice, type Written } from "./feedback.ts";
import { connAddress, connBody, connDraft, connError, connInitial, CONN_HASH, CONN_TEXT, parseConnections, parseMade, type ConnKind, type Scope } from "./connections.ts";

export const API = "/api/v1";
const VIEW_EVERY_MS = 500;
const RING_WARNING_MS = 6000;
const VIEW_OK_MS = 2000;
const PENDING_KEEP = 3;
/** How often the device's timings go into the session record while frames run. */
const TIMINGS_EVERY_MS = 5000;
/** How long the hand-tracking banner stays before it folds into the header chip. */
const HANDS_BANNER_MS = 6000;
/** Recorded connection trouble is the recording's, not the viewer's: a replay leaves it out. */
const REPLAY_QUIET = new Set(["slow_connection", "reconnected", "no_recording"]);
const PASSED_MS = 1400;
/** After I did it, the next step can wait on the server looking at earlier steps again; the button comes back after this. */
const DID_IT_WAIT_MS = 30_000;
/** How long Yes on a plug hold shows Checking at most, waiting for the server's look at the plug. */
const YES_WAIT_MS = 20_000;
/** How long the line about a tap for the step before stays up. */
const STALE_TAP_MS = 6_000;
/** How often an open People screen reads the people again, so a new joiner shows up without a reload. */
const PEOPLE_POLL_MS = 15_000;

export interface HandsLike {
  detect(source: TexImageSource, tMs: number, imgW: number, imgH: number): HandsResult;
}

export interface WorkerLike {
  postMessage(m: FlowIn, transfer: Transferable[]): void;
  onmessage: ((ev: MessageEvent<FlowOut>) => void) | null;
}

export interface Deps {
  doc: Document;
  search: string;
  wsUrl: string;
  fetch: typeof fetch;
  synth: SynthLike | null;
  alarm: Alarm;
  openSocket: (url: string) => SocketLike;
  openCamera: (video: HTMLVideoElement) => Promise<unknown>;
  eachFrame: (video: HTMLVideoElement, fn: (t: FrameTick) => void) => () => void;
  loadHands: () => Promise<HandsLike>;
  flowWorker: () => WorkerLike;
  jpeg: (src: ImageBitmap, w: number, h: number) => Promise<string>;
  bitmap: (src: HTMLVideoElement | HTMLImageElement) => Promise<ImageBitmap>;
  heading: () => number | null;
  now: () => number;
  raf: (fn: (t: number) => void) => void;
  g1: Constants;
  lang: string;
  barLayout: BarLayout;
  /** The fixed disclaimer texts (rules/disclaimer.json). */
  disclaimers: Disclaimers | null;
  /** The household token on this device (the pilot door's), sent as hello.household and the /api bearer. */
  household: TokenStore;
  /** This app's address, for the Home Assistant row (location.origin). */
  origin: string;
  copy: (text: string) => Promise<void>;
  /** A new recorder, made inside the press (audio.ts's Recorder). */
  recorder: () => RecorderLike;
  /** The open microphone for hands-free talk, made inside the tap that turns it on (optional: absent, no toggle). */
  listener?: () => ListenerLike;
  /** Opens the app at a new query (the new appliance's Start). */
  navigate: (search: string) => void;
  /** Swaps the address's query without a reload, so a job's Start opens the camera inside the tap (the STOP tone's
   * unlock needs that tap on iOS); without it, Start loads the query as a new page. */
  replaceSearch?: (search: string) => void;
  /** The in-app consent every door shows (a pilot invite too: its signed form is on paper); null shows a placeholder. */
  appConsent?: ConsentLine | null;
  /** Whether this device already accepted the first-run screen, and recording that it has. */
  firstRunSeen: () => boolean;
  markFirstRun: () => void;
  /** Camera only has been used on this phone before, so its first-time hint is not shown (optional: absent, it shows). */
  clearSeen?: () => boolean;
  markClear?: () => void;
  /** Dev builds only: a labelled TEST ZONE for M1, never filmed. It adds a zone; it can never remove or weaken one. */
  dev: boolean;
  /** The numbers the rules' texts name (rules/numbers.json), for the stop's call buttons. */
  numbers?: PhoneNumber[];
  /** Each stop script's lead line (rules/stops.json). */
  stopLeads?: Record<string, string>;
  /** The phone signed in with a new token (a join, a return link, a new home): this browser's push subscription, if
   * it has one, leaves the token it was saved under and is saved under the new one, so reminders follow the person
   * now using the phone. */
  pushMove?: (from: string | null, to: string) => Promise<void>;
  /** A short buzz when a step passes (Android); absent where the phone cannot vibrate. */
  vibrate?: (ms: number) => void;
  /** Whether the server has a curated recording to watch (GET /replay/golden is 200). */
  golden?: () => Promise<boolean>;
  /** The address's #fragment at load: #job/<appliance>/<job> opens that job's sheet. */
  hash?: string;
  /** Puts the sheet's place in the address and history ("" for home), so Back closes it. */
  pushHash?: (hash: string) => void;
  /** The address's path at load: /try is a judges' link (its code in #k=). */
  path?: string;
  /** Takes a door's code (invite, #k=, #return/…) out of the address once read. */
  clearDoor?: () => void;
  /** Remember the language picked in settings and open the app again in it. */
  setLang?: (lang: string) => void;
  /** The home's language now applies (GET /household with the new token); the page reloads once if it differs. */
  followHomeLang?: () => Promise<void>;
  /** Keeping the app on the home screen (Android's prompt, Safari's steps); absent, it is never offered. */
  install?: InstallHost;
  /** Sends a body with the bearer and reports the share sent (XHR, for the PDF's progress); absent, plain fetch. */
  upload?: (url: string, body: Blob, headers: Record<string, string>, sent: (f: number) => void) => Promise<Response>;
  /** The PDF shared to the app (?share=pdf), taken once from where the service worker held it; null when none. */
  sharedPdf?: () => Promise<File | null>;
  /** A blob's object URL for an <img>, and freeing it; absent, the manual's thumbnail isn't shown. */
  objectUrl?: { make(b: Blob): string; drop(u: string): void };
}

/** "nl.gas.tap" → "gas tap": the zone's name for the screen, from its rule id. */
export function zoneLabel(ruleId: string): string {
  const [, family, zone] = ruleId.split(".");
  return `${family ?? ""} ${(zone ?? "").replace(/_/g, " ")}`.trim();
}

export class App {
  readonly s: SessionState = initial();
  readonly l: LocalState = localInitial();
  readonly els: Els;
  readonly speech: Speech;
  readonly tracker = new Tracker();
  readonly gate = new FrameGate();
  readonly guard: Guard;
  readonly metrics = new Metrics();
  private timingsAt = 0;
  ws: WsClient | null = null;
  img = { w: LONG_EDGE, h: Math.round((LONG_EDGE * 9) / 16) };
  /** Frame ids sent to the server, newest last (the technician note's photos come from these). */
  readonly sentFrames: string[] = [];
  /** The latest frame as shown: a recorded frame in replay, else null (the <video> shows the camera). */
  replayFrame: HTMLImageElement | null = null;
  /** The frame's pan (CSS px) that keeps the shown outlines clear of the header and the sheet; eased per paint. */
  pan: Pan = NO_PAN;
  /** The overlay's colours, read once from the page's tokens. */
  look: Look | null = null;

  private video: HTMLVideoElement;
  private canvas: HTMLCanvasElement;
  private shapesSince = new Map<string, number>();
  /** The steps of this job the person ended themselves (our own steps, witnesses.by_person), for the Job done line. */
  private byYou = new Set<string>();
  /** The steps done so far this job, for Job done's list. */
  private doneSteps: DoneStep[] = [];
  /** When this job's first step came (now()), for the minutes Job done shows; null in a replay or before it. */
  private jobSince: number | null = null;
  private pulseSince = new Map<string, number>();
  private pending = new Map<string, ImageBitmap>();
  private flowBusy = false;
  private hands: HandsLike | null = null;
  private lastPoints: Pt[] = [];
  private headings = new Map<string, number>();
  private frontHeading: number | null = null;
  private lastView: ViewResult | null = null;
  private lastViewAt = -Infinity;
  private viewOkUntil = 0;
  private ringWarnUntil = 0;
  private ringWarnText: string | null = null;
  private wasStopped = false;
  private wasServerStop = false;
  /** The door this phone is opening its household through, until it has a token. */
  private door: Door | null = null;
  private dueNames: DueNames = new Map();
  private joining = false;
  private noticeBeforeOk = false;
  private serverSawStop = false;
  private params: URLSearchParams;
  /** The shared PDF itself, while the person chooses its appliance (l.shared names it). */
  private sharedFile: File | null = null;

  constructor(private readonly d: Deps) {
    const root = d.doc.getElementById("app")!;
    this.els = mount(root);
    this.video = d.doc.getElementById("cam") as HTMLVideoElement;
    this.canvas = d.doc.getElementById("overlay") as HTMLCanvasElement;
    this.speech = new Speech(d.synth, d.lang);
    this.guard = new Guard(d.g1, (m) => this.ws?.send(m));
    this.params = new URLSearchParams(d.search);
    this.l.clearUsed = d.clearSeen?.() ?? false;
    this.l.canListen = d.listener !== undefined;
    root.addEventListener("click", (ev) => this.onClick(ev));
    // A tap on the picture itself (the sheet and header take their own taps) tucks the text away, or brings it back.
    d.doc.addEventListener("click", (ev) => {
      const el = ev.target as HTMLElement | null;
      if (el && (el.id === "cam" || el.classList?.contains("replay-frame") || el === root)) this.toggleClear();
    });
    root.addEventListener("change", (ev) => void this.onChange(ev));
    root.addEventListener("input", (ev) => this.onField(ev));
    // The keyboard's Go (Enter) in the add form presses its visible button, as a form would: on an iPhone that key is
    // where the thumb already is.
    root.addEventListener("keydown", (ev) => {
      const t = ev.target as HTMLElement;
      if (ev.key === "Enter" && t instanceof HTMLInputElement && t.dataset.cf === "label") {
        ev.preventDefault();
        void this.connMake();
        return;
      }
      if (ev.key === "Enter" && t instanceof HTMLInputElement && (t.dataset.pf === "join-name" || t.dataset.pf === "rename")) {
        ev.preventDefault();
        if (t.dataset.pf === "join-name") void this.joinPeople();
        else this.peopleTap("people-rename-save", "");
        return;
      }
      if (ev.key !== "Enter" || !(t instanceof HTMLInputElement) || !t.closest("#add")) return;
      const btn = t.id === "add-pdf" ? t.closest("#add-pdf-row")?.querySelector("button") : this.els.root.querySelector<HTMLButtonElement>("#add-go");
      if (btn instanceof HTMLButtonElement && !btn.hidden && !btn.disabled) {
        ev.preventDefault();
        btn.click();
      }
    });
    // No form in the app navigates: Enter taps the form's own button, and its click does the work.
    root.addEventListener("submit", (ev) => ev.preventDefault());
    // Leaving the app (home button, another app) ends most sessions: what was timed goes into the record first.
    d.doc.addEventListener("visibilitychange", () => {
      if (d.doc.visibilityState === "hidden") this.sendTimings();
      else void this.refreshPeople();
    });
    root.addEventListener("change", (ev) => this.onField(ev));
    root.addEventListener("pointerdown", (ev) => this.onTalk(ev, true));
    root.addEventListener("pointerup", (ev) => this.onTalk(ev, false));
    root.addEventListener("pointercancel", (ev) => this.onTalk(ev, false));
    root.addEventListener("contextmenu", (ev) => {
      if ((ev.target as HTMLElement).closest?.('[data-act="talk"]')) ev.preventDefault(); // no long-press menu
    });
  }

  /** "Remind me tomorrow": the server sends the reminder once more in 24 h; the due date stays. */
  private async snooze(): Promise<void> {
    const lt = this.l.landing;
    if (!lt || lt.snooze === "busy" || lt.snooze === "done") return;
    lt.snooze = "busy";
    try {
      const r = await this.api(`${API}/reminders/${encodeURIComponent(lt.key)}/snooze`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ hours: 24 }),
      });
      lt.snooze = r.ok ? "done" : "failed";
    } catch {
      lt.snooze = "failed";
    }
  }

  /** Nothing under way that a reload would end: no session or replay, no add flow, no text typed and not sent. */
  quiet(): boolean {
    return !this.l.live && this.ws === null && this.replayFrame === null && !this.params.has("replay") && this.l.add === null && !typing(this.els.root);
  }

  /** Every /api call: the household bearer added; a 401 forgets the token and asks to join again. */
  async api(url: string, init?: RequestInit): Promise<Response> {
    const r = this.lost(await this.d.fetch(url, authed(init, this.d.household.get())));
    await this.capped(r);
    return r;
  }

  /** A 429 with a CapError body: the cap is said kindly on home. The caller still gets the response. */
  private async capped(r: Response): Promise<void> {
    if (r.status !== 429) return;
    const cap = capOf(await r.clone().json().catch(() => null));
    if (cap) this.l.cap = capText(cap, new Date(), this.d.lang);
  }

  private lost(r: Response): Response {
    const token = this.d.household.get();
    if (r.status === 401 && token !== null) void this.confirmLost(token);
    return r;
  }

  private checkingLost = false;
  /** The door shown because the token stopped working: a shut door then says to ask for an invite, not that it is shut. */
  private lostDoor: Door | null = null;

  /** A 401 is asked once more, with GET /household, before the phone forgets its home: the token may be a person's
   * only key to it, so a stray 401 must not throw it away. */
  private async confirmLost(token: string): Promise<void> {
    if (this.checkingLost) return;
    this.checkingLost = true;
    try {
      const r = await this.d.fetch(`${API}/household`, authed(undefined, token)).catch(() => null);
      if (r?.status !== 401 || this.d.household.get() !== token) return;
      this.d.household.set(null);
      this.l.ha.token = null;
      this.l.conn = null;
      // A door from the address is already open (or its code on the way): the dead token just goes.
      if (this.door !== null || this.joining) return;
      const door: Door = { kind: "open" };
      this.lostDoor = door;
      this.showDoor(door);
      this.l.joinError = tr(this.d.lang, JOIN_TEXT.lost);
    } finally {
      this.checkingLost = false;
    }
  }

  async boot(): Promise<void> {
    this.l.disclaimers = this.d.disclaimers;
    this.l.lang = this.d.lang;
    this.l.ha = { origin: this.d.origin, token: this.d.household.get() };
    this.l.debug = this.params.get("debug") === "1";
    this.l.numbers = this.d.numbers ?? [];
    this.l.stopLeads = this.d.stopLeads ?? {};
    const replay = this.params.get("replay");
    if (replay) return this.startReplay(replay);
    const reminder = this.params.get("reminder");
    if (reminder) {
      const ch = this.params.get("ch") ?? "push";
      void this
        .api(`${API}/reminders/${encodeURIComponent(reminder)}/opened`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ channel: ch }),
        })
        .catch(() => undefined);
      const min = Number(this.params.get("min"));
      this.l.landing = {
        key: reminder,
        canSnooze: ch !== "ics",
        snooze: null,
        title: this.params.get("title") ?? tr(this.d.lang, "Your reminder"),
        alias: this.params.get("alias"),
        why: this.params.get("why"),
        minutes: Number.isFinite(min) && min > 0 ? min : null,
      };
    }
    this.l.testZone = this.d.dev && this.params.get("testzone") === "1";
    // A phone with no household comes in through a door: an invite link, a judges' link (/try#k=), a return link
    // from its other phone, or the open door (the site's Start). Each joins with no typing, once consent is ticked.
    let hash = this.d.hash ?? "";
    // A people invite (#join/<code>): read once and taken out of the address, as a return link is.
    const code = this.openJoin(hash);
    if (code) hash = "";
    else {
      // A door with a code (a return link, an invite, a judges' link) is followed even with a home stored: once that
      // home's token was replaced, it is the way back in, and the old token goes only when the new one comes. The
      // open door is for a phone with no home.
      const found = doorFromUrl(this.d.path ?? "/", hash, this.params.get("invite"), this.params.has("start"));
      const door = found && (this.d.household.get() === null || found.kind !== "open") ? found : null;
      if (door) {
        this.d.clearDoor?.();
        if (door.kind !== "invite") hash = "";
      }
      if (door?.kind === "return") void this.comeBack(door);
      else if (door) this.showDoor(door);
      if (door?.kind === "open" && this.params.has("deleted")) this.l.joinError = tr(this.d.lang, JOIN_TEXT.deleted);
    }
    // Job done's form opens on a fresh page, away from the camera.
    if (this.params.has("feedback") && this.d.household.get() !== null) {
      hash = FB_HASH;
      this.d.pushHash?.(FB_HASH.slice(1));
    }
    if (this.door === null && !code && !this.d.firstRunSeen()) this.l.firstRun = disclaimer(this.d.disclaimers, "first_run", this.d.lang) ?? "";
    this.route(hash);
    if (this.params.get("share") === "pdf") void this.takeShared();
    void this.loadHome();
    void this.probeGolden();
    this.loop();
  }

  /** A PDF shared from another app: held until the person picks its appliance, or adds a new one. */
  private async takeShared(): Promise<void> {
    const f = await this.d.sharedPdf?.().catch(() => null);
    if (!f) return;
    this.sharedFile = f;
    this.l.shared = { name: f.name || "manual.pdf" };
  }

  /** The held PDF goes to the appliance now on the add screen, as a picked file would. */
  private sendShared(): void {
    const f = this.sharedFile;
    if (!f || !this.l.add?.appliance) return;
    this.sharedFile = null;
    this.l.shared = null;
    void this.sendPdf(f);
  }

  // ---- keeping the app ----------------------------------------------------------------------------------------------

  /** After a real job is done, never in a replay or a recorded run: offer what this browser allows, if anything. */
  private offerInstall(): void {
    if (this.s.mode === "replay" || this.d.doc.defaultView?.location.pathname.startsWith("/watch")) return;
    const kind = this.d.install?.kind() ?? null;
    // The page itself, without the query or #: a reminder's or an invite's words stay out of a link to copy.
    const url = `${this.d.origin}${this.d.doc.defaultView?.location.pathname ?? "/"}`;
    this.l.install = kind ? { kind, phase: "offer", url } : null;
  }

  private async install(): Promise<void> {
    const host = this.d.install;
    if (!host || !this.l.install) return;
    const added = await host.prompt().catch(() => false);
    host.done();
    this.l.install = added ? { ...this.l.install, phase: "added" } : null;
  }

  // ---- the job sheet --------------------------------------------------------------------------------------------

  /** The address changed (Back, or a link): open the sheet it names, or close it. */
  route(hash: string): void {
    // A join link tapped while the app is open arrives here, not at boot.
    if (this.openJoin(hash)) return;
    this.l.settings = hash === SETTINGS_HASH;
    if (this.l.settings) {
      this.l.sheet = null;
      this.l.devices = null;
      this.l.landing = null;
      this.l.people = null;
      this.l.conn = null;
      this.l.fb = null;
      return;
    }
    if (hash === FB_HASH) {
      this.l.sheet = null;
      this.l.devices = null;
      this.l.landing = null;
      this.l.people = null;
      this.l.conn = null;
      if (!this.l.fb) void this.openFeedback();
      return;
    }
    this.l.fb = null;
    if (hash === CONN_HASH) {
      this.l.sheet = null;
      this.l.devices = null;
      this.l.landing = null;
      this.l.people = null;
      if (!this.l.conn) {
        this.l.conn = connInitial();
        void this.loadConn();
      }
      return;
    }
    this.l.conn = null;
    if (hash === PEOPLE_HASH) {
      this.l.sheet = null;
      this.l.devices = null;
      this.l.landing = null;
      if (!this.l.people) {
        this.l.people = peopleInitial();
        void this.loadPeople();
      } else void this.refreshPeople();
      return;
    }
    this.l.people = null;
    const dv = parseDevicesHash(hash);
    if (dv) {
      this.l.sheet = null;
      this.l.landing = null;
      if (this.l.devices?.applianceId !== dv.applianceId) this.l.devices = devicesInitial(dv.applianceId);
      if (dv.applianceId) void this.loadRecord(dv.applianceId);
      return;
    }
    const at = parseSheetHash(hash);
    if (!at) {
      this.l.sheet = null;
      this.l.devices = null;
      return;
    }
    if (this.l.sheet?.applianceId === at.applianceId && this.l.sheet.jobId === at.jobId) return;
    this.l.sheet = sheetInitial(at.applianceId, at.jobId);
    // A reminder's landing says less than the sheet: once the job's sheet is open (from the landing, or a calendar
    // entry whose address names both), the landing has done its work. Its open was already counted at boot.
    this.l.landing = null;
    if (this.l.home && !this.onList(at.applianceId, at.jobId)) this.l.sheet.error = "missing";
    void this.loadChanges();
  }

  /** On /due, or a card job whose manual gives no interval: off the list until someone sets its schedule, but its sheet
   * offers one. */
  private onList(applianceId: string, jobId: string): boolean {
    if (this.l.home?.due.some((d) => d.applianceId === applianceId && d.jobId === jobId)) return true;
    const j = this.l.cards[applianceId]?.jobs.find((x) => x.id === jobId);
    return !!j && j.kind === "upkeep" && !j.interval;
  }

  private openSheet(applianceId: string, jobId: string): void {
    this.d.pushHash?.(sheetHash(applianceId, jobId));
    this.route(sheetHash(applianceId, jobId));
  }

  private closeSheet(): void {
    this.l.sheet = null;
    // A sheet opened from an appliance's page goes back to it.
    this.d.pushHash?.(this.l.devices ? devicesHash(this.l.devices.applianceId) : "");
  }

  private openDevices(applianceId: string | null): void {
    this.d.pushHash?.(devicesHash(applianceId));
    this.route(devicesHash(applianceId));
  }

  /** Something's wrong: what the typed code means, from this appliance's checked card only (no model call). */
  private async lookFault(): Promise<void> {
    const dv = this.l.devices;
    const id = dv?.applianceId;
    if (!dv || !id || dv.fault.busy) return;
    const f = dv.fault;
    const code = f.code.trim();
    f.error = null;
    if (!code) {
      f.error = FAULT_TEXT.empty;
      return;
    }
    if (code.length > 20) {
      f.error = FAULT_TEXT.long;
      return;
    }
    f.busy = true;
    try {
      const r = await this.api(`${API}/appliances/${encodeURIComponent(id)}/meaning?code=${encodeURIComponent(code)}`);
      if (!r.ok) {
        f.error = r.status === 429 && this.l.cap ? this.l.cap : faultError(r.status, this.l.lang);
        f.answer = null;
        return;
      }
      const ans = faultAnswer(await r.json(), this.d.lang.slice(0, 2).toLowerCase());
      if (!ans) f.error = FAULT_TEXT.failed;
      f.answer = ans;
    } catch {
      f.error = FAULT_TEXT.failed;
    } finally {
      f.busy = false;
    }
  }

  /** How the appliance's card was checked: GET .../card/record; a 404 (no card, or a server before 1.25) says "none". */
  private async loadRecord(applianceId: string): Promise<void> {
    const dv = this.l.devices;
    if (!dv || dv.record !== null || this.d.household.get() === null) return;
    const r = await this.api(`${API}/appliances/${encodeURIComponent(applianceId)}/card/record`).catch(() => null);
    const rec = r?.ok ? ((await r.json().catch(() => null)) as CardRecord | null) : null;
    if (this.l.devices !== dv) return;
    // A network failure leaves it to try again on the next open.
    if (r) dv.record = rec && typeof rec.reports_open === "object" ? rec : "none";
  }

  /** Report a problem (POST .../reports), or "This isn't my appliance" (POST .../not-mine). Neither edits the card. */
  /** DELETE /appliances/{id}: gone from this home (a 404 means it already was), then back to the list. */
  private async removeAppliance(): Promise<void> {
    const dv = this.l.devices;
    const id = dv?.applianceId;
    if (!dv || !id || dv.remove !== "ask") return;
    dv.remove = "busy";
    dv.removeError = null;
    const r = await this.api(`${API}/appliances/${encodeURIComponent(id)}`, { method: "DELETE" }).catch(() => null);
    if (r && (r.ok || r.status === 404)) {
      const home = this.l.home;
      if (home) home.appliances = home.appliances.filter((a) => a.id !== id);
      delete this.l.cards[id];
      this.openDevices(null);
      void this.loadHome();
      return;
    }
    dv.remove = "ask";
    dv.removeError = tr(this.l.lang, "Couldn't remove it. Check the connection and try again.");
  }

  private async sendReport(): Promise<void> {
    const dv = this.l.devices;
    const d = dv?.report;
    const id = dv?.applianceId;
    if (!dv || !d || !id || d.busy) return;
    const got = reportBody(d, this.l.cards[id]?.manual.pages ?? 0, this.l.lang);
    if ("error" in got) {
      d.error = got.error;
      return;
    }
    d.busy = true;
    d.error = null;
    const notMine = d.kind === "not_mine";
    try {
      const r = await this.api(`${API}/appliances/${encodeURIComponent(id)}/${notMine ? "not-mine" : "reports"}`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(got.body),
      });
      if (!r.ok) {
        const e = (await r.json().catch(() => ({}))) as { error?: string };
        d.error = reportError(r.status, e.error, this.l.lang);
        return;
      }
      dv.report = null;
      dv.done = notMine ? "unlinked" : "sent";
      dv.record = null;
      if (notMine) {
        // The card is gone for this home: its jobs leave the list, and the page offers the manual instead.
        const ap = (await r.json().catch(() => null)) as Appliance | null;
        const home = this.l.home;
        if (home && ap?.id === id) home.appliances = home.appliances.map((a) => (a.id === id ? ap : a));
        delete this.l.cards[id];
        void this.loadHome();
      } else void this.loadRecord(id);
    } catch {
      d.error = reportError(0, undefined, this.l.lang);
    } finally {
      d.busy = false;
    }
  }

  /** A page of the home's own uploaded manual, fetched with the bearer; the previous page's object URL is dropped. */
  private async viewPage(applianceId: string, page: number): Promise<void> {
    const ou = this.d.objectUrl;
    if (!ou) return;
    const pages = this.l.cards[applianceId]?.manual.pages ?? 0;
    const n = Math.max(1, pages ? Math.min(page, pages) : page);
    const old = this.l.viewer?.src;
    const v = { applianceId, page: n, pages, src: old ?? null, failed: false };
    this.l.viewer = v;
    const r = await this.api(`${API}/appliances/${encodeURIComponent(applianceId)}/manual/pages/${n}.png`).catch(() => null);
    if (this.l.viewer !== v) return;
    if (!r?.ok) {
      v.failed = true;
      v.src = null;
      if (old) ou.drop(old);
      return;
    }
    const src = ou.make(await r.blob());
    if (this.l.viewer !== v) return ou.drop(src);
    v.src = src;
    if (old) ou.drop(old);
  }

  /** A live job's header: its appliance's name and the job's title, once home and the card have them. */
  private liveHead(): LocalState["head"] {
    const aid = this.params.get("appliance");
    const jid = this.params.get("job");
    if (!aid || !jid) return null;
    const title = this.l.cards[aid]?.jobs.find((j) => j.id === jid)?.title;
    if (!title) return null;
    const alias = this.l.home?.appliances.find((a) => a.id === aid)?.alias ?? null;
    return { kicker: alias, title };
  }

  /** The session's appliance's manual, so each step's citation opens the maker's PDF at its page. */
  private async loadSessionManual(applianceId: string): Promise<void> {
    let view = this.l.cards[applianceId];
    if (!view && this.d.household.get() !== null) {
      const r = await this.api(`${API}/appliances/${encodeURIComponent(applianceId)}/card`).catch(() => null);
      const got = r?.ok ? cardView(await r.json().catch(() => null), this.d.lang) : null;
      if (got) view = this.l.cards[applianceId] = got;
    }
    if (view && this.l.live) this.l.sessionManual = view.manual;
  }

  private closeViewer(): void {
    const src = this.l.viewer?.src;
    if (src) this.d.objectUrl?.drop(src);
    this.l.viewer = null;
  }

  private async loadChanges(): Promise<void> {
    const sh = this.l.sheet;
    if (!sh || this.d.household.get() === null) return;
    const r = await this.api(jobPath(API, sh.applianceId, sh.jobId, "changes")).catch(() => null);
    const changes = r?.ok ? ((await r.json().catch(() => [])) as Change[]) : [];
    if (this.l.sheet === sh) sh.changes = Array.isArray(changes) ? changes : [];
  }

  /** One schedule write: the server answers with the job's new Due, which replaces the row; then its history again. */
  private async sheetWrite(method: string, tail: string, body: unknown, on422: SheetError): Promise<void> {
    const sh = this.l.sheet;
    if (!sh || sh.busy) return;
    sh.busy = true;
    sh.error = null;
    try {
      const r = await this.api(jobPath(API, sh.applianceId, sh.jobId, tail), {
        method,
        headers: { "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!r.ok) {
        sh.error = r.status === 422 ? on422 : r.status === 403 ? "readOnly" : r.status === 409 ? "nothing" : "failed";
        // Refused although the picker allowed it: the job's dates moved on another phone, so fetch them again.
        if (r.status === 422 && tail === "snooze") void this.loadHome();
        return;
      }
      // 204: the job left /due (its manual gives no interval, and its schedule was stopped or undone).
      if (r.status === 204) this.dropDue(sh.applianceId, sh.jobId);
      else this.replaceDue((await r.json()) as DueWire);
      sh.edit = null;
      sh.doneDate = "";
      sh.snoozeDate = "";
      sh.doneAsk = false;
      await this.loadChanges();
    } catch {
      sh.error = "failed";
    } finally {
      sh.busy = false;
    }
  }

  private replaceDue(w: DueWire): void {
    const home = this.l.home;
    if (!home) return;
    const next = dueItem(w, new Map(home.appliances.map((a) => [a.id, a.alias])), this.dueNames);
    // A job with no interval joins the list when its schedule is set (or an undo brings it back).
    const rest = home.due.filter((d) => !(d.applianceId === next.applianceId && d.jobId === next.jobId));
    home.due = sortDue([...rest, next]);
  }

  private dropDue(applianceId: string, jobId: string): void {
    const home = this.l.home;
    if (home) home.due = home.due.filter((d) => !(d.applianceId === applianceId && d.jobId === jobId));
  }

  /** What is typed in the sheet, kept in its state so a redraw keeps it. */
  private onField(ev: Event): void {
    const sh = this.l.sheet;
    const input = ev.target as HTMLInputElement;
    // A date button shows the date picked, in the app's language.
    const box = input.closest?.<HTMLElement>(".date-pick");
    const pick = box?.querySelector(".date-pick-text");
    if (pick) pick.textContent = input.value || !box?.dataset.empty ? datePickText(input.value, this.d.lang) : box.dataset.empty;
    const saveDate = input.closest?.(".last-done")?.querySelector<HTMLButtonElement>('[data-act="last-date"]');
    if (saveDate) saveDate.disabled = !input.value;
    if (input.dataset.fault === "code" && this.l.devices) {
      this.l.devices.fault.code = input.value;
      return;
    }
    const rf = input.dataset.report;
    const rd = this.l.devices?.report;
    if (rf && rd) {
      if (rf === "kind" && input.checked) {
        rd.kind = input.value as ReportKind;
        rd.error = null;
      } else if (rf === "step" || rf === "page" || rf === "note") rd[rf] = input.value;
      return;
    }
    if (input.dataset.pf) {
      this.peopleField(input, ev.type === "change");
      return;
    }
    if (input.dataset.cf) {
      this.connField(input, ev.type === "change");
      return;
    }
    if (input.dataset.fb) {
      this.fbField(input);
      return;
    }
    const f = input.dataset.field;
    if (!sh || !f) return;
    if (f === "assignee") {
      if (ev.type === "change") void this.sheetWrite("PUT", "assignee", { person_id: input.value || null }, "failed");
      return;
    }
    if (f === "doneDate" && input.closest(".other-day")) {
      // Another day… under Mark done: picking the day marks it done on that day.
      if (ev.type === "change" && input.value) this.sheetTap("sheet-done-date", input.value);
      return;
    }
    if (f === "snoozeDate" && input.closest(".other-day")) {
      // Another day… is a choice like Tomorrow: picking the day is the snooze.
      if (ev.type === "change" && input.value) this.sheetTap("sheet-snooze-to", input.value);
      return;
    }
    if (f === "doneDate" || f === "snoozeDate") {
      sh[f] = input.value;
      // Its Save or Snooze wakes as soon as there is a date, before the next redraw.
      const b = input.closest(".field")?.querySelector("button");
      if (b) b.disabled = sh.busy || !input.value;
    }
    else if (sh.edit) {
      if (f === "kind" && input.checked) sh.edit.kind = input.value as typeof sh.edit.kind;
      else if (f === "unit") sh.edit.unit = input.value as typeof sh.edit.unit;
      else if (f === "every" || f === "min" || f === "max") sh.edit[f] = input.value;
      // The hint under the numbers follows what is typed, without redrawing the inputs.
      const why = this.els.sheet.querySelector<HTMLElement>(".sched-edit .error");
      const got = draftBody(sh.edit, this.l.lang);
      if (why) {
        why.textContent = "error" in got ? got.error : "";
        why.hidden = !("error" in got);
      }
    }
  }

  private sheetTap(act: string, date?: string): void {
    const sh = this.l.sheet;
    if (!sh) return;
    const item = this.l.home?.due.find((d) => d.applianceId === sh.applianceId && d.jobId === sh.jobId);
    switch (act) {
      case "sheet-close":
        this.closeSheet();
        break;
      case "sched-edit":
        if (item || this.onList(sh.applianceId, sh.jobId)) sh.edit = draftOf(item ?? null);
        // The editor lives under Job settings: keep that open once it closes, to show what was saved.
        sh.more = true;
        break;
      case "sched-stop":
        void this.sheetWrite("PUT", "schedule", { kind: "manual" }, "schedule");
        break;
      case "sched-cancel":
        sh.edit = null;
        break;
      case "sched-save": {
        const got = sh.edit ? draftBody(sh.edit, this.l.lang) : null;
        if (got && "body" in got) void this.sheetWrite("PUT", "schedule", got.body, "schedule");
        else if (got) {
          // Save on an editor still empty: say what it needs.
          const why = this.els.sheet.querySelector<HTMLElement>(".sched-edit .error");
          if (why) {
            why.textContent = got.error;
            why.hidden = false;
          }
        }
        break;
      }
      case "sheet-done":
        void this.sheetWrite("POST", "done", {}, "future");
        break;
      case "sheet-done-date":
        if (date) sh.doneDate = date;
        if (sh.doneDate) void this.sheetWrite("POST", "done", { date: sh.doneDate }, "future");
        break;
      case "sheet-done-ask":
        sh.doneAsk = !sh.doneAsk;
        sh.later = false;
        break;
      case "sheet-later":
        sh.later = !sh.later;
        sh.doneAsk = false;
        break;
      case "sheet-more":
        sh.more = !sh.more;
        break;
      case "sheet-snooze-to":
      case "sheet-snooze":
        if (act === "sheet-snooze-to") sh.snoozeDate = date ?? "";
        // A date typed past the picker's lower bound is refused here, with the same words the server's 422 gets.
        if (sh.snoozeDate && item && sh.snoozeDate < snoozeMin(item, today())) sh.error = "later";
        else if (sh.snoozeDate) void this.sheetWrite("PUT", "snooze", { until: sh.snoozeDate }, "later");
        break;
      case "sheet-unsnooze":
        void this.sheetWrite("PUT", "snooze", { until: null }, "failed");
        break;
      case "sheet-undo":
        void this.sheetWrite("POST", "undo", undefined, "failed");
        break;
    }
  }

  // ---- replay ---------------------------------------------------------------------------------------------------

  private framesAsked = 0;
  private frameShown = { n: 0, url: null as string | null };

  /** A recorded frame. A home's own recording gives its frames only to that home's token, which an img's src can't
   * carry: with a token stored the frame is fetched with it and shown from a blob. One that comes back late never
   * covers a newer one. */
  private async replayFrameAt(img: HTMLImageElement, url: string): Promise<void> {
    const ou = this.d.objectUrl;
    if (this.d.household.get() === null || !ou) {
      img.src = url;
      return;
    }
    const n = ++this.framesAsked;
    const r = await this.api(url).catch(() => null);
    const blob = r?.ok ? await r.blob().catch(() => null) : null;
    if (!blob || n < this.frameShown.n) return;
    if (this.frameShown.url) ou.drop(this.frameShown.url);
    this.frameShown = { n, url: ou.make(blob) };
    img.src = this.frameShown.url!;
  }

  private async startReplay(id: string): Promise<void> {
    this.s.mode = "replay";
    this.s.replayId = id;
    const rec = await loadRecording(API, id, (u, i) => this.api(String(u), i)).catch(() => null);
    if (!rec) {
      this.s.mode = null;
      this.l.replayMissing = true;
      this.loop();
      return;
    }
    this.l.replayTitle = replayTitle(rec.summary, this.l.lang);
    const job = rec.summary?.job_title;
    if (job) this.l.head = { kicker: replayTitle({ appliance_alias: rec.summary?.appliance_alias ?? null, job_title: null }, this.l.lang), title: job };
    const img = this.d.doc.createElement("img");
    img.className = "replay-frame";
    // Outlines are in the recorded frame's pixels: the frame's own size maps them, whatever its shape.
    img.addEventListener("load", () => {
      if (img.naturalWidth > 0 && img.naturalHeight > 0) this.img = { w: img.naturalWidth, h: img.naturalHeight };
    });
    this.video.replaceWith(img);
    this.replayFrame = img;
    const player = new Player(timeline(rec), (it) => {
      if (it.what === "s2c") {
        if (it.msg.type === "notice" && REPLAY_QUIET.has(it.msg.kind)) return;
        this.onMessage(it.msg);
      }
      else if (it.what === "frame") void this.replayFrameAt(img, frameUrl(API, rec.meta.session_id, it.frameId));
      else {
        // The phone's own stop, as it fired live.
        this.l.guard =
          it.msg.kind === "stop"
            ? {
                kind: "stop",
                zoneId: it.msg.zone_id,
                label: "",
                event: { t_ms: it.msg.t_ms, kind: "stop", reason: it.msg.reason ?? "distance", ttc_ms: it.msg.ttc_ms, dist_cm: it.msg.dist_cm },
                at: it.msg.t_ms,
              }
            : { kind: "pause", zoneId: it.msg.zone_id, label: "part", why: it.msg.reason === "hand_lost" ? "hand_lost" : it.msg.reason === "stale_frame" ? "stale_frame" : "zone_lost", event: null };
        this.alarmOnTransition();
      }
    });
    const tick = (t: number): void => {
      player.tick(t);
      if (!player.done) this.d.raf(tick);
    };
    this.d.raf(tick);
    this.loop();
  }

  // ---- live -----------------------------------------------------------------------------------------------------

  /** A job's Start: the camera opens on that job within the press, and the address names it for a reload. */
  private startJob(applianceId: string, jobId: string): void {
    const search = `?appliance=${encodeURIComponent(applianceId)}&job=${encodeURIComponent(jobId)}`;
    if (!this.d.replaceSearch) return this.d.navigate(search);
    this.d.replaceSearch(search);
    this.params = new URLSearchParams(search);
    this.l.add = null;
    void this.startLive();
  }

  async startLive(): Promise<void> {
    if (this.l.live || this.l.firstRun !== null) return;
    this.l.live = true;
    this.l.landing = null;
    this.l.clear = false;
    this.l.clearNews = null;
    this.d.alarm.unlock?.();
    this.ws = new WsClient({
      url: this.d.wsUrl,
      lang: this.d.lang,
      household: this.d.household.get(),
      open: this.d.openSocket,
      applianceId: this.params.get("appliance"),
      jobId: this.params.get("job"),
    });
    this.ws.on((m) => {
      if (m.type === "notice" && !this.ws?.connected) this.noticeBeforeOk = true;
      this.onMessage(m);
    });
    this.ws.onRefused(() => void this.onRefused());
    // Deleted, here or on another phone: this phone forgets the household and goes back to the start, saying why.
    this.ws.onGone(() => {
      this.d.household.set(null);
      this.d.navigate("?start&deleted");
    });
    this.ws.onCapped((reason) => {
      // The day's cap: said kindly in the session, and again on home; replay and reminders keep working.
      const cap = capOf(reason);
      this.l.slow = false;
      this.l.cap = cap ? capText(cap, new Date(), this.d.lang) : tr(this.d.lang, CAP_UNKNOWN_TEXT);
      this.l.refused = this.l.cap;
    });
    this.ws.onStatus((up) => {
      this.l.slow = !up && !this.ws?.refused;
      if (up) this.noticeBeforeOk = false;
      if (up) this.gate.reset();
    });
    this.ws.connect();
    const aid = this.params.get("appliance");
    if (aid) void this.loadSessionManual(aid);
    // A named job is shown up front first: the camera starts only when the person says they are ready (or, from a
    // server that sends no preview, at the first step).
    if (this.params.get("job")) {
      // The hand model loads while the person reads, so the first frame after Ready waits only for the camera.
      this.handsLoad = this.d.loadHands();
      this.handsLoad.catch(() => undefined);
      this.toCamera = () => {
        this.toCamera = null;
        void this.startCamera();
      };
      return;
    }
    await this.startCamera();
  }

  /** The camera, hand tracking and the frame loop; called inside the Ready tap where there is one (iOS wants the
   * camera opened from the person's own gesture). */
  private async startCamera(): Promise<void> {
    const hands = this.handsLoad ?? this.d.loadHands();
    hands.catch(() => undefined);
    try {
      await this.d.openCamera(this.video);
    } catch (e) {
      console.warn("camera:", e);
      this.l.camError = tr(this.l.lang, CAMERA_TEXT);
      return;
    }
    this.img = imageSize(this.video.videoWidth || 1280, this.video.videoHeight || 720);
    try {
      this.hands = await hands;
    } catch (e) {
      // No WebGL or no model: the phone carries on without hand tracking and says so; G1 then sees no hand.
      console.warn("hand tracking:", e);
      this.l.handsError = tr(this.l.lang, HANDS_TEXT);
      this.l.handsUntil = this.d.now() + HANDS_BANNER_MS;
    }
    const w = this.d.flowWorker();
    w.onmessage = (ev) => void this.onFlow(ev.data);
    this.d.eachFrame(this.video, (t) => void this.onFrame(t, w));
  }

  /** The hand model, started early where a preview gives the time. */
  private handsLoad: Promise<HandsLike> | null = null;

  /** Starts the camera, once, when the preview has been answered. */
  private toCamera: (() => void) | null = null;

  private async onFrame(t: FrameTick, w: WorkerLike): Promise<void> {
    // The preview again (the job restarted): nothing is looked at or sent until the person is ready.
    if (this.s.preview) return;
    // The stop path first, synchronously on this frame: hands, then G1. Nothing async sits in front of it.
    const h = this.hands?.detect(this.video, t.captureMs, this.img.w, this.img.h);
    if (h) this.metrics.handsTook(h.ms);
    this.metrics.frames++;
    const g0 = this.d.now();
    this.lastPoints = h?.points ?? [];
    const zones = this.zonesNow();
    const before = this.l.guard.kind;
    this.l.guard = this.guard.frame(
      {
        frameId: t.frameId,
        tMs: t.captureMs,
        ageMs: this.d.now() - t.captureMs,
        points: this.lastPoints,
        trackerConfident: this.tracker.confident,
        zones,
      },
      this.ws?.connected ?? false,
    );
    this.metrics.g1.push(this.d.now() - g0);
    if (before !== this.l.guard.kind) {
      if (this.l.guard.kind === "stop") this.metrics.pendingStop = { frameId: t.frameId, captureMs: t.captureMs };
      this.alarmOnTransition();
    }
    this.maybeCheckView(zones);
    if (this.d.now() - this.timingsAt >= TIMINGS_EVERY_MS) this.sendTimings();

    // Then the flow worker, latest-wins: a frame is skipped while the worker is still on an older one.
    if (this.d.heading() !== null) this.headings.set(t.frameId, this.d.heading()!);
    while (this.headings.size > 600) this.headings.delete(this.headings.keys().next().value!);
    if (this.flowBusy) return;
    this.flowBusy = true;
    const [forWorker, keep] = await Promise.all([this.d.bitmap(this.video), this.d.bitmap(this.video)]);
    this.pending.set(t.frameId, keep);
    while (this.pending.size > PENDING_KEEP) {
      const [id, b] = this.pending.entries().next().value!;
      b.close();
      this.pending.delete(id);
    }
    w.postMessage({ frameId: t.frameId, bitmap: forWorker, imgW: this.img.w, imgH: this.img.h, roi: this.roi() }, [forWorker]);
  }

  private async onFlow(r: FlowOut): Promise<void> {
    this.flowBusy = false;
    if (r.error) console.warn("flow worker:", r.error);
    this.tracker.push({ frameId: r.frameId, delta: r.delta, points: r.points, inliers: r.inliers });
    this.metrics.flow.push(r.ms);
    this.gate.trackerWantsAnchor = this.tracker.wantsAnchor();
    const bmp = this.pending.get(r.frameId);
    if (!bmp) return;
    this.pending.delete(r.frameId);
    const why = this.gate.offer(r.phash, this.d.now());
    if (why && this.ws) {
      const jpeg = await this.d.jpeg(bmp, this.img.w, this.img.h);
      if (this.ws.sendFrame({ type: "frame", frame_id: r.frameId, jpeg_1024: jpeg, phash: r.phash, width: this.img.w, height: this.img.h })) {
        this.sentFrames.push(r.frameId);
        if (this.sentFrames.length > 50) this.sentFrames.shift();
      }
    }
    bmp.close();
  }

  /** The box the flow worker seeds corners in: every outline and zone, as they are now. */
  private roi(): FlowIn["roi"] {
    const pts: Pt[] = [];
    for (const z of this.s.zones.values()) for (const p of z.polys) pts.push(...(this.tracker.warpPoly(z.msg.frame_id, p) ?? []));
    for (const o of this.s.outlines.values()) for (const p of o.polys) pts.push(...(this.tracker.warpPoly(o.frameId, p) ?? []));
    return pts.length ? bbox(pts) : null;
  }

  zonesNow(): ZoneNow[] {
    const out: ZoneNow[] = [];
    for (const [id, z] of this.s.zones) {
      const poly = z.polys[0] ? this.tracker.warpPoly(z.msg.frame_id, z.polys[0]) : null;
      out.push({
        zoneId: id,
        label: zoneLabel(z.msg.rule_id),
        polygon: poly,
        ringCm: z.msg.ring_cm,
        ringStep: z.msg.ring_step ?? false,
        scaleCmPerPx: this.scaleFor(z.msg, poly),
        toolLenCm: z.msg.tool_len_cm ?? 0,
        testZone: false,
      });
    }
    if (this.l.testZone) out.push(this.testZone());
    return out;
  }

  /** Scale now: the part's known width over its width in the image; else the view check's scale, tracked. */
  private scaleFor(z: ZoneMsg, poly: Pt[] | null): number | null {
    if (poly && z.known_width_cm) {
      const b = bbox(poly);
      return z.known_width_cm / Math.max(1, b.x1 - b.x0);
    }
    if (this.lastView?.ok) return this.lastView.scaleCmPerPx;
    return null;
  }

  private testZone(): ZoneNow {
    const cx = this.img.w / 2;
    const cy = this.img.h / 2;
    return {
      zoneId: "test-zone",
      label: "TEST ZONE",
      polygon: [
        { x: cx - 40, y: cy - 40 },
        { x: cx + 40, y: cy - 40 },
        { x: cx + 40, y: cy + 40 },
        { x: cx - 40, y: cy + 40 },
      ],
      ringCm: 3,
      ringStep: false,
      scaleCmPerPx: 0.1,
      toolLenCm: 0,
      testZone: true,
    };
  }

  private maybeCheckView(zones: ZoneNow[]): void {
    const st = this.s.step;
    const now = this.d.now();
    if (!st || (st.view !== "side" && zones.length === 0) || now - this.lastViewAt < VIEW_EVERY_MS) return;
    this.lastViewAt = now;
    const zone = zones.find((z) => !z.testZone);
    const zmsg = zone ? this.s.zones.get(zone.zoneId)?.msg : undefined;
    const target = [...this.s.outlines.values()].find((o) => o.kind === "target");
    const r = checkView({
      imgW: this.img.w,
      imgH: this.img.h,
      frontHeadingDeg: this.frontHeading,
      headingDeg: this.d.heading(),
      zone: zone?.polygon ?? null,
      target: target?.polys[0] ? this.tracker.warpPoly(target.frameId, target.polys[0]) : null,
      knownWidthCm: zmsg?.known_width_cm ?? null,
    });
    const changed = r.ok !== this.lastView?.ok || r.kind !== this.lastView?.kind;
    this.lastView = r;
    if (changed) {
      this.ws?.send({ type: "view", ok: r.ok, kind: r.kind, scale_cm_per_px: r.scaleCmPerPx });
      if (r.ok) this.viewOkUntil = now + VIEW_OK_MS;
    }
  }

  // ---- server messages --------------------------------------------------------------------------------------------

  onMessage(m: S2C): void {
    const now = this.d.now();
    if (m.type === "outline" || m.type === "zone") {
      const id = m.type === "outline" ? m.part_id : m.zone_id;
      if (!this.shapesSince.has(id)) this.shapesSince.set(id, now);
      // The first outline of a job is located from the front: its heading is what a side view turns away from.
      if (this.frontHeading === null) this.frontHeading = this.headings.get(m.frame_id) ?? this.d.heading();
      if (m.type === "zone") {
        if (m.ring_step && !this.s.zones.get(m.zone_id)?.msg.ring_step) {
          this.ringWarnText = ringWarning(m.ring_cm, zoneLabel(m.rule_id), this.l.lang);
          this.ringWarnUntil = now + RING_WARNING_MS;
        }
      }
    }
    if (this.l.clear) {
      if (m.type === "step" && m.step_id !== this.s.step?.step_id) this.l.clearNews = "step";
      else if (m.type === "sentence" && this.s.step && !passed(this.s, m)) this.l.clearNews ??= "said";
    }
    if (m.type === "step" && m.step_id !== this.s.step?.step_id) {
      this.lastView = null;
      this.shapesSince.clear();
      if (m.index === 0) this.jobSince = this.s.mode === "replay" ? null : now;
    }
    const late = (m.type === "sentence" || m.type === "held") && passed(this.s, m);
    // The job is done on this phone: a check after it has nothing to show on.
    if (m.type === "checking" && this.l.complete) return;
    reduce(this.s, m);
    if (m.type === "step") this.toCamera?.();
    if (m.type === "zone") {
      const p = this.s.zones.get(m.zone_id)?.polys[0];
      if (p) this.tracker.anchored(m.frame_id, Math.max(bbox(p).x1 - bbox(p).x0, bbox(p).y1 - bbox(p).y0));
    }
    if (m.type === "sentence" && !late) {
      this.l.caption = { id: m.id, until: now + captionMs(m.text) };
      this.speech.onDeviceOnly = this.s.mode === "local";
      // A line of the STOP script in force is the rules' fixed text (no model or user words), and is always heard.
      const stop = this.s.safety.state === "STOP" ? this.s.safety.script : null;
      this.speech.say(m, !!stop && m.text.trim().length > 0 && stop.includes(m.text.trim()));
    }
    if (m.type === "step") this.l.waitStep = null;
    // The server's answer to a yes on a plug hold: a word about it, the hold again or lifted, the step itself.
    if (m.type === "safety" || m.type === "step" || m.type === "notice" || (m.type === "sentence" && !late)) this.l.yesUntil = 0;
    if (m.type === "notice" && m.kind === "tap_not_current") this.l.staleTapUntil = now + STALE_TAP_MS;
    // A step after Job done: the server's last look found a step not done after all, and the job is open again on it.
    if (m.type === "step" && this.l.complete) this.l.complete = null;
    // A hold or stop after Job done: the job is open again (a reopened step whose gate holds says so before its card).
    // Safety outranks the done screen, and the last step's card is not the one held, so neither shows.
    if (m.type === "safety" && m.state.state !== "OK" && this.l.complete) {
      this.l.complete = null;
      this.s.step = null;
    }
    if (m.type === "checking") this.gate.checking = true;
    if (m.type === "verdict") {
      this.gate.checking = false;
      const st = this.s.step;
      if (m.verdict.result === "done") {
        for (const o of this.s.outlines.values()) if (o.kind === "target") this.pulseSince.set(o.id, now);
        if (st && st.step_id === m.verdict.step_id) {
          // Our own step, ended by the person: no camera check ran, so none is claimed.
          const byYou = m.verdict.witnesses?.by_person === true;
          if (st.index === 0) this.byYou.clear();
          if (st.index === 0) this.doneSteps = [];
          if (byYou) this.byYou.add(st.step_id);
          this.doneSteps = [...this.doneSteps.filter((x) => x.n !== st.index + 1), { n: st.index + 1, text: st.text, page: ourStep(st) || st.page === undefined ? null : st.page, byYou }];
          this.l.passed = { index: st.index, until: now + PASSED_MS, byYou };
          this.d.vibrate?.(30);
          if (st.index + 1 === st.of) {
            this.toggleListen(false);
            const min = this.jobSince === null ? 0 : Math.round((now - this.jobSince) / 60_000);
            this.l.complete = { of: st.of, byYou: this.byYou.size, nextDue: null, steps: [...this.doneSteps].sort((a, b) => a.n - b.n), min };
            this.jobSince = null;
            this.doneSteps = [];
            this.byYou.clear();
            this.offerInstall();
            void this.offerFeedback();
            void this.loadNextDue(st.job_id);
          }
        }
      }
    }
    if (m.type === "plate_read") {
      // A settled read (nothing left to ask, or confirmed) moves on to adding the appliance.
      if (settled(m)) {
        this.l.plateChoices = null;
        // A failed read (no fields, no model) falls back to typing the model.
        const read = m.model !== null && m.fields.length > 0;
        this.l.add = {
          photoId: read ? m.photo_id : null,
          label: read ? [m.brand, m.model].filter(Boolean).join(" ") : null,
          appliance: null,
          error: null,
          busy: false,
          nextPollAt: 0,
          jobs: null,
          upload: null,
          thumb: null,
        };
      } else this.l.plateChoices = initialChoices(m);
    }
    if (m.type === "safety") {
      this.gate.wantsFresh = m.state.state === "HOLD" && m.state.precondition === "fresh_frame";
      if (m.state.state === "STOP") this.serverSawStop = true;
      else if (m.state.state === "OK" && this.serverSawStop) {
        // Code on the server cleared the stop it recorded: the phone's latch follows.
        this.serverSawStop = false;
        this.guard.serverCleared();
        this.l.guard = this.guard.state;
      }
      this.alarmOnTransition();
    }
  }

  private alarmOnTransition(): void {
    const serverStop = this.s.safety.state === "STOP";
    const stopped = serverStop || this.l.guard.kind === "stop";
    if (stopped && !this.wasStopped) {
      this.d.alarm.fire();
      this.speech.silence();
      this.l.pageUrl = null; // nothing sits over the stop
      this.l.noteOffered = true;
    } else if (!stopped && this.wasStopped) this.speech.resume();
    // The phone's own stop stays silent, since what the session says may not know of it yet. Once the session is in
    // STOP, G0 passes only rules-owned lines (the stop script, a warning), so what follows is heard; what was being
    // said before was cancelled above.
    if (serverStop && !this.wasServerStop) this.speech.resume();
    // A stop hides the controls, so the open microphone (whose sign is there) closes with it.
    if (stopped && this.listener) this.toggleListen(false);
    this.wasStopped = stopped;
    this.wasServerStop = serverStop;
  }

  // ---- taps -----------------------------------------------------------------------------------------------------

  private onClick(ev: Event): void {
    // A tap on a date button opens the platform's picker where the browser can (the input sits over the button too).
    const date = (ev.target as HTMLElement).closest?.(".date-pick")?.querySelector<HTMLInputElement>('input[type="date"]');
    if (date && "showPicker" in date) {
      try {
        date.showPicker();
      } catch {
        // Not from a user gesture, or not supported: the input itself still takes the tap.
      }
    }
    const el = (ev.target as HTMLElement).closest<HTMLElement>("[data-tap],[data-act]");
    if (!el) return;
    const tap = el.dataset.tap;
    if (tap === "confirm_gas_tap" || tap === "no" || tap === "yes" || tap === "done" || tap === "did_it") {
      // A step tap names the step on screen: the server refuses it for any other (a step that came while the finger moved).
      const step = (tap === "done" || tap === "did_it") && !this.l.complete ? this.s.step?.step_id : undefined;
      this.ws?.send(step ? { type: "tap", action: tap, step_id: step } : { type: "tap", action: tap });
      if (tap === "yes" && this.s.safety.state === "HOLD" && this.s.safety.precondition === "power_off_seen") this.l.yesUntil = this.d.now() + YES_WAIT_MS;
      if (tap === "did_it" && this.s.step) {
        this.l.waitStep = this.s.step.step_id;
        this.l.waitUntil = this.d.now() + DID_IT_WAIT_MS;
      }
      return;
    }
    switch (el.dataset.act) {
      case "accept-first-run":
        void this.acceptFirstRun();
        break;
      case "terms-close":
        el.closest("details")?.removeAttribute("open");
        break;
      case "consent":
        if (this.l.consent) this.l.consent.ticked = (el as HTMLInputElement).checked;
        if (this.l.consent?.ticked) this.l.joinError = null;
        break;
      case "another-make":
        void this.makeReturnLink();
        break;
      case "another-copy": {
        const an = this.l.another;
        if (an?.url)
          void this.d
            .copy(an.url)
            .then(() => void (an.copied = true))
            .catch(() => undefined);
        break;
      }
      case "delete-household":
        if (!this.l.deleteArmed) this.l.deleteArmed = true;
        else void this.deleteHousehold();
        break;
      case "type-model":
        this.l.add = { photoId: null, label: null, appliance: null, error: null, busy: false, nextPollAt: 0, jobs: null, upload: null, thumb: null };
        break;
      case "add-go":
        if (this.l.add)
          void this.postAppliance({
            alias: this.els.addName.value,
            photoId: this.l.add.photoId,
            model: this.l.add.photoId ? "" : this.els.addModel.value,
            brand: this.l.add.needBrand ? this.els.addBrand.value : "",
          });
        break;
      case "add-pdf":
        if (this.l.add?.appliance) void this.postAppliance({ alias: this.l.add.appliance.alias, model: this.l.add.appliance.model, pdfUrl: this.els.addPdf.value });
        break;
      case "add-start":
        if (this.l.add?.appliance) this.d.navigate(`?appliance=${encodeURIComponent(this.l.add.appliance.id)}`);
        break;
      case "add-job":
        if (this.l.add?.appliance && el.dataset.job) this.startJob(this.l.add.appliance.id, el.dataset.job);
        break;
      case "shared-pick": {
        const ap = this.l.home?.appliances.find((a) => a.id === el.dataset.appliance);
        if (!ap) break;
        this.l.add = { photoId: null, label: null, appliance: ap, error: null, busy: false, nextPollAt: 0, jobs: null, upload: null, thumb: null, existing: true };
        this.sendShared();
        break;
      }
      case "shared-new":
        // The file waits while the model is typed; it goes once the appliance exists.
        this.l.shared = null;
        this.l.add = { photoId: null, label: null, appliance: null, error: null, busy: false, nextPollAt: 0, jobs: null, upload: null, thumb: null };
        break;
      case "shared-drop":
        this.sharedFile = null;
        this.l.shared = null;
        break;
      case "add-close":
        // Closed before the appliance existed: back to choosing where the shared PDF goes.
        if (this.sharedFile) this.l.shared = { name: this.sharedFile.name || "manual.pdf" };
        if (this.l.add?.thumb?.src) this.d.objectUrl?.drop(this.l.add.thumb.src);
        this.l.add = null;
        break;
      case "start":
        void this.startLive();
        break;
      case "devices":
        this.openDevices(null);
        break;
      case "home-tab":
        this.l.landing = null;
        this.d.pushHash?.("");
        this.route("");
        break;
      case "settings":
        this.d.pushHash?.(SETTINGS_HASH.slice(1));
        this.route(SETTINGS_HASH);
        break;
      case "devices-open": {
        // Picked from "Something's wrong": its page opens at the code box.
        const wrong = this.l.devices?.wrong === true;
        if (el.dataset.appliance) this.openDevices(el.dataset.appliance);
        if (wrong && this.l.devices) this.l.devices.scroll = "fault";
        break;
      }
      case "wrong": {
        // Something's wrong: straight to the code box when the home has one appliance with a card, else pick one.
        const ready = (this.l.home?.appliances ?? []).filter((a) => a.card_status === "checked");
        this.openDevices(ready.length === 1 ? ready[0]!.id : null);
        if (this.l.devices) {
          if (ready.length === 1) this.l.devices.scroll = "fault";
          else this.l.devices.wrong = true;
        }
        break;
      }
      case "devices-back":
        this.openDevices(null);
        break;
      case "devices-close":
        this.l.devices = null;
        this.d.pushHash?.("");
        break;
      case "devices-add-open":
      case "devices-add-close":
        if (this.l.devices) this.l.devices.adding = el.dataset.act === "devices-add-open";
        break;
      case "devices-type":
        this.l.add = { photoId: null, label: null, appliance: null, error: null, busy: false, nextPollAt: 0, jobs: null, upload: null, thumb: null };
        break;
      case "devices-manual": {
        const ap = this.l.home?.appliances.find((a) => a.id === el.dataset.appliance);
        if (ap) this.l.add = { photoId: null, label: null, appliance: ap, error: null, busy: false, nextPollAt: 0, jobs: null, upload: null, thumb: null, existing: true };
        break;
      }
      case "report-open":
        if (this.l.devices) {
          this.l.devices.report = reportInitial(el.dataset.step ?? "");
          this.l.devices.done = null;
        }
        break;
      case "remove-ask":
      case "remove-cancel":
        if (this.l.devices && this.l.devices.remove !== "busy") {
          this.l.devices.remove = el.dataset.act === "remove-ask" ? "ask" : null;
          this.l.devices.removeError = null;
        }
        break;
      case "remove-go":
        void this.removeAppliance();
        break;
      case "report-cancel":
        if (this.l.devices) this.l.devices.report = null;
        break;
      case "report-send":
        void this.sendReport();
        break;
      case "fault-look":
        void this.lookFault();
        break;
      case "fault-pick":
        if (this.l.devices && el.dataset.code) {
          this.l.devices.fault.code = el.dataset.code;
          void this.lookFault();
        }
        break;
      case "view-page":
        if (el.dataset.appliance) void this.viewPage(el.dataset.appliance, Number(el.dataset.page) || 1);
        break;
      case "page-prev":
      case "page-next": {
        const v = this.l.viewer;
        if (v) void this.viewPage(v.applianceId, v.page + (el.dataset.act === "page-next" ? 1 : -1));
        break;
      }
      case "start-job":
        if (accessOf(this.l.folk) === "viewer") break;
        this.startJob(el.dataset.appliance ?? "", el.dataset.job ?? "");
        break;
      case "last-never":
      case "last-unknown":
      case "last-date": {
        const date = el.parentElement?.querySelector<HTMLInputElement>('input[type="date"]')?.value ?? "";
        const body = el.dataset.act === "last-never" ? { source: "never" } : el.dataset.act === "last-unknown" ? { source: "unknown" } : { source: "user", date };
        if (body.source === "user" && !date) break;
        void this.answerLastDone(el.dataset.appliance ?? "", el.dataset.job ?? "", body);
        break;
      }
      case "open-sheet":
        this.openSheet(el.dataset.appliance ?? "", el.dataset.job ?? "");
        break;
      case "home-done":
      case "home-later":
        // From home's next job: its sheet opens, marked done today (with Undo in view) or with Later's picks open.
        this.openSheet(el.dataset.appliance ?? "", el.dataset.job ?? "");
        if (this.l.sheet && el.dataset.act === "home-later") this.l.sheet.later = true;
        else if (this.l.sheet) this.sheetTap("sheet-done");
        break;
      case "landing-sheet": {
        // The reminder's key is <appliance_id>:<job_id>:<due_on>.
        const [aid, job] = this.l.landing?.key.split(":") ?? [];
        if (aid && job) {
          this.l.landing = null;
          this.openSheet(aid, job);
        }
        break;
      }
      case "people":
        this.d.pushHash?.(PEOPLE_HASH.slice(1));
        this.route(PEOPLE_HASH);
        break;
      case "save-timings":
        this.saveTimings();
        break;
      case "connections":
        this.d.pushHash?.(CONN_HASH.slice(1));
        this.route(CONN_HASH);
        break;
      case "conn-close":
        // Connections sits under Household: back to it.
        this.d.pushHash?.(PEOPLE_HASH.slice(1));
        this.route(PEOPLE_HASH);
        break;
      case "conn-feed-show":
      case "conn-feed-copy":
      case "conn-copy-address":
      case "conn-copy-key":
      case "conn-done":
      case "conn-new":
      case "conn-cancel":
      case "conn-remove":
      case "conn-remove-no":
      case "conn-remove-yes":
        this.connTap(el.dataset.act, el.dataset.conn ?? "");
        break;
      case "conn-make":
        void this.connMake();
        break;
      case "people-close":
        this.l.people = null;
        this.d.pushHash?.("");
        break;
      case "fb-open":
        this.l.fbOffer = false;
        this.d.navigate("?feedback");
        break;
      case "fb-later":
        this.l.fbOffer = false;
        break;
      case "fb-pick":
        this.fbPick(el.dataset.q ?? "", el.dataset.v ?? "");
        break;
      case "fb-send":
        void this.fbSend();
        break;
      case "fb-close":
        this.l.fb = null;
        this.d.pushHash?.("");
        break;
      case "people-rename":
      case "people-rename-save":
      case "people-rename-cancel":
      case "people-remove":
      case "people-leave":
      case "people-remove-yes":
      case "people-remove-no":
      case "invite-open":
      case "invite-cancel":
      case "invite-again":
      case "invite-make":
      case "invite-share":
      case "invite-copy":
      case "invite-mail":
      case "alerts-save":
        this.peopleTap(el.dataset.act, el.dataset.person ?? "");
        break;
      case "join-go":
        void this.joinPeople();
        break;
      case "sheet-close":
      case "sched-edit":
      case "sched-cancel":
      case "sched-save":
      case "sched-stop":
      case "sheet-done":
      case "sheet-done-date":
      case "sheet-done-ask":
      case "sheet-snooze":
      case "sheet-snooze-to":
      case "sheet-later":
      case "sheet-more":
      case "sheet-unsnooze":
      case "sheet-undo":
        this.sheetTap(el.dataset.act, el.dataset.date);
        break;
      case "hands-info":
        this.l.handsUntil = this.d.now() + HANDS_BANNER_MS;
        break;
      case "landing-dismiss":
        this.l.landing = null;
        break;
      case "landing-snooze":
        void this.snooze();
        break;
      case "finish":
        if (!this.params.has("replay")) this.ws?.end(); // the server ends the session, not only this screen
        this.d.navigate("");
        break;
      case "replay-again": {
        const id = this.params.get("replay");
        if (id) this.d.navigate(`?replay=${encodeURIComponent(id)}`);
        break;
      }
      case "install":
        void this.install();
        break;
      case "install-later":
        this.d.install?.done();
        this.l.install = null;
        break;
      case "install-copy":
        if (this.l.install) {
          const inst = this.l.install;
          void this.d.copy(inst.url).then(() => void (inst.phase = "copied")).catch(() => undefined);
        }
        break;
      case "share-done": {
        const nav = this.d.doc.defaultView?.navigator;
        if (nav?.share && this.l.complete) void nav.share({ title: tr(this.l.lang, "Job done"), text: shareText(this.l.complete.of, this.l.complete.byYou, this.l.lang) }).catch(() => undefined);
        break;
      }
      case "plate-retake":
        this.l.plateChoices = null;
        this.s.plate = null;
        break;
      case "plate-choice": {
        const c = this.l.plateChoices;
        const f = el.dataset.field!;
        if (c?.[f]) c[f]![Number(el.dataset.index)] = el.dataset.option!;
        break;
      }
      case "confirm-plate":
        if (this.l.plateChoices) {
          this.ws?.send({ type: "tap", action: "confirm_plate", arg: confirmArg(this.l.plateChoices) });
          this.l.plateChoices = null;
        }
        break;
      case "note":
        void this.shareNote();
        break;
      case "answer-clear":
        this.s.answer = null;
        break;
      case "job-ready": {
        const pv = this.s.preview;
        if (!pv) break;
        this.ws?.send({ type: "job_ready", job_id: pv.job_id, ready: pv.needs.map((n) => n.id).filter((id) => this.l.ticked.includes(id)) });
        this.s.preview = null;
        // A line held while the preview was put together belongs to the preview: the job starts without its bar.
        this.s.held = null;
        this.s.heldCount = 0;
        this.l.ticked = [];
        this.toCamera?.();
        break;
      }
      case "listen":
        this.toggleListen();
        break;
      case "tuck":
        // Under a hold the arrow tucks the step's words for that hold; otherwise it is camera only.
        if (this.els.tuck.dataset.mode === "hold") this.l.clearHold = !this.l.clearHold;
        else this.toggleClear();
        break;
      case "unclear":
        this.toggleClear();
        break;
      case "show-page":
        if (this.s.step && this.s.sessionId && !ourStep(this.s.step))
          this.l.pageUrl = `${API}/sessions/${encodeURIComponent(this.s.sessionId)}/pages/${this.s.step.page}.png`;
        break;
      case "close-page":
        this.l.pageUrl = null;
        this.closeViewer();
        break;
    }
  }

  private async onChange(ev: Event): Promise<void> {
    const input = ev.target as HTMLInputElement;
    if (input.dataset.act === "lang") {
      if (input.value !== this.d.lang.slice(0, 2)) this.d.setLang?.(input.value);
      return;
    }
    if (input.dataset.act === "need") {
      const rest = this.l.ticked.filter((id) => id !== input.value);
      this.l.ticked = input.checked ? [...rest, input.value] : rest;
      return;
    }
    if (input.dataset.act === "add-file") {
      const file = input.files?.[0];
      input.value = ""; // the same file can be picked again after a refusal
      if (file) await this.sendPdf(file);
      return;
    }
    if (input.dataset.act !== "plate-photo" || !input.files?.[0]) return;
    const bmp = await createImageBitmap(input.files[0]);
    const s = LONG_EDGE / Math.max(bmp.width, bmp.height);
    const jpeg = await this.d.jpeg(bmp, Math.round(bmp.width * Math.min(1, s * 2)), Math.round(bmp.height * Math.min(1, s * 2)));
    bmp.close();
    this.ws?.send({ type: "plate_photo", photo_id: `plate-${Date.now().toString(36)}`, jpeg });
    input.value = "";
  }

  private recorder: RecorderLike | null = null;
  private listener: ListenerLike | null = null;

  /**
   * Hands-free talk: off until the person turns it on, and off again when the job ends. Each turn the microphone hears
   * goes up as an utterance, as a held press would; nothing is taken while the phone itself speaks.
   */
  private toggleListen(on = !this.listener): void {
    if (!on) {
      this.listener?.stop();
      this.listener = null;
      this.l.listening = false;
      return;
    }
    if (this.listener || !this.d.listener || !this.l.live) return;
    const li = this.d.listener();
    this.listener = li;
    this.l.listening = true;
    this.l.talkError = null;
    const send = (wav: string): void => {
      if (this.listener !== li) return;
      asked(this.s);
      this.ws?.send({ type: "utterance", utterance_id: `u-${Date.now().toString(36)}`, text: null, audio_wav_b64: wav, lang: this.d.lang });
    };
    li.start(send, () => this.d.synth?.speaking?.() ?? false).catch((e: unknown) => {
      this.l.talkError = micErrorText(e, this.l.lang);
      if (this.listener === li) this.toggleListen(false);
    });
  }

  /**
   * Hold to talk, on pointer events alone (mouse, touch and pen). The press is claimed at once: no scroll, text
   * selection or long-press menu (with touch-action: none on the button), and the pointer is captured so the release
   * reaches the button wherever the finger ends. The recorder is made inside the press; a release that comes while
   * the permission prompt is still up still stops it.
   */
  private onTalk(ev: Event, down: boolean): void {
    if (down) {
      const el = (ev.target as HTMLElement).closest?.<HTMLElement>('[data-act="talk"]');
      if (!el) return;
      ev.preventDefault();
      const id = (ev as PointerEvent).pointerId;
      if (id !== undefined) el.setPointerCapture?.(id);
      if (this.recorder) return;
      const r = this.d.recorder();
      this.recorder = r;
      this.l.talking = true;
      this.l.talkSince = this.d.now();
      this.l.talkError = null;
      r.start().catch((e: unknown) => {
        this.l.talkError = micErrorText(e, this.l.lang);
        if (this.recorder === r) {
          this.recorder = null;
          this.l.talking = false;
        }
        void r.stop().catch(() => undefined);
      });
    } else if (this.recorder) {
      const r = this.recorder;
      this.recorder = null;
      this.l.talking = false;
      void r
        .stop()
        .then((wav) => {
          if (wav) asked(this.s);
          if (wav) this.ws?.send({ type: "utterance", utterance_id: `u-${Date.now().toString(36)}`, text: null, audio_wav_b64: wav, lang: this.d.lang });
        })
        .catch(() => undefined);
    }
  }

  /** The technician note: built on the server, sent by the phone's own share sheet. Nothing leaves until the user
   * sends it. */
  /** About > Open on another phone: POST /household/return-link. The link is shown here only, never stored. */
  private async makeReturnLink(): Promise<void> {
    if (this.l.another?.busy) return;
    const an = { busy: true, url: null, until: null, copied: false, error: null } as NonNullable<LocalState["another"]>;
    this.l.another = an;
    try {
      const r = await this.api(`${API}/household/return-link`, { method: "POST" });
      if (r.ok) {
        const b = (await r.json()) as { url?: unknown; expires_at?: unknown };
        an.url = typeof b.url === "string" && /^https?:\/\//.test(b.url) ? b.url : null;
        const at = typeof b.expires_at === "string" ? new Date(b.expires_at) : null;
        an.until = at && !Number.isNaN(at.getTime()) ? at.toLocaleTimeString(this.l.lang, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }) : null;
        if (!an.url) an.error = tr(this.l.lang, RETURN_LINK_TEXT.failed);
      } else an.error = tr(this.l.lang, r.status === 403 ? RETURN_LINK_TEXT.forbidden : RETURN_LINK_TEXT.failed);
    } catch {
      an.error = tr(this.l.lang, RETURN_LINK_TEXT.failed);
    } finally {
      an.busy = false;
    }
  }

  /** The door's screen: the first-run facts, and the consent box to tick before the household opens. */
  private showDoor(door: Door): void {
    this.door = door;
    this.askConsent();
    this.l.doorShut = false;
    if (door.kind === "open") void this.readDoor(door);
  }

  /** The first-run screen with the consent box, unticked. */
  private askConsent(): void {
    const line = this.d.appConsent;
    this.l.consent = { text: line?.text ?? APP_CONSENT_PLACEHOLDER, status: line?.status ?? null, heading: line?.heading ?? null, body: line?.body ?? [], ticked: false };
    this.l.firstRun = disclaimer(this.d.disclaimers, "first_run", this.d.lang) ?? "";
  }

  /** The open door asks the server first: a shut or capped door says so and offers the replay, with no consent asked
   * for a home that couldn't open. An unreadable answer leaves the door asking as usual. */
  private async readDoor(door: Door): Promise<void> {
    const st = await doorState(this.d.fetch, API);
    if (this.door !== door || st === null || st.state === "open") return;
    this.l.doorShut = true;
    this.l.joinError =
      this.lostDoor === door ? tr(this.d.lang, JOIN_TEXT.lostShut) : st.state === "shut" ? tr(this.d.lang, JOIN_TEXT.door_closed) : doorCappedText(st.reopensAt, new Date(), this.d.lang);
    this.l.doorReplay = true;
  }

  /** A return link from the household's other phone: the same household on this one, with no consent asked again. */
  private async comeBack(door: Door): Promise<void> {
    this.joining = true;
    const r = await joinHousehold(this.d.fetch, API, door, this.d.lang);
    this.joining = false;
    if ("token" in r) {
      this.signedIn(r.token);
      this.d.markFirstRun();
      void this.loadHome();
      return;
    }
    // The link didn't work but the phone still has a home: it stays (a dead token shows the door on its own 401).
    if (this.d.household.get() !== null) return;
    this.showDoor({ kind: "open" });
    this.joinFailed(r, door);
  }

  /** The door said no. A shut door or a cap leaves the replay, so the door offers it beside the reason. */
  private joinFailed(r: { error: JoinError } | { cap: Cap }, door: Door): void {
    this.l.joinError =
      "cap" in r
        ? joinCapText(r.cap, new Date(), this.d.lang)
        : tr(this.d.lang, door.kind === "try" && r.error === "gone" ? JOIN_TEXT.tryGone : JOIN_TEXT[r.error]);
    this.l.doorReplay = "cap" in r || r.error === "door_closed";
  }

  private signedIn(token: string): void {
    const before = this.d.household.get();
    this.d.household.set(token);
    void this.d.followHomeLang?.();
    void this.d.pushMove?.(before, token).catch(() => undefined);
    this.l.ha.token = token;
    this.door = null;
    this.l.consent = null;
    this.l.joinError = null;
    this.l.doorReplay = false;
    this.l.doorShut = false;
    this.l.firstRun = null;
  }

  /** The first-run tap: the user's consent (18+, the AI line), which opens the household through its door. From the
   * open door or a judges' link the camera comes next, to read the first appliance's plate. */
  private async acceptFirstRun(): Promise<void> {
    if (this.joining) return;
    const door = this.door;
    if (door !== null) {
      if (!this.l.consent?.ticked) {
        this.l.joinError = tr(this.d.lang, JOIN_TEXT.consent);
        return;
      }
      this.joining = true;
      const r = await joinHousehold(this.d.fetch, API, door, this.d.lang);
      this.joining = false;
      if (!("token" in r)) {
        this.joinFailed(r, door);
        return;
      }
      this.signedIn(r.token);
      this.d.markFirstRun();
      void this.loadHome();
      // From the open door or a judges' link the camera opens at once. A name is asked only at the first invite.
      if (door.kind === "open" || door.kind === "try") void this.startLive();
      return;
    }
    // A people invite's consent: ticked before the name is asked; the join itself sends it.
    if (this.l.join && this.l.consent && !this.l.consent.ticked) {
      this.l.joinError = tr(this.d.lang, JOIN_TEXT.consent);
      return;
    }
    this.l.joinError = null;
    this.l.firstRun = null;
    this.d.markFirstRun();
  }

  /** The server refused the hello (close 1008). Its notice, when it sent one, says why; without one, a gone token
   * is the likely cause, so GET /household finds out (a 401 drops the token and shows the join screen). */
  private async onRefused(): Promise<void> {
    this.l.slow = false;
    if (this.noticeBeforeOk) return;
    const r = await this.api(`${API}/household`).catch(() => null);
    // A view-only person reads the home but runs no repair: picking the appliance again would only loop.
    if (r?.status !== 401) this.l.refused = tr(this.l.lang, accessOf(this.l.folk) === "viewer" ? PEOPLE_TEXT.viewerSession : REFUSED_TEXT);
  }

  /** POST /appliances; a 201 starts the polling, a 409/422 says why in the phone's words. */
  private async postAppliance(i: AddInput): Promise<void> {
    const a = this.l.add;
    if (!a || a.busy) return;
    if (this.d.household.get() === null) {
      a.error = tr(this.l.lang, NO_HOUSEHOLD_TEXT);
      return;
    }
    a.busy = true;
    a.error = null;
    try {
      const r = await this.api(`${API}/appliances`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(applianceBody(i)),
      });
      if (r.ok) {
        a.appliance = (await r.json()) as Appliance;
        a.nextPollAt = this.d.now() + POLL_MS;
        this.sendShared();
      } else {
        const body = (await r.json().catch(() => ({}))) as { error?: string };
        // A model code that could be a maker's who needs naming: ask for the brand, then Add sends both.
        if (body.error === "brand_needed") a.needBrand = true;
        a.error = postError(r.status, body.error, this.l.lang);
      }
    } catch {
      a.error = postError(0, undefined, this.l.lang);
    } finally {
      a.busy = false;
    }
  }

  /** The manual's PDF from the phone: checked here (50 MB, starts %PDF) before a byte is sent, then POSTed raw to
   * /appliances/{id}/manual with the share sent on screen. A 201 restarts the ingest, and the polling with it. */
  private async sendPdf(file: Blob): Promise<void> {
    const a = this.l.add;
    const ap = a?.appliance;
    if (!a || !ap || a.upload) return;
    const problem = pdfProblem(file.size, await firstBytes(file, 4));
    if (problem) {
      a.error = tr(this.l.lang, PDF_TEXT[problem]);
      return;
    }
    a.error = null;
    a.upload = { sent: 0 };
    const url = `${API}/appliances/${encodeURIComponent(ap.id)}/manual`;
    const token = this.d.household.get();
    const headers: Record<string, string> = { "content-type": "application/pdf" };
    if (token) headers.authorization = `Bearer ${token}`;
    try {
      const sent = (f: number): void => {
        if (a.upload) a.upload.sent = Math.min(1, Math.max(a.upload.sent, f));
      };
      const r = this.lost(
        this.d.upload ? await this.d.upload(url, file, headers, sent) : await this.d.fetch(url, { method: "POST", headers, body: file }),
      );
      if (r.ok) {
        a.appliance = (await r.json()) as Appliance;
        a.jobs = null;
        a.nextPollAt = this.d.now() + POLL_MS;
      } else {
        const body = (await r.json().catch(() => ({}))) as { error?: string };
        a.error = uploadError(r.status, body.error, this.l.lang);
      }
    } catch {
      a.error = uploadError(0, undefined, this.l.lang);
    } finally {
      a.upload = null;
    }
  }

  /** The manual's first page as soon as the ingest has it, fetched with the bearer (an <img> can't send one). Tried
   * once per thumb_url; an empty src is a miss and shows nothing. */
  private loadThumb(): void {
    const a = this.l.add;
    const u = a?.appliance?.ingest?.manual?.thumb_url;
    const ou = this.d.objectUrl;
    if (!a || !u || !ou || a.thumb?.url === u || !u.startsWith(`${API}/`)) return;
    const old = a.thumb?.src;
    a.thumb = { url: u, src: "" };
    void this.api(u)
      .then(async (r) => {
        if (!r.ok) return;
        const src = ou.make(await r.blob());
        if (this.l.add !== a || a.thumb?.url !== u) return ou.drop(src);
        a.thumb = { url: u, src };
      })
      .catch(() => undefined)
      .finally(() => old && ou.drop(old));
  }

  /** GET /appliances/{id} every 2 s while the page is visible, until checked, pages_only or failed. */
  private pollAppliance(now: number): void {
    const a = this.l.add;
    const ap = a?.appliance;
    if (!a || !ap || a.busy || a.upload || FINAL.includes(ap.card_status) || now < a.nextPollAt) return;
    if (this.d.doc.visibilityState === "hidden") return;
    a.busy = true;
    a.nextPollAt = now + POLL_MS;
    void this.api(`${API}/appliances/${encodeURIComponent(ap.id)}`)
      .then(async (r) => {
        // An upload started meanwhile answers with the restarted ingest; this older one would undo it.
        if (r.ok && this.l.add === a && !a.upload) a.appliance = (await r.json()) as Appliance;
      })
      .catch(() => undefined)
      .finally(() => (a.busy = false));
  }

  /** Once the card is checked, GET its card for the jobs to start; a job's title is the card's fixed text. */
  private loadJobs(): void {
    const a = this.l.add;
    const ap = a?.appliance;
    if (!a || !ap || a.busy || a.jobs !== null || ap.card_status !== "checked") return;
    a.busy = true;
    void this.api(`${API}/appliances/${encodeURIComponent(ap.id)}/card`)
      .then(async (r) => {
        const card = r.ok ? ((await r.json()) as { jobs?: { id: string; title: Record<string, string> }[] }) : {};
        const l = this.d.lang.slice(0, 2).toLowerCase();
        a.jobs = (card.jobs ?? []).map((j) => ({ id: j.id, title: j.title[l] ?? j.title.en ?? j.id }));
      })
      .catch(() => (a.jobs = []))
      .finally(() => (a.busy = false));
  }

  /** Home offers the curated recording once there is one (the server 404s golden until then). */
  private async probeGolden(): Promise<void> {
    this.l.golden = (await this.d.golden?.().catch(() => false)) === true;
  }

  /** The device's timings since the last send, into the session record (numbers and frame ids only). Kept for the
   * next send while the socket is down. */
  sendTimings(): void {
    this.timingsAt = this.d.now();
    if (!this.ws?.connected) return;
    const t = this.metrics.drain();
    if (t) this.ws.send({ type: "timings", ...t });
  }

  /** ?debug=1: this run's timings as a JSON file: frame ids and milliseconds only, no frames and no token. */
  saveTimings(): void {
    const doc = this.d.doc;
    const url = this.d.objectUrl;
    if (!url) return;
    const at = new Date().toISOString();
    const run = { at, session_id: this.s.sessionId, user_agent: doc.defaultView?.navigator.userAgent ?? null, ...this.metrics.snapshot(this.gate.sent, this.gate.held) };
    const href = url.make(new Blob([JSON.stringify(run, null, 1)], { type: "application/json" }));
    const a = doc.createElement("a");
    a.href = href;
    a.download = `fixit-timings-${at.replace(/[:.]/g, "-")}.json`;
    doc.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => url.drop(href), 1000);
  }

  // ---- connections ---------------------------------------------------------------------------------------------

  /** The Connections screen's reads: the caller's own calendar address, and the home's keys (an admin's only: anyone
   * else gets 403, and the keys' part of the screen stays away). */
  private async loadConn(): Promise<void> {
    const st = this.l.conn;
    if (!st) return;
    const [rf, rt] = await Promise.all([this.api(`${API}/household/feeds`).catch(() => null), this.api(`${API}/household/tokens`).catch(() => null)]);
    if (this.l.conn !== st) return;
    const feed = rf?.ok ? ((await rf.json().catch(() => null)) as { ics_url?: unknown } | null)?.ics_url : null;
    st.feed = typeof feed === "string" && /^https?:\/\//.test(feed) ? feed : null;
    st.tokens = rt?.ok ? parseConnections(await rt.json().catch(() => null)) : null;
    if (rt?.ok && st.tokens === null) st.error = CONN_TEXT.failed;
    else if (rt && !rt.ok && rt.status !== 403 && rt.status !== 404) st.error = CONN_TEXT.failed;
    st.loaded = true;
  }

  /** One write on the Connections screen; a refusal says why. The answer, or null on a refusal. */
  private async connWrite(method: string, path: string, body?: unknown): Promise<{ status: number; json: unknown } | null> {
    const st = this.l.conn;
    if (!st || st.busy) return null;
    st.busy = true;
    st.error = null;
    try {
      const r = await this.api(`${API}${path}`, {
        method,
        headers: body === undefined ? undefined : { "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const json = r.status === 204 ? null : await r.json().catch(() => null);
      // A key already gone is what Remove wanted.
      if (!r.ok && !(method === "DELETE" && r.status === 404)) {
        st.error = connError(r.status);
        return null;
      }
      return { status: r.status, json };
    } catch {
      st.error = CONN_TEXT.failed;
      return null;
    } finally {
      st.busy = false;
    }
  }

  /** The home's keys again, after a change. */
  private async reloadKeys(): Promise<void> {
    const st = this.l.conn;
    const r = await this.api(`${API}/household/tokens`).catch(() => null);
    if (!st || this.l.conn !== st) return;
    const got = r?.ok ? parseConnections(await r.json().catch(() => null)) : null;
    if (got) st.tokens = got;
  }

  private copied(what: "feed" | "key" | "address", text: string): void {
    const st = this.l.conn;
    if (st) void this.d.copy(text).then(() => void (st.copied = what)).catch(() => undefined);
  }

  private connTap(act: string, id: string): void {
    const st = this.l.conn;
    if (!st) return;
    switch (act) {
      case "conn-feed-show":
        st.feedShown = true;
        break;
      case "conn-feed-copy":
        if (st.feed) this.copied("feed", st.feed);
        break;
      case "conn-copy-address":
        if (st.made) this.copied("address", st.made.address);
        break;
      case "conn-copy-key":
        if (st.made) this.copied("key", st.made.token);
        break;
      case "conn-done":
        // The key leaves the phone's memory here: it is never shown again.
        st.made = null;
        st.copied = null;
        break;
      case "conn-new":
        st.draft = connDraft("ha", this.l.lang);
        st.confirm = null;
        st.error = null;
        break;
      case "conn-cancel":
        st.draft = null;
        break;
      case "conn-remove":
        st.confirm = id;
        st.error = null;
        break;
      case "conn-remove-no":
        st.confirm = null;
        break;
      case "conn-remove-yes": {
        const tid = st.confirm;
        if (!tid) break;
        void this.connWrite("DELETE", `/household/tokens/${encodeURIComponent(tid)}`).then((r) => {
          if (!r) return;
          st.confirm = null;
          if (st.tokens) st.tokens = st.tokens.filter((t) => t.id !== tid);
          void this.reloadKeys();
        });
        break;
      }
    }
  }

  /** POST /household/tokens: the key is shown once, with where it goes. */
  private async connMake(): Promise<void> {
    const st = this.l.conn;
    const d = st?.draft;
    if (!st || !d || d.busy) return;
    const got = connBody(d);
    if ("error" in got) {
      d.error = got.error;
      return;
    }
    d.busy = true;
    d.error = null;
    const r = await this.connWrite("POST", "/household/tokens", got.body);
    d.busy = false;
    const made = r ? parseMade(r.json) : null;
    if (!made) {
      d.error = st.error ?? CONN_TEXT.failed;
      st.error = null;
      return;
    }
    st.made = { kind: d.kind, label: got.body.label, token: made.token, address: connAddress(d.kind, this.d.origin) };
    st.copied = null;
    st.draft = null;
    await this.reloadKeys();
  }

  /** A field on the Connections screen: the name is kept as typed; a choice redraws. */
  private connField(input: HTMLInputElement, changed: boolean): void {
    const d = this.l.conn?.draft;
    if (!d) return;
    const f = input.dataset.cf;
    if (f === "label") {
      d.label = input.value;
      d.labelSet = true;
      d.error = null;
      return;
    }
    if (!changed || !input.checked) return;
    if (f === "kind") {
      const kind = input.value as ConnKind;
      const next = connDraft(kind, this.l.lang);
      d.kind = kind;
      if (!d.scopeSet) d.scope = next.scope;
      if (!d.labelSet) d.label = next.label;
    } else if (f === "scope") {
      d.scope = input.value as Scope;
      d.scopeSet = true;
    }
  }

  // ---- the feedback form -------------------------------------------------------------------------------------

  /** Whether the server counts this home in the pilot: only it gets the form, never a door or a judges' home. */
  private async pilot(): Promise<boolean> {
    if (this.d.household.get() === null) return false;
    const r = await this.api(`${API}/household`).catch(() => null);
    return !!r?.ok && isPilot(await r.json().catch(() => null));
  }

  /** A live job's end, never a replay or a recorded run: Job done asks what the person thought, in a pilot home. */
  private async offerFeedback(): Promise<void> {
    if (this.s.mode === "replay" || this.d.doc.defaultView?.location.pathname.startsWith("/watch")) return;
    if (await this.pilot()) this.l.fbOffer = true;
  }

  /** #feedback: the form, once the server says this is a pilot home; anywhere else the address leads home. */
  private async openFeedback(): Promise<void> {
    if (!(await this.pilot())) {
      this.d.pushHash?.("");
      return;
    }
    if (!this.l.fb) this.l.fb = fbInitial(this.d.lang);
  }

  /** One answer per tick-box question; the chosen one tapped again is taken back. */
  private fbPick(q: string, v: string): void {
    const f = this.l.fb;
    if (!f || f.busy || !(q in CHOICES) || !CHOICES[q as Choice].includes(v)) return;
    f.choices[q as Choice] = f.choices[q as Choice] === v ? null : v;
    f.error = null;
  }

  /** Typed answers are kept exactly as typed; the quote choice redraws for its name field. */
  private fbField(input: HTMLInputElement): void {
    const f = this.l.fb;
    if (!f) return;
    const k = input.dataset.fb!;
    if (k === "quote") {
      if (input.checked) f.quote = input.value as typeof f.quote;
    } else if (k === "quote_name") f.quoteName = input.value;
    else if (k in f.written) f.written[k as Written] = input.value;
    f.error = null;
  }

  private async fbSend(): Promise<void> {
    const f = this.l.fb;
    if (!f || f.busy) return;
    const b = fbBody(f);
    if ("error" in b) {
      f.error = b.error;
      return;
    }
    f.busy = true;
    f.error = null;
    try {
      const r = await this.api(`${API}/household/feedback`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(b.body),
      }).catch(() => null);
      if (r?.ok) f.sent = true;
      else f.error = fbError(r?.status ?? 0, f.lang);
    } finally {
      f.busy = false;
    }
  }

  // ---- people in the home -------------------------------------------------------------------------------------

  /** #join/<code>: taken out of the address at once and kept only in memory, then the join screen. A phone with no
   * home sees the first-run facts and ticks the consent before it gives its name. The code, or null for any other
   * address. */
  private openJoin(hash: string): string | null {
    const code = joinCode(hash);
    if (!code) return null;
    this.d.clearDoor?.();
    const has = this.d.household.get() !== null;
    this.l.join = { kind: "join", code, name: "", busy: false, error: null, switching: has };
    this.l.people = null;
    this.l.conn = null;
    this.l.sheet = null;
    this.l.devices = null;
    if (!has) {
      this.askConsent();
    }
    return code;
  }

  /** GET /household/people. A server without people (404), or no answer, leaves folk null and the app as it was. */
  private async loadFolk(): Promise<void> {
    const r = await this.api(`${API}/household/people`).catch(() => null);
    this.l.folk = r?.ok ? parsePeople(await r.json().catch(() => null)) : null;
  }

  /** The People screen shown again (its tab, the app back in front, or every PEOPLE_POLL_MS while it is open): the
   * people read afresh, so someone who joined meanwhile is listed. An open rename or removal is left as it is. */
  private async refreshPeople(): Promise<void> {
    const st = this.l.people;
    if (!st || st.people === null || st.busy || st.rename || st.confirm) return;
    await this.loadFolk();
    if (this.l.people !== st || this.l.folk === null) return;
    st.people = this.l.folk;
  }

  /** The People screen's two reads: the people, and the caller's own alerts (absent until the server has them). */
  private async loadPeople(): Promise<void> {
    const st = this.l.people;
    if (!st) return;
    const [, ra] = await Promise.all([this.loadFolk(), this.api(`${API}/me/alerts`).catch(() => null)]);
    if (this.l.people !== st) return;
    st.people = this.l.folk;
    if (this.l.folk === null) st.error = PEOPLE_TEXT.failed;
    const alerts = ra?.ok ? parseAlerts(await ra.json().catch(() => null)) : null;
    st.alerts = alerts;
    st.draft = alerts && { ...alerts, follows: [...alerts.follows], quiet: alerts.quiet && { ...alerts.quiet } };
    const poll = (): void => {
      if (this.l.people !== st) return;
      if (this.d.doc.visibilityState !== "hidden") void this.refreshPeople();
      setTimeout(poll, PEOPLE_POLL_MS);
    };
    setTimeout(poll, PEOPLE_POLL_MS);
  }

  /** One write on the People screen; a refusal says why in plain words. The answer's JSON, or null on a refusal. */
  private async peopleWrite(method: string, path: string, body?: unknown): Promise<{ status: number; json: unknown } | null> {
    const st = this.l.people;
    if (!st || st.busy) return null;
    st.busy = true;
    st.error = null;
    st.note = null;
    try {
      const r = await this.api(`${API}${path}`, {
        method,
        headers: body === undefined ? undefined : { "content-type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const json = await r.json().catch(() => null);
      if (!r.ok) {
        const e = (json ?? {}) as { error?: unknown; detail?: unknown };
        st.error = peopleError(r.status, typeof e.error === "string" ? e.error : typeof e.detail === "string" ? e.detail : undefined);
        return null;
      }
      return { status: r.status, json };
    } catch {
      st.error = PEOPLE_TEXT.failed;
      return null;
    } finally {
      st.busy = false;
    }
  }

  private peopleTap(act: string, id: string): void {
    const st = this.l.people;
    if (!st) return;
    const who = this.l.folk?.find((p) => p.id === id) ?? null;
    switch (act) {
      case "people-rename":
        if (who) st.rename = { id: who.id, name: who.name };
        st.confirm = null;
        break;
      case "people-rename-cancel":
        st.rename = null;
        break;
      case "people-rename-save": {
        const rn = st.rename;
        if (!rn) break;
        const bad = nameError(rn.name);
        if (bad) {
          st.error = bad;
          break;
        }
        void this.peopleWrite("PATCH", `/household/people/${encodeURIComponent(rn.id)}`, { name: rn.name.trim() }).then((r) => {
          if (!r) return;
          st.rename = null;
          void this.afterPeopleWrite();
        });
        break;
      }
      case "people-remove":
      case "people-leave":
        if (who) st.confirm = { id: who.id, kind: act === "people-leave" ? "leave" : "remove" };
        st.rename = null;
        break;
      case "people-remove-no":
        st.confirm = null;
        break;
      case "people-remove-yes": {
        const c = st.confirm;
        if (!c) break;
        void this.peopleWrite("DELETE", `/household/people/${encodeURIComponent(c.id)}`).then((r) => {
          st.confirm = null;
          if (!r) return;
          // Leaving: this phone's token stopped at once, so it forgets the home and goes back to the start.
          if (c.kind === "leave") {
            this.d.household.set(null);
            this.d.navigate("?start");
            return;
          }
          void this.afterPeopleWrite();
        });
        break;
      }
      case "invite-open":
      case "invite-again":
        st.invite = inviteDraft((st.people ?? this.l.folk)?.find((p) => p.me));
        break;
      case "invite-cancel":
        st.invite = null;
        break;
      case "invite-make": {
        const inv = st.invite;
        if (!inv || inv.busy) break;
        const got = inviteBody(inv);
        if ("error" in got) {
          inv.error = got.error;
          break;
        }
        const myName = inv.myName?.trim() ?? null;
        const me = (st.people ?? this.l.folk)?.find((p) => p.me);
        if (myName !== null) {
          const bad = nameError(myName);
          if (bad) {
            inv.error = bad;
            break;
          }
        }
        inv.busy = true;
        inv.error = null;
        // The first invite names its sender first, so the invite goes out from a name the others know.
        const named = myName !== null && me ? this.peopleWrite("PATCH", `/household/people/${encodeURIComponent(me.id)}`, { name: myName }) : Promise.resolve({ status: 200, json: null });
        void named.then((n) => {
          if (!n) return null;
          if (inv.myName !== null) {
            inv.myName = null;
            void this.afterPeopleWrite();
          }
          return this.peopleWrite("POST", "/household/invites", got.body);
        }).then((r) => {
          inv.busy = false;
          // The phone's own refusal line goes on the invite, where the tap was.
          if (!r) {
            inv.error = st.error;
            st.error = null;
            return;
          }
          inv.made = parseInvite(r.json);
          if (!inv.made) inv.error = PEOPLE_TEXT.failed;
        });
        break;
      }
      case "invite-share": {
        const made = st.invite?.made;
        const nav = this.d.doc.defaultView?.navigator;
        if (!made) break;
        // The link alone: no home name or role travels with it, and the share sheet picks the person.
        if (nav?.share) void nav.share({ title: inviteShareTitle(this.l.lang), url: made.join_url }).catch(() => undefined);
        else this.peopleTap("invite-copy", "");
        break;
      }
      case "invite-copy": {
        const inv = st.invite;
        if (inv?.made) void this.d.copy(inv.made.join_url).then(() => void (inv.copied = true)).catch(() => undefined);
        break;
      }
      case "invite-mail": {
        const made = st.invite?.made;
        if (!made?.email) break;
        // The server sends no mail: the phone's own mail app does, from the person's own address.
        const a = this.d.doc.createElement("a");
        a.href = inviteMail(made, null, this.l.lang);
        a.click();
        break;
      }
      case "alerts-save": {
        if (!st.draft) break;
        const got = alertsBody(st.draft);
        st.note = null;
        if ("error" in got) {
          st.alertsError = got.error;
          break;
        }
        st.alertsError = null;
        void this.peopleWrite("PUT", "/me/alerts", got.body).then((r) => {
          const a = r ? parseAlerts(r.json) : null;
          // The refusal goes by the alerts' Save, where the tap was.
          if (!r) {
            st.alertsError = st.error;
            st.error = null;
            return;
          }
          if (a) {
            st.alerts = a;
            st.draft = { ...a, follows: [...a.follows], quiet: a.quiet && { ...a.quiet } };
          }
          st.note = PEOPLE_TEXT.saved;
        });
        break;
      }
    }
  }

  /** People changed: the list again, and home, whose rows name who does each job. */
  private async afterPeopleWrite(): Promise<void> {
    await this.loadHome();
    if (this.l.people) this.l.people.people = this.l.folk;
  }

  /** A field on the People or join screen. Typing is kept without a redraw; a choice (role, how, what) on change. */
  private peopleField(input: HTMLInputElement, changed: boolean): void {
    const f = input.dataset.pf;
    if (f === "join-name") {
      if (this.l.join) this.l.join.name = input.value;
      return;
    }
    const st = this.l.people;
    if (!st) return;
    if (f === "rename") {
      if (st.rename) st.rename.name = input.value;
      return;
    }
    if (f === "invite-myname") {
      if (st.invite) st.invite.myName = input.value;
      return;
    }
    if (f === "invite-email") {
      if (st.invite) st.invite.email = input.value;
      return;
    }
    if (!changed) return;
    if (f === "invite-role") {
      if (st.invite) st.invite.role = input.value as RoleChoice;
      return;
    }
    if (f === "role") {
      const id = input.dataset.person ?? "";
      void this.peopleWrite("PATCH", `/household/people/${encodeURIComponent(id)}`, roleBody(input.value as RoleChoice)).then((r) => {
        // A refusal redraws with its line, which puts the select back to the role the person still has.
        if (r) void this.afterPeopleWrite();
      });
      return;
    }
    const d = st.draft;
    if (!d) return;
    if (f === "push" || f === "calendar" || f === "hub") d[f] = input.checked;
    else if (f === "items" && input.checked) d.items = input.value === "chosen" ? "chosen" : "all";
    else if (f === "follow") d.follows = input.checked ? [...new Set([...d.follows, input.value])] : d.follows.filter((x) => x !== input.value);
    else if (f === "quiet") d.quiet = input.checked ? (st.alerts?.quiet ?? { from: "22:00", to: "07:00" }) : null;
    else if ((f === "quiet-from" || f === "quiet-to") && d.quiet) d.quiet = { ...d.quiet, [f === "quiet-from" ? "from" : "to"]: input.value };
    st.note = null;
    st.alertsError = null;
  }

  /** Join a home from a people invite: the name the others see, then POST /household/join. The token replaces any
   * home this phone had; the code is never kept. */
  private async joinPeople(): Promise<void> {
    const j = this.l.join;
    if (!j || j.busy) return;
    const bad = nameError(j.name);
    if (bad) {
      j.error = bad;
      return;
    }
    j.busy = true;
    j.error = null;
    try {
      const r = await this.d.fetch(`${API}/household/join`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ code: j.code, name: j.name.trim() }),
      });
      const b = (await r.json().catch(() => null)) as { token?: unknown; error?: unknown; detail?: unknown } | null;
      if (r.status !== 201 || typeof b?.token !== "string") {
        j.error = joinError(r.status, typeof b?.error === "string" ? b.error : typeof b?.detail === "string" ? b.detail : undefined);
        return;
      }
      this.signedIn(b.token);
      this.d.markFirstRun();
      this.l.join = null;
      this.l.home = null;
      void this.loadHome();
    } catch {
      j.error = joinError(0);
    } finally {
      j.busy = false;
    }
  }

  private async loadHome(): Promise<void> {
    if (this.d.household.get() === null) return;
    try {
      // All of /due, the whole home: a job with no last-done date has no due date, and within_days would drop it.
      const [ra, rd] = await Promise.all([this.api(`${API}/appliances`), this.api(`${API}/due`), this.loadFolk()]);
      if (!ra.ok) return;
      const appliances = await this.withIngest((await ra.json()) as Appliance[]);
      const names = new Map(appliances.map((a) => [a.id, a.alias]));
      this.dueNames = await this.loadDueNames(appliances);
      const due = rd.ok ? ((await rd.json()) as DueWire[]).map((d) => dueItem(d, names, this.dueNames)) : [];
      this.l.home = { appliances, due: sortDue(due) };
      const sh = this.l.sheet;
      if (sh && !this.onList(sh.applianceId, sh.jobId)) sh.error = "missing";
    } catch {
      // Home stays the plain start screen.
    }
  }

  /** The list leaves out ingest; an appliance with no checked card is fetched on its own, so its notice (no manual found,
   * "not mine", and the like) still says what to do next. One that won't load keeps the list's row. */
  private async withIngest(appliances: Appliance[]): Promise<Appliance[]> {
    return Promise.all(
      appliances.map(async (a) => {
        if (a.card_status === "checked" || a.ingest) return a;
        const r = await this.api(`${API}/appliances/${encodeURIComponent(a.id)}`).catch(() => null);
        const one = r?.ok ? ((await r.json().catch(() => null)) as Appliance | null) : null;
        return one?.id === a.id ? one : a;
      }),
    );
  }

  /** Each checked card's due_name per job, in the app's language (else English), and its interval quote's language and
   * English line; a card that won't load adds nothing. */
  private async loadDueNames(appliances: Appliance[]): Promise<DueNames> {
    const l = this.d.lang.slice(0, 2).toLowerCase();
    const out: DueNames = new Map();
    type CardJson = {
      jobs?: { id: string; due_name?: Record<string, string> | null; interval?: { quote_ref?: string } | null }[];
      quotes?: { id: string; lang?: string; en?: string | null }[];
    };
    await Promise.all(
      appliances
        .filter((a) => a.card_status === "checked")
        .map(async (a) => {
          const r = await this.api(`${API}/appliances/${encodeURIComponent(a.id)}/card`).catch(() => null);
          const card = r?.ok ? ((await r.json().catch(() => ({}))) as CardJson) : {};
          const view = cardView(card, l);
          if (view) this.l.cards[a.id] = view;
          const quotes = Array.isArray(card.quotes) ? card.quotes : [];
          for (const j of Array.isArray(card.jobs) ? card.jobs : []) {
            const q = quotes.find((x) => x.id === j.interval?.quote_ref);
            out.set(`${a.id}/${j.id}`, { name: j.due_name?.[l] ?? j.due_name?.en ?? undefined, lang: q?.lang, en: q?.en ?? undefined });
          }
        }),
    );
    return out;
  }

  /** "When did you last do this?": the server sets the job's last-done date (or never / don't know) and returns its
   * new due date; the row then reads like any other. */
  private async answerLastDone(applianceId: string, jobId: string, body: { source: string; date?: string }): Promise<void> {
    const item = this.l.home?.due.find((d) => d.applianceId === applianceId && d.jobId === jobId);
    if (!item || item.answer === "busy") return;
    item.answer = "busy";
    if (body.date) item.picked = body.date;
    try {
      const r = await this.api(`${API}/appliances/${encodeURIComponent(applianceId)}/jobs/${encodeURIComponent(jobId)}/last-done`, {
        method: "PUT",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) {
        item.answer = r.status === 422 && body.source === "user" ? "future" : "failed";
        return;
      }
      this.replaceDue((await r.json()) as DueWire);
      if (this.l.sheet) void this.loadChanges();
    } catch {
      item.answer = "failed";
    }
  }

  /** After the last step: when this job is next due, from the schedule (the server moves it once the job is done). */
  private async loadNextDue(jobId: string): Promise<void> {
    const aid = this.params.get("appliance");
    if (!aid || this.d.household.get() === null) return;
    const r = await this.api(`${API}/due`).catch(() => null);
    if (!r?.ok) return;
    const d = ((await r.json()) as { appliance_id: string; job_id: string; due_on: string | null }[]).find(
      (x) => x.appliance_id === aid && x.job_id === jobId,
    );
    if (d?.due_on && this.l.complete) {
      const dt = new Date(`${d.due_on}T12:00:00Z`);
      this.l.complete.nextDue = `${dt.getUTCDate()} ${dt.toLocaleString(langOf(this.l.lang) === "nl" ? "nl-NL" : "en-GB", { month: "long", timeZone: "UTC" })}`;
    }
  }

  /** DELETE /household (the pilot form promises deletion): the server forgets the household, the phone its token. */
  private async deleteHousehold(): Promise<void> {
    this.l.deleteArmed = false;
    const r = await this.api(`${API}/household`, { method: "DELETE" });
    if (r.status !== 204) return;
    this.d.household.set(null);
    this.l.ha = { ...this.l.ha, token: null };
    this.l.conn = null;
    this.l.joinError = tr(this.d.lang, JOIN_TEXT.deleted);
    this.l.firstRun = disclaimer(this.d.disclaimers, "first_run", this.d.lang) ?? "";
  }

  private async shareNote(): Promise<void> {
    const id = this.s.sessionId;
    if (!id) return;
    const r = await this.api(`${API}/sessions/${encodeURIComponent(id)}/note`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ photo_frame_ids: this.sentFrames.slice(-3) }),
    });
    if (!r.ok) return;
    const note = (await r.json()) as { text: string; photos: string[] };
    const files: File[] = [];
    for (const [i, url] of note.photos.entries()) {
      const b = await this.api(url).then((x) => (x.ok ? x.blob() : null));
      if (b) files.push(new File([b], `photo-${i + 1}.jpg`, { type: "image/jpeg" }));
    }
    const nav = this.d.doc.defaultView?.navigator;
    if (nav?.canShare?.({ files }) && files.length) await nav.share({ title: tr(this.l.lang, "Note for a technician"), text: note.text, files });
    else if (nav?.share) await nav.share({ title: tr(this.l.lang, "Note for a technician"), text: note.text });
  }

  // ---- drawing --------------------------------------------------------------------------------------------------

  private loop(): void {
    const frame = (t: number): void => {
      this.paint(t);
      this.d.raf(frame);
    };
    this.d.raf(frame);
  }

  /** Where the frame should sit so every outline is in the band between the header (and notices) and the sheet. */
  private toggleClear(): void {
    if (!this.l.live && this.s.mode !== "replay") return;
    this.l.clear = !this.l.clear;
    this.l.clearNews = null;
    if (!this.l.clearUsed) {
      this.l.clearUsed = true;
      this.d.markClear?.();
    }
  }

  private panFor(shapes: DrawShape[], rect: DOMRect): Pan {
    const pts = shapes.flatMap((sh) => sh.polys.flat());
    let target = NO_PAN;
    if (pts.length > 0 && rect.width > 0 && rect.height > 0) {
      const box = bbox(pts);
      const below = (el: Element | null): number => {
        const r = el?.getBoundingClientRect();
        return r && r.height > 0 ? r.bottom - rect.top : 0;
      };
      // The sheet, and the notices and caption stacked on it, cover the frame only where they overlap: beside it (the
      // wide layout) the whole height is free.
      const foot = [".status", "#sentences", ".sheet"]
        .map((q) => this.els.root.querySelector(q)?.getBoundingClientRect())
        .filter((r): r is DOMRect => !!r && r.height > 0 && r.left < rect.right && r.right > rect.left && r.top - rect.top > below(this.els.top));
      const free = {
        top: below(this.els.top),
        bottom: foot.length ? Math.min(...foot.map((r) => r.top)) - rect.top : rect.height,
      };
      target = focusPan(this.img.w, this.img.h, rect.width, rect.height, box, free, 16);
    }
    // Ease there over a few frames; with reduced motion, jump.
    const k = this.d.doc.defaultView?.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? 1 : 0.2;
    const step = (a: number, b: number): number => (Math.abs(b - a) < 0.5 ? b : a + (b - a) * k);
    return { dx: step(this.pan.dx, target.dx), dy: step(this.pan.dy, target.dy) };
  }

  paint(t: number): void {
    const now = this.d.now();
    this.pollAppliance(now);
    this.loadJobs();
    this.loadThumb();
    const view = this.lastView;
    this.l.viewMsg =
      now < this.ringWarnUntil
        ? this.ringWarnText
        : view && !view.ok && this.s.step?.view === "side"
          ? tr(this.l.lang, VIEW_TEXT[view.why])
          : now < this.viewOkUntil
            ? tr(this.l.lang, "View OK.")
            : null;
    this.l.noVoice = this.speech.noVoice;
    if (this.l.live) this.l.head = this.liveHead();
    render(this.els, this.s, this.l, this.d.barLayout, now);
    if (this.metrics.painted(this.d.now(), !this.els.stop.hidden)) this.sendTimings();

    const ctx = this.canvas.getContext?.("2d");
    if (!ctx) return;
    const rect = this.canvas.getBoundingClientRect();
    const dpr = this.d.doc.defaultView?.devicePixelRatio ?? 1;
    if (this.canvas.width !== Math.round(rect.width * dpr)) this.canvas.width = Math.round(rect.width * dpr);
    if (this.canvas.height !== Math.round(rect.height * dpr)) this.canvas.height = Math.round(rect.height * dpr);
    const replay = this.s.mode === "replay";
    const place = (frameId: string, p: Pt[]): Pt[] | null => (replay ? p : this.tracker.warpPoly(frameId, p));

    const shapes: DrawShape[] = [];
    // The part's tag says what it is and where the manual shows it, as the site's figures do: "Coarse filter · p. 43".
    const st = this.s.step;
    const page = st && !ourStep(st) && st.page !== undefined ? `p. ${st.page}` : null;
    for (const o of this.s.outlines.values()) {
      const polys = o.polys.map((p) => place(o.frameId, p)).filter((p): p is Pt[] => p !== null);
      const label = o.kind === "target" && o.label ? `${o.label.charAt(0).toUpperCase()}${o.label.slice(1)}` : null;
      shapes.push({ id: o.id, kind: o.kind, polys, since: this.shapesSince.get(o.id) ?? t, pulseSince: this.pulseSince.get(o.id) ?? null, label, page });
    }
    this.pan = this.panFor(shapes, rect);
    const frame = replay ? this.replayFrame : this.video;
    if (frame) {
      // Inside its own box (object-fit: cover), so the frame still fills the screen sideways.
      const at = this.pan.dx === 0 && this.pan.dy === 0 ? "" : `calc(50% + ${this.pan.dx.toFixed(1)}px) calc(50% + ${this.pan.dy.toFixed(1)}px)`;
      if (frame.style.objectPosition !== at) frame.style.objectPosition = at;
      // Where the picture ends once panned up, so its foot can fade into the strip below.
      const foot = this.pan.dy < 0 ? `${(rect.height / 2 + (this.img.h * Math.max(rect.width / this.img.w, rect.height / this.img.h)) / 2 + this.pan.dy).toFixed(0)}px` : "";
      if (frame.style.getPropertyValue("--foot") !== foot) frame.style.setProperty("--foot", foot);
    }
    const map = coverMap(this.img.w, this.img.h, this.canvas.width, this.canvas.height, { dx: this.pan.dx * dpr, dy: this.pan.dy * dpr });
    const zones: DrawZone[] = [];
    const nowZones = replay ? [] : this.zonesNow();
    for (const z of nowZones) {
      if (!z.polygon || !z.scaleCmPerPx) continue;
      const gap = this.guard.gaps.get(z.zoneId) ?? null;
      zones.push({
        id: z.zoneId,
        polys: [z.polygon],
        ringPx: this.guard.ringFor(z) / z.scaleCmPerPx,
        handGapPx: gap === null ? null : gap / z.scaleCmPerPx,
        testZone: z.testZone,
      });
    }
    if (replay)
      for (const [id, z] of this.s.zones)
        zones.push({ id, polys: z.polys, ringPx: z.msg.ring_cm / (z.msg.known_width_cm ? z.msg.known_width_cm / Math.max(1, bbox(z.polys[0] ?? []).x1 - bbox(z.polys[0] ?? []).x0) : 0.1), handGapPx: null, testZone: false });
    this.look ??= readLook(this.d.doc.body, this.d.doc.defaultView?.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false);
    draw(ctx, shapes, zones, map, t, dpr, this.look);
  }
}

export type { GuardState };

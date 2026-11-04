// The household token: the pilot door (an invite link or QR, ?invite=<code>, then POST /households with consent)
// returns it once, the phone keeps it on the device, and sends it as hello.household and as the bearer on every
// /api call. A 401 means it is gone or wrong: the phone forgets it and shows the join screen again.

import { langOf } from "./words.ts";

export interface TokenStore {
  get(): string | null;
  set(token: string | null): void;
}

const KEY = "fixit.household.v1";

/** The device's token in localStorage; a private window that refuses storage keeps it for this page only. */
export function localTokenStore(): TokenStore {
  let mem: string | null = null;
  return {
    get() {
      try {
        return localStorage.getItem(KEY) ?? mem;
      } catch {
        return mem;
      }
    },
    set(token) {
      mem = token;
      try {
        if (token === null) localStorage.removeItem(KEY);
        else localStorage.setItem(KEY, token);
      } catch {
        // Kept in memory only.
      }
    },
  };
}

/** The request with the household bearer added, when there is a token. */
export function authed(init: RequestInit | undefined, token: string | null): RequestInit {
  if (!token) return init ?? {};
  const headers = new Headers(init?.headers);
  headers.set("authorization", `Bearer ${token}`);
  return { ...init, headers };
}

/** Which door opened this phone: an invite link (?invite=), the open door (no code), a judges' link (/try#k=), or a
 * return link made on the household's other phone (#return/<code>). */
export type Door = { kind: "invite"; code: string } | { kind: "open" } | { kind: "try"; code: string } | { kind: "return"; code: string };

/** A spend cap's 429 (REST) or 4429 close (WebSocket): which cap, and when it opens again (null: not known). */
export interface Cap {
  error: "home_cap" | "global_cap" | "judges_cap" | "judges_full" | "door_cap" | "address_cap";
  reopensAt: string | null;
}

export type JoinError = "used" | "throttled" | "failed" | "door_closed" | "gone";
export type JoinResult = { token: string } | { error: JoinError } | { cap: Cap };

const CAPS = ["home_cap", "global_cap", "judges_cap", "judges_full", "door_cap", "address_cap"] as const;

/** A CapError body (REST 429, or a 4429 close reason, as JSON); null for anything else, such as a plain rate limit. */
export function capOf(raw: unknown): Cap | null {
  let v = raw;
  if (typeof v === "string") {
    try {
      v = JSON.parse(v);
    } catch {
      return null;
    }
  }
  if (!v || typeof v !== "object") return null;
  const o = v as { error?: unknown; reopens_at?: unknown };
  if (!CAPS.includes(o.error as Cap["error"])) return null;
  return { error: o.error as Cap["error"], reopensAt: typeof o.reopens_at === "string" ? o.reopens_at : null };
}

/** GET /households/door: whether the open door would open a home now, so a shut or capped door can offer the replay
 * before it asks for consent. null when it can't be read: the door then asks as usual, and the POST says why. */
export type DoorState = { state: "open" | "shut" | "capped"; reopensAt: string | null };
export async function doorState(fetcher: typeof fetch, api: string): Promise<DoorState | null> {
  try {
    const r = await fetcher(`${api}/households/door`, { method: "GET" });
    if (!r.ok) return null;
    const b = (await r.json()) as { state?: unknown; reopens_at?: unknown };
    if (b.state !== "open" && b.state !== "shut" && b.state !== "capped") return null;
    return { state: b.state, reopensAt: typeof b.reopens_at === "string" ? b.reopens_at : null };
  } catch {
    return null;
  }
}

/** Opens the household through its door. Called only once the consent box is ticked (the return link needs none: the
 * household gave it on its first phone). Country comes from the invite or the server, so only the language is sent. */
export async function joinHousehold(fetcher: typeof fetch, api: string, door: Door, lang: string): Promise<JoinResult> {
  const l = lang.slice(0, 2).toLowerCase();
  const [path, body] =
    door.kind === "invite"
      ? ["households", { invite_code: door.code, consent: true, lang: l }]
      : door.kind === "open"
        ? ["households", { consent: true, age_ok: true, lang: l }]
        : door.kind === "try"
          ? ["households/try", { code: door.code, consent: true, age_ok: true, lang: l }]
          : ["households/return", { code: door.code }];
  try {
    const r = await fetcher(`${api}/${path}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    if (r.status === 429) {
      const cap = capOf(await r.json().catch(() => null));
      return cap ? { cap } : { error: "throttled" };
    }
    if (r.status === 403) {
      const e = (await r.json().catch(() => ({}))) as { error?: unknown; detail?: unknown };
      return { error: e.error === "door_closed" || e.detail === "door_closed" ? "door_closed" : "used" };
    }
    if (r.status === 404) return { error: "gone" };
    if (!r.ok) return { error: "failed" };
    const b = (await r.json()) as { token?: unknown };
    return typeof b.token === "string" ? { token: b.token } : { error: "failed" };
  } catch {
    return { error: "failed" };
  }
}

/** The door in the address, if any. A code is read once and then taken out of the address, so it is never
 * bookmarked, shared or left in the history: /try#k=<code> (a judges' link) and #return/<code>. The site's Start
 * button opens /start (or ?start): the open door. With none of these the phone shows no door. */
export function doorFromUrl(path: string, hash: string, invite: string | null, start = false): Door | null {
  const ret = /^#return\/([A-Za-z0-9_-]{1,200})$/.exec(hash);
  if (ret) return { kind: "return", code: ret[1]! };
  if (/^\/try\/?$/.test(path)) {
    // Every printed QR (the video, the site, the card) is a bare /try: with no code it is the open door, and the
    // server's answer says whether that door is shut or capped.
    const k = /^#k=([A-Za-z0-9_-]{1,200})$/.exec(hash);
    return k ? { kind: "try", code: k[1]! } : { kind: "open" };
  }
  if (invite) return { kind: "invite", code: invite };
  if (start || /^\/start\/?$/.test(path)) return { kind: "open" };
  return null;
}

/** The consent every door shows (eval/consent/app-consent.json): the screen itself says what leaves the phone, what
 * Meta keeps, deletion on request, and 18+. Until the file exists this visible placeholder stands in and says so. */
export const APP_CONSENT_PLACEHOLDER = "[App consent: what leaves the phone, what Meta keeps, deletion on request, and 18+ go here.]";

/** eval/consent/app-consent.json: the screen's text in en and nl, and its draft tag until the wording is approved. */
export interface ConsentFile {
  en: string;
  nl?: string;
  form_version: number;
  signed: boolean;
  status?: { en?: string; nl?: string };
}

export interface ConsentLine {
  /** The sentence beside the tick box. */
  text: string;
  /** A whole consent screen (the in-app line) splits into its heading, the paragraphs above the box, and the box's
   * sentence; a one-paragraph file is the box's sentence alone. */
  heading?: string | null;
  body?: string[];
  /** The small draft tag shown above the text until the form is signed; null once it is. */
  status: string | null;
}

/** The consent line to show: the file's text in the language (else English), with its draft tag until the form is
 * signed; the placeholder when there is no file. */
export function consentLine(f: ConsentFile | null, lang: string, placeholder = APP_CONSENT_PLACEHOLDER): ConsentLine {
  if (!f) return { text: placeholder, status: null };
  const nl = lang.slice(0, 2).toLowerCase() === "nl";
  const all = ((nl ? f.nl : undefined) ?? f.en).split(/\n\s*\n/).map((x) => x.trim()).filter(Boolean);
  const screen = all.length > 2 ? { heading: all[0]!, body: all.slice(1, -1) } : { heading: null, body: [] };
  const text = all.length > 2 ? all[all.length - 1]! : all.join(" ");
  const status = f.signed ? null : ((nl ? f.status?.nl : undefined) ?? f.status?.en ?? "Draft wording");
  return { text, status, ...screen };
}

/** The closing sentence of a line met at the door, where only the replay is left to offer. The screen drops it when
 * there is no replay to watch. */
export const STILL_REPLAY = { en: "You can still watch the demo replay.", nl: "Je kunt nog wel de demo-opname bekijken." } as const;

export const JOIN_TEXT: Record<JoinError | "lost" | "lostShut" | "deleted" | "consent" | "tryGone", string> = {
  consent: "Tick the consent box first.",
  deleted: "Your household and its data are deleted from this service.",
  used: "That invite was already used, or is wrong. Ask for a new link.",
  throttled: "Too many tries. Wait a while, then open the link again.",
  failed: "Couldn't join just now. Check the connection and try again.",
  lost: "This phone isn't signed in any more. On a phone that still is, open Settings at the top, then Open on another phone, for a link. Or start a new household here.",
  // A removed person, or a phone signed out, while the door is shut: no new household to start, so ask for an invite.
  lostShut: `This phone isn't signed in to a home any more. To come back, ask someone in the home for a new invite. ${STILL_REPLAY.en}`,
  door_closed: `Fix-It Lens is open by invitation only for now. ${STILL_REPLAY.en}`,
  gone: "That link was already used or has expired. On your other phone, open Settings at the top, then Open on another phone, for a new one.",
  tryGone: "That link doesn't work any more. Ask whoever sent it for a new one.",
};

/** About > Open on another phone, when no link could be made. */
export const RETURN_LINK_TEXT = {
  failed: "Couldn't make a link just now. Check the connection and try again.",
  forbidden: "Only the household's own phones can make this link. Ask whoever runs your household.",
} as const;

/** The door read as capped before any consent: when it opens again, or that no time is known. */
export function doorCappedText(reopensAt: string | null, now: Date, lang = "en", timeZone?: string): string {
  const when = reopensAt ? reopens(new Date(reopensAt), now, lang, timeZone) : null;
  if (langOf(lang) === "nl")
    return `Fix-It Lens laat nu even geen nieuwe huizen toe. ${when ? `Het gaat ${when} weer open.` : "Er is nog niet bekend wanneer het weer opengaat."} ${STILL_REPLAY.nl}`;
  return `Fix-It Lens isn't opening new homes just now. ${when ? `It opens again ${when}.` : "No time is known yet for when it opens again."} ${STILL_REPLAY.en}`;
}

/** A 4429 close whose reason couldn't be read. */
export const CAP_UNKNOWN_TEXT = "Today's limit for guided help is reached. Your reminders, your appliances and the demo replay still work.";

const STILL_WORKS = {
  en: "Your reminders, your appliances and the demo replay still work.",
  nl: "Je herinneringen, je apparaten en de demo-opname werken nog gewoon.",
} as const;

/** A cap in kind words: which limit, when it opens again in the phone's own time zone, and what still works. */
export function capText(cap: Cap, now: Date, lang = "en", timeZone?: string): string {
  return capLine(cap, now, STILL_WORKS, lang, timeZone);
}

/** A cap met at the door: there is no home yet, so only the replay is left to offer. */
export function joinCapText(cap: Cap, now: Date, lang = "en", timeZone?: string): string {
  return capLine(cap, now, STILL_REPLAY, lang, timeZone);
}

function capLine(cap: Cap, now: Date, still: { en: string; nl: string }, lang: string, timeZone?: string): string {
  const when = cap.reopensAt ? reopens(new Date(cap.reopensAt), now, lang, timeZone) : null;
  if (langOf(lang) === "nl") {
    switch (cap.error) {
      case "home_cap":
        return `Je huis heeft de begeleide hulp van vandaag gebruikt. ${when ? `Die gaat ${when} weer open.` : "Die gaat morgen weer open."} ${still.nl}`;
      case "judges_cap":
        return `Deze link heeft alle begeleide hulp gebruikt die hij had, en gaat niet meer open. Vraag degene die hem stuurde om een nieuwe. ${still.nl}`;
      case "door_cap":
        return `De nieuwe huizen van vandaag zijn allemaal vergeven. ${when ? `Nieuwe huizen gaan ${when} weer open.` : "Nieuwe huizen gaan morgen weer open."} ${still.nl}`;
      case "address_cap":
        return `Vanaf deze verbinding is vandaag al een nieuw huis geopend. ${when ? `Er kan ${when} weer een worden geopend.` : "Er kan morgen weer een worden geopend."} ${still.nl}`;
      case "global_cap":
        return `Fix-It Lens heeft de limiet van vandaag voor iedereen bereikt. ${when ? `Begeleide hulp gaat ${when} weer open.` : "Begeleide hulp gaat later weer open."} ${still.nl}`;
      case "judges_full":
        return "Deze link heeft alle huizen geopend die hij kan openen. Vraag degene die hem stuurde om een nieuwe, of bekijk de demo-opname.";
    }
  }
  switch (cap.error) {
    case "home_cap":
      return `Your home has used today's guided help. ${when ? `It opens again ${when}.` : "It opens again tomorrow."} ${still.en}`;
    case "judges_cap":
      return `This link has used all the guided help it had, and it doesn't reopen. Ask whoever sent it for a new one. ${still.en}`;
    case "door_cap":
      return `Today's new homes are all taken. ${when ? `New homes open again ${when}.` : "New homes open again tomorrow."} ${still.en}`;
    case "address_cap":
      return `A new home was already opened from this connection today. ${when ? `Another can open ${when}.` : "Another can open tomorrow."} ${still.en}`;
    case "global_cap":
      return `Fix-It Lens has reached today's limit for everyone. ${when ? `Guided help opens again ${when}.` : "Guided help opens again later."} ${still.en}`;
    case "judges_full":
      return "This link has opened all the homes it can. Ask whoever sent it for a new one, or watch the demo replay.";
  }
}

/** "at 01:00" today, "tomorrow at 01:00", or a date for later; in the phone's time zone and language. */
function reopens(at: Date, now: Date, lang: string, timeZone?: string): string | null {
  if (Number.isNaN(at.getTime())) return null;
  const nl = langOf(lang) === "nl";
  const locale = nl ? "nl-NL" : "en-GB";
  const day = (d: Date) => d.toLocaleDateString("en-CA", { timeZone });
  const time = at.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", timeZone });
  const date = at.toLocaleDateString(locale, { day: "numeric", month: "long", timeZone });
  if (day(at) === day(now)) return nl ? `om ${time}` : `at ${time}`;
  if (day(at) === day(new Date(now.getTime() + 86_400_000))) return nl ? `morgen om ${time}` : `tomorrow at ${time}`;
  return nl ? `op ${date} om ${time}` : `on ${date} at ${time}`;
}

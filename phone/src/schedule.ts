// The household's upkeep schedule as the phone reads it: GET /due's jobs, the manual's interval beside the household's
// own (a fixed interval or a window), what each change was and who made it, and the words for when a job is due.
// Every date and interval here is the server's or the user's; the phone only words them, in English or Dutch.

import { langOf, tr, trf } from "./words.ts";

export type Unit = "day" | "week" | "month" | "year";
export type Reason = "scheduled" | "never" | "unknown" | "unanswered" | "cycle";
/** upcoming: before due_on; due: from due_on (to window_end for a window); overdue: after that; none: no date. */
export type Status = "upcoming" | "due" | "overdue" | "none";

/** The household's own schedule for a job; absent means the manual's. */
export type Schedule = { kind: "every"; every: number; unit: Unit } | { kind: "window"; min: number; max: number; unit: Unit };

export interface LastDone {
  date: string | null;
  source: "user" | "checked" | "never" | "unknown";
}

/** One job of GET /due, and the answer to every schedule write. The schedule fields are absent from an older server. */
export interface DueWire {
  appliance_id: string;
  job_id: string;
  title: string;
  /** 1.40: the job in a few words, as its reminder names it. Absent from an older server: the card's name, else the title. */
  due_name?: string;
  due_on: string | null;
  reason: Reason;
  last_done?: LastDone;
  /** The card's interval: cited truth, shown beside the household's own. null (1.20): the manual gives none, and the
   * job is on /due only because a person set the household's own schedule. */
  manual?: { every: number; unit: Unit | "cycle"; page: number | null; quote: string; lang?: string; quote_en?: string | null } | null;
  schedule?: Schedule | null;
  window_end?: string | null;
  status?: Status;
  snoozed_until?: string | null;
  assignee?: string | null;
}

/** The job's state before and after a change. */
export interface JobState {
  last_done: string | null;
  last_done_source: LastDone["source"] | null;
  schedule: Schedule | null;
  snoozed_until: string | null;
  assignee?: string | null;
}

/** GET …/changes: one write to a job, who made it (from the token that sent it), and whether it was undone. */
export interface Change {
  id: number;
  at: string;
  kind: "done" | "last_done" | "schedule" | "snooze" | "assignee" | "undo";
  /** Read from the token that made the call; label is the person's name or the token's label, as it was then. */
  by: { kind: "app" | "agent" | "ha"; person_id?: string | null; label?: string };
  before: JobState;
  after: JobState;
  undone: boolean;
}

/** One upkeep job on the home list and its sheet, with its appliance's name. */
export interface DueItem {
  applianceId: string;
  jobId: string;
  title: string;
  alias: string;
  dueOn: string | null;
  reason: Reason;
  status: Status;
  windowEnd: string | null;
  snoozedUntil: string | null;
  lastDone: LastDone | null;
  manual: DueWire["manual"] | null;
  schedule: Schedule | null;
  /** The person the job is given to (a person id), who alone is reminded of it; null: nobody, so everyone following
   * the appliance is. */
  assignee: string | null;
  /** No last-done date yet: the row asks "When did you last do this?" (Never / Don't know / a date). */
  unanswered: boolean;
  /** While that answer is saved, or why it wasn't. */
  answer: "busy" | "failed" | "future" | null;
  /** The date last picked, kept when the row is drawn again. */
  picked: string;
}

/** What the card adds to a job, by "appliance/job": its own short name ("Machine Clean"), so home names a job as its
 * push does, and its interval quote's language and English line (a translation, never the citation). */
export interface CardFacts {
  name?: string;
  lang?: string;
  en?: string;
}
export type DueNames = Map<string, CardFacts>;

export function today(now = Date.now()): string {
  return new Date(now).toISOString().slice(0, 10);
}

/** The server's status, or the same rule worked out from the dates when an older server sends none. */
export function statusOf(d: DueWire, on: string): Status {
  if (d.status) return d.status;
  if (!d.due_on) return "none";
  const end = d.window_end ?? d.due_on;
  if (end < on) return "overdue";
  return d.due_on <= on ? "due" : "upcoming";
}

export function dueItem(d: DueWire, names: Map<string, string>, facts: DueNames = new Map(), on = today()): DueItem {
  const f = facts.get(`${d.appliance_id}/${d.job_id}`);
  const manual = d.manual ? { ...d.manual, lang: d.manual.lang ?? f?.lang, quote_en: d.manual.quote_en ?? f?.en ?? null } : null;
  return {
    applianceId: d.appliance_id,
    jobId: d.job_id,
    title: d.due_name || f?.name || d.title,
    alias: names.get(d.appliance_id) ?? "",
    dueOn: d.due_on,
    reason: d.reason,
    status: statusOf(d, on),
    windowEnd: d.window_end ?? null,
    snoozedUntil: d.snoozed_until ?? null,
    lastDone: d.last_done ?? null,
    manual,
    schedule: d.schedule ?? null,
    assignee: d.assignee ?? null,
    unanswered: d.reason === "unanswered",
    answer: null,
    picked: "",
  };
}

const RANK: Record<Status, number> = { overdue: 0, due: 1, upcoming: 2, none: 3 };

/** Overdue first, then due, then by date; the jobs with no date (unanswered, counted in cycles) last. */
export function sortDue(due: DueItem[]): DueItem[] {
  return [...due].sort((x, y) => RANK[x.status] - RANK[y.status] || (x.dueOn ?? "9999").localeCompare(y.dueOn ?? "9999"));
}

function days(from: string, to: string): number {
  return Math.round((Date.parse(`${to}T12:00:00Z`) - Date.parse(`${from}T12:00:00Z`)) / 86_400_000);
}

/** "3 Nov", or "3 Nov 2027" outside this year; in Dutch, "3 nov." as the platform writes it. */
export function shortDate(iso: string, on: string, lang = "en"): string {
  const d = new Date(`${iso}T12:00:00Z`);
  if (langOf(lang) === "nl") {
    const year = iso.slice(0, 4) === on.slice(0, 4) ? {} : { year: "numeric" as const };
    return d.toLocaleDateString("nl-NL", { day: "numeric", month: "short", timeZone: "UTC", ...year });
  }
  const m = d.toLocaleString("en-GB", { month: "short", timeZone: "UTC" });
  return iso.slice(0, 4) === on.slice(0, 4) ? `${d.getUTCDate()} ${m}` : `${d.getUTCDate()} ${m} ${d.getUTCFullYear()}`;
}

/** How far off a date is, as people say it: "tomorrow", "in 5 days", "in 3 weeks", "next month", "in 4 months". */
export function fromNow(iso: string, on: string, lang = "en"): string {
  const nl = langOf(lang) === "nl";
  const n = days(on, iso);
  if (n <= 0) return nl ? "vandaag" : "today";
  if (n === 1) return nl ? "morgen" : "tomorrow";
  if (n < 14) return nl ? `over ${n} dagen` : `in ${n} days`;
  const months = (Number(iso.slice(0, 4)) - Number(on.slice(0, 4))) * 12 + Number(iso.slice(5, 7)) - Number(on.slice(5, 7));
  if (months <= 0) return nl ? `over ${Math.round(n / 7)} weken` : `in ${Math.round(n / 7)} weeks`;
  if (months === 1) return nl ? "volgende maand" : "next month";
  if (months < 24) return nl ? `over ${months} maanden` : `in ${months} months`;
  return nl ? `over ${Math.floor(months / 12)} jaar` : `in ${Math.floor(months / 12)} years`;
}

/** How long ago a date was: "1 day", "5 days", "3 weeks", "2 months". */
export function agoText(iso: string, on: string, lang = "en"): string {
  const n = Math.max(1, days(iso, on));
  const m = Math.round(n / 30.44);
  if (n < 14) return count(n, "day", lang);
  if (n < 60) return count(Math.round(n / 7), "week", lang);
  return m < 24 ? count(m, "month", lang) : count(Math.floor(m / 12), "year", lang);
}

/** "1 day", "3 weeks"; "1 dag", "3 weken", "2 jaar". */
function count(n: number, unit: Unit | "cycle", lang: string): string {
  const [one, many] = unitWords(lang)[unit];
  return `${n} ${n === 1 ? one : many}`;
}

/** When an overdue job became overdue: the day after its window closed, or its due date. */
function overdueSince(d: DueItem): string | null {
  return d.windowEnd ? nextDay(d.windowEnd) : d.dueOn;
}

/** The home row's and the sheet's line for when a job is due. */
export function whenText(d: DueItem, on: string, lang = "en"): string {
  const t = (en: string) => tr(lang, en);
  const tf = (en: string, v: Record<string, string | number>) => trf(lang, en, v);
  if (d.unanswered) return t("when did you last do this?");
  if (!d.dueOn) return d.reason === "cycle" ? t("counted in uses, not dates") : t("no date yet");
  if (d.status === "overdue") {
    const since = overdueSince(d);
    return since && since < on ? tf("overdue by {ago}", { ago: agoText(since, on, lang) }) : t("overdue");
  }
  if (d.status === "due") {
    if (d.windowEnd && d.windowEnd > on) return tf("due now, by {date}", { date: shortDate(d.windowEnd, on, lang) });
    return d.dueOn < on ? t("overdue") : t("due today");
  }
  return fromNow(d.dueOn, on, lang);
}

function cap(t: string): string {
  return `${t.charAt(0).toUpperCase()}${t.slice(1)}`;
}

/** The home list's sections, in order. Drawn with tr, so their Dutch is in the table. */
export type Group = "overdue" | "due" | "soon" | "later" | "undated";

export const GROUP_TEXT: Record<Group, string> = {
  overdue: "Overdue",
  due: "Due now",
  soon: "Next 30 days",
  later: "Later",
  undated: "Needs a date",
};

const SOON_DAYS = 30;

export function groupOf(d: DueItem, on: string): Group {
  if (d.status === "overdue") return "overdue";
  if (d.status === "due") return "due";
  if (!d.dueOn || d.unanswered) return "undated";
  return days(on, d.dueOn) <= SOON_DAYS ? "soon" : "later";
}

/** A date as a button shows it, in the app's language: short weekday, day, short month, day first (en-GB or nl). */
export function friendlyDate(iso: string, lang: string): string {
  const nl = lang.slice(0, 2).toLowerCase() === "nl";
  return new Date(`${iso}T12:00:00Z`).toLocaleDateString(nl ? "nl-NL" : "en-GB", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
}

/** The sheet's line, with its dates: "In 5 days · 1 Nov", "Due now · 7 Oct to 5 Jan 2027", "Overdue · since 24 Oct". */
export function sheetWhen(d: DueItem, on: string, lang = "en"): string {
  const t = (en: string) => tr(lang, en);
  const tf = (en: string, v: Record<string, string | number>) => trf(lang, en, v);
  const date = (iso: string) => shortDate(iso, on, lang);
  // Unanswered: the question itself sits under this line.
  if (d.unanswered) return t("No date yet");
  if (!d.dueOn) return cap(whenText(d, on, lang));
  const end = d.windowEnd;
  if (d.status === "overdue") return tf("Overdue · since {date}", { date: date(end ?? d.dueOn) });
  if (end) return tf("{when} · {from} to {to}", { when: d.status === "due" ? t("Due now") : cap(fromNow(d.dueOn, on, lang)), from: date(d.dueOn), to: date(end) });
  return `${cap(whenText(d, on, lang))} · ${date(d.dueOn)}`;
}

const UNIT: Record<Unit | "cycle", [string, string]> = {
  day: ["day", "days"],
  week: ["week", "weeks"],
  month: ["month", "months"],
  year: ["year", "years"],
  cycle: ["use", "uses"],
};

// Dutch counts years as "jaar" after a number ("2 jaar"), and a use of a machine as a "beurt".
const UNIT_NL: Record<Unit | "cycle", [string, string]> = {
  day: ["dag", "dagen"],
  week: ["week", "weken"],
  month: ["maand", "maanden"],
  year: ["jaar", "jaar"],
  cycle: ["beurt", "beurten"],
};

/** A unit's [one, many] in the language. */
export function unitWords(lang = "en"): Record<Unit | "cycle", [string, string]> {
  return langOf(lang) === "nl" ? UNIT_NL : UNIT;
}

function every(n: number, unit: Unit | "cycle", lang: string): string {
  const [one, many] = unitWords(lang)[unit];
  if (langOf(lang) !== "nl") return n === 1 ? `every ${one}` : `every ${n} ${many}`;
  // "elk jaar" (het jaar), but "elke dag", "elke week", "elke maand", "elke beurt"; with a number always "elke".
  return n === 1 ? `${unit === "year" ? "elk" : "elke"} ${one}` : `elke ${n} ${many}`;
}

/** "every 4 months", "every 3 to 6 months"; "elke 4 maanden", "elke 3 tot 6 maanden". */
export function scheduleText(s: Schedule, lang = "en"): string {
  if (s.kind === "every") return every(s.every, s.unit, lang);
  const many = unitWords(lang)[s.unit][1];
  return langOf(lang) === "nl" ? `elke ${s.min} tot ${s.max} ${many}` : `every ${s.min} to ${s.max} ${many}`;
}

/** The manual's interval with its page: "every 3 months · p. 43". */
/** The manual gives this job no interval: what is on /due is the household's own schedule and nothing else. */
export function ownOnly(d: DueItem): boolean {
  return !d.manual && d.schedule !== null;
}

/** An upkeep job on a card whose manual gives no interval and that nobody in the home has scheduled: it isn't on
 * /due, and home lists it under "No schedule yet" so a person can set one. */
export interface Unscheduled {
  applianceId: string;
  jobId: string;
  title: string;
  alias: string;
}

export function manualText(m: NonNullable<DueWire["manual"]>, lang = "en"): string {
  return m.page ? `${every(m.every, m.unit, lang)} · p. ${m.page}` : every(m.every, m.unit, lang);
}

/** The schedule editor while open: what is typed is kept here as text, so a redraw keeps it. */
export interface Draft {
  kind: "manual" | "every" | "window";
  every: string;
  min: string;
  max: string;
  /** "" until picked: a job whose manual gives no interval starts with nothing filled in. */
  unit: Unit | "";
}

/** The editor's start: the household's own schedule, or the manual's interval to change from. With neither (a job
 * whose manual gives no interval, or one counted in uses) every field starts empty: the phone never suggests one. */
export function draftOf(d: DueItem | null): Draft {
  const s = d?.schedule;
  if (s?.kind === "every") return { kind: "every", every: String(s.every), min: "", max: "", unit: s.unit };
  if (s?.kind === "window") return { kind: "window", every: "", min: String(s.min), max: String(s.max), unit: s.unit };
  const m = d?.manual;
  if (m && m.unit !== "cycle") return { kind: "manual", every: String(m.every), min: String(m.every), max: String(m.every * 2), unit: m.unit };
  return { kind: m ? "manual" : "every", every: "", min: "", max: "", unit: "" };
}

const MAX_EVERY = 3650;

function whole(t: string): number | null {
  if (!/^\d+$/.test(t.trim())) return null;
  const n = Number(t);
  return n >= 1 && n <= MAX_EVERY ? n : null;
}

const PICK_UNIT = "Pick days, weeks, months or years.";

/** The PUT …/schedule body for the draft, or why it can't be saved yet. */
export function draftBody(d: Draft, lang = "en"): { body: Schedule | { kind: "manual" } } | { error: string } {
  const t = (en: string) => tr(lang, en);
  if (d.kind === "manual") return { body: { kind: "manual" } };
  const unit = d.unit;
  if (d.kind === "every") {
    const n = whole(d.every);
    if (n === null) return { error: t("Type a whole number from 1.") };
    return unit ? { body: { kind: "every", every: n, unit } } : { error: tr(lang, PICK_UNIT) };
  }
  const lo = whole(d.min);
  const hi = whole(d.max);
  if (lo === null || hi === null) return { error: t("Type whole numbers from 1.") };
  if (lo >= hi) return { error: t("The first number must be the smaller one.") };
  return unit ? { body: { kind: "window", min: lo, max: hi, unit } } : { error: tr(lang, PICK_UNIT) };
}

/** The day after a date. */
export function nextDay(iso: string): string {
  return new Date(Date.parse(`${iso}T12:00:00Z`) + 86_400_000).toISOString().slice(0, 10);
}

/** The earliest date a snooze can pick: after today and after the current due date (a snooze only moves it later). */
export function snoozeMin(d: DueItem, on: string): string {
  return nextDay(d.dueOn && d.dueOn > on ? d.dueOn : on);
}

function byText(by: Change["by"], lang: string): string {
  const t = (en: string) => tr(lang, en);
  const tf = (en: string, v: Record<string, string | number>) => trf(lang, en, v);
  const label = by.label?.trim();
  if (by.kind === "ha") return t("from Home Assistant");
  if (by.kind === "agent") return label ? tf("by an agent ({label})", { label }) : t("by an agent");
  return label ? tf("by {label}", { label }) : t("in the app");
}

function lastDoneWords(s: JobState, on: string, lang: string): string {
  if (s.last_done_source === "never") return tr(lang, "never done");
  if (s.last_done_source === "unknown" || !s.last_done) return tr(lang, "don't know");
  return shortDate(s.last_done, on, lang);
}

/** What a change did: "Snoozed until 3 Dec". A job whose manual gives no interval has no manual's to go back to. */
/** names: the home's people by id, so an assignment names who; without them it says only that it changed. */
export function changeWhat(c: Change, on: string, noInterval = false, names: Map<string, string> = new Map(), lang = "en"): string {
  const t = (en: string) => tr(lang, en);
  const tf = (en: string, v: Record<string, string | number>) => trf(lang, en, v);
  switch (c.kind) {
    case "done":
      return c.after.last_done ? tf("Marked done on {date}", { date: shortDate(c.after.last_done, on, lang) }) : t("Marked done");
    case "last_done":
      return tf("Last done set: {what}", { what: lastDoneWords(c.after, on, lang) });
    case "schedule":
      if (c.after.schedule) return tf("Schedule set to {schedule}", { schedule: scheduleText(c.after.schedule, lang) });
      return noInterval ? t("Schedule stopped") : t("Schedule back to the manual's");
    case "snooze":
      return c.after.snoozed_until ? tf("Snoozed until {date}", { date: shortDate(c.after.snoozed_until, on, lang) }) : t("Snooze cleared");
    case "assignee":
      if (!c.after.assignee) return t("Given to nobody");
      return names.has(c.after.assignee) ? tf("Given to {name}", { name: names.get(c.after.assignee)! }) : t("Assigned");
    case "undo":
      return t("Undid a change");
  }
}

/** One history line: what changed, and who changed it. */
export function changeText(c: Change, on: string, noInterval = false, names: Map<string, string> = new Map(), lang = "en"): string {
  return `${changeWhat(c, on, noInterval, names, lang)}, ${byText(c.by, lang)}`;
}

/** The change an undo would revert: the latest one that is not an undo and not undone. */
export function undoable(changes: Change[] | null): Change | null {
  return changes?.find((c) => c.kind !== "undo" && !c.undone) ?? null;
}

/** The path of one job's schedule route. */
export function jobPath(api: string, applianceId: string, jobId: string, tail: string): string {
  return `${api}/appliances/${encodeURIComponent(applianceId)}/jobs/${encodeURIComponent(jobId)}/${tail}`;
}

/** The sheet's place in the address: #job/<appliance>/<job>, the link a reminder and an agent open. */
export function sheetHash(applianceId: string, jobId: string): string {
  return `job/${encodeURIComponent(applianceId)}/${encodeURIComponent(jobId)}`;
}

export function parseSheetHash(hash: string): { applianceId: string; jobId: string } | null {
  const m = /^#?job\/([^/]+)\/([^/]+)$/.exec(hash);
  if (!m) return null;
  try {
    return { applianceId: decodeURIComponent(m[1]!), jobId: decodeURIComponent(m[2]!) };
  } catch {
    return null;
  }
}

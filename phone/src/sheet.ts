// One job's sheet: when it is due, the manual's interval beside the household's own, mark done, snooze, its history
// and undo. Reached in one tap from the home list and from a reminder, and at #job/<appliance>/<job>. A job whose manual
// gives no interval has only the household's schedule: its sheet says so, offers one, and can stop it again.

import {
  changeText,
  changeWhat,
  draftBody,
  friendlyDate,
  manualText,
  nextDay,
  ownOnly,
  scheduleText,
  shortDate,
  snoozeMin,
  sheetWhen,
  undoable,
  type Change,
  type DueItem,
  type Draft,
  type Unit,
  type Unscheduled,
  unitWords,
} from "./schedule.ts";
import { citeNode, type ManualRef } from "./devices.ts";
import { pageBack } from "./home.ts";
import { accessOf, PEOPLE_TEXT, type Person } from "./people.ts";
import { tr, trf } from "./words.ts";

// The texts tables stay English; each is drawn through tr, and its Dutch is in the home table.

export const LAST_DONE_TEXT = {
  ask: "When did you last do this?",
  failed: "Couldn't save that. Try again.",
  future: "That date is still to come. Pick today or earlier.",
} as const;

export const SHEET_TEXT = {
  failed: "Couldn't save that. Try again.",
  future: "That date is still to come. Pick today or earlier.",
  later: "A snooze only moves it later. Pick a later date.",
  schedule: "That schedule can't be saved. Check the numbers.",
  readOnly: "This phone can only view this home's jobs.",
  nothing: "Nothing to undo.",
  missing: "This job has no schedule in your home.",
  loading: "Getting this job's history…",
  opening: "Fetching this job's dates and history…",
} as const;

export type SheetError = keyof typeof SHEET_TEXT;

export interface SheetState {
  applianceId: string;
  jobId: string;
  /** The job's changes, newest first; null until fetched. */
  changes: Change[] | null;
  busy: boolean;
  error: SheetError | null;
  /** The schedule editor while open. */
  edit: Draft | null;
  /** The dates picked for "done on" and "snooze until", kept when the sheet is drawn again. */
  doneDate: string;
  snoozeDate: string;
  /** Mark done open: Today, Yesterday or another day. */
  doneAsk: boolean;
  /** Later… open: the quick snooze picks and a date. */
  later: boolean;
  /** Job settings open: the household's schedule, who does it, the history. */
  more: boolean;
}

export function sheetInitial(applianceId: string, jobId: string): SheetState {
  return { applianceId, jobId, changes: null, busy: false, error: null, edit: null, doneDate: "", snoozeDate: "", doneAsk: false, later: false, more: false };
}

function el<K extends keyof HTMLElementTagNameMap>(doc: Document, tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = doc.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function button(doc: Document, act: string, text: string, cls?: string): HTMLButtonElement {
  const b = el(doc, "button", cls, text);
  b.type = "button";
  b.dataset.act = act;
  return b;
}

/** A date as a friendly button (the date in words, or "Pick a date") over the platform's own picker, which a tap opens.
 * The label is drawn as given: the caller words it. */
export function datePick(
  doc: Document,
  o: { field?: string; value: string; label: string; lang: string; min?: string; max?: string; empty?: string },
): HTMLElement {
  const box = el(doc, "label", "date-pick");
  // A chip that means a day when empty ("today") says so; the rest ask for one.
  if (o.empty) box.dataset.empty = o.empty;
  const text = el(doc, "span", "date-pick-text", o.value ? datePickText(o.value, o.lang) : (o.empty ?? datePickText("", o.lang)));
  const i = el(doc, "input");
  i.type = "date";
  if (o.field) i.dataset.field = o.field;
  i.value = o.value;
  if (o.min) i.min = o.min;
  if (o.max) i.max = o.max;
  i.setAttribute("aria-label", o.label);
  box.append(text, i);
  return box;
}

/** Mark done asks when: today, yesterday, or another day up to today (picking it is the answer). */
function doneChoices(doc: Document, doneDate: string, today: string, lang: string): HTMLElement {
  const t = (en: string) => tr(lang, en);
  const picks = el(doc, "div", "choices");
  const yesterday = new Date(Date.parse(`${today}T12:00:00Z`) - 86_400_000).toISOString().slice(0, 10);
  picks.append(button(doc, "sheet-done", t("Today")));
  const y = button(doc, "sheet-done-date", t("Yesterday"));
  y.dataset.date = yesterday;
  picks.append(y);
  const other = el(doc, "label", "date-pick other-day");
  const i = el(doc, "input");
  i.type = "date";
  i.dataset.field = "doneDate";
  i.value = doneDate;
  i.max = today;
  i.setAttribute("aria-label", t("Done on another day"));
  other.append(el(doc, "span", undefined, t("Another day…")), i);
  picks.append(other);
  const box = el(doc, "div", "later done-when");
  box.append(picks);
  return box;
}

export function doneLabel(doneDate: string, today: string, lang: string): string {
  return doneDate && doneDate !== today ? trf(lang, "Done on {date}", { date: shortDate(doneDate, today, lang) }) : tr(lang, "Done today");
}

export function datePickText(value: string, lang: string): string {
  return value ? friendlyDate(value, lang) : tr(lang, "Pick a date");
}

/** The question under a job with no last-done date: Never, Don't know, or a date (today or earlier) and Save. */
export function lastDoneForm(doc: Document, d: DueItem, today: string, lang = "en"): HTMLElement {
  const t = (en: string) => tr(lang, en);
  const tf = (en: string, v: Record<string, string | number>) => trf(lang, en, v);
  const box = el(doc, "fieldset", "last-done");
  box.disabled = d.answer === "busy";
  const btn = (act: string, text: string): HTMLButtonElement => {
    const b = button(doc, act, text);
    b.dataset.appliance = d.applianceId;
    b.dataset.job = d.jobId;
    return b;
  };
  const choices = el(doc, "div", "choices");
  choices.append(btn("last-never", t("Never")), btn("last-unknown", t("Don't know")));
  const when = el(doc, "div", "choices");
  // Save date waits for a date.
  const save = btn("last-date", t("Save date"));
  save.disabled = !d.picked;
  when.append(datePick(doc, { value: d.picked, label: tf("Last done: {job}", { job: d.title }), lang, max: today }), save);
  box.append(el(doc, "legend", undefined, t(LAST_DONE_TEXT.ask)), choices, when);
  const why = d.answer === "failed" ? LAST_DONE_TEXT.failed : d.answer === "future" ? LAST_DONE_TEXT.future : null;
  if (why) {
    const p = el(doc, "p", "error", t(why));
    p.setAttribute("role", "alert");
    box.append(p);
  }
  return box;
}

function lastDoneLine(d: DueItem, today: string, lang: string): string | null {
  const ld = d.lastDone;
  if (!ld || d.unanswered) return null;
  if (ld.source === "never") return tr(lang, "Not done yet");
  if (!ld.date) return tr(lang, "Last done: don't know");
  return trf(lang, "Last done {date}", { date: shortDate(ld.date, today, lang) });
}

const UNITS: Unit[] = ["day", "week", "month", "year"];

function numberInput(doc: Document, field: string, value: string, label: string): HTMLInputElement {
  const i = el(doc, "input");
  i.type = "number";
  i.inputMode = "numeric";
  i.min = "1";
  i.max = "3650";
  i.dataset.field = field;
  i.value = value;
  i.setAttribute("aria-label", label);
  return i;
}

/** Nothing typed yet: the editor of a job with no interval opens empty, and says nothing until something is typed. */
function blank(d: Draft): boolean {
  return !d.every && !d.min && !d.max && !d.unit;
}

/** Manual, every N, or between N and M: one choice at a time, then Save. With no interval in the manual there is no
 * "As the manual says" to go back to. */
function editor(doc: Document, d: Draft, noInterval: boolean, lang: string): HTMLElement {
  const t = (en: string) => tr(lang, en);
  const box = el(doc, "fieldset", "sched-edit");
  box.append(el(doc, "legend", undefined, t("How often for your home")));
  const kinds: [Draft["kind"], string][] = [
    ...(noInterval ? [] : [["manual", t("As the manual says")] as [Draft["kind"], string]]),
    ["every", t("Every")],
    ["window", t("Between")],
  ];
  for (const [k, text] of kinds) {
    const row = el(doc, "label", "sched-kind");
    const r = el(doc, "input");
    r.type = "radio";
    r.name = "sched-kind";
    r.value = k;
    r.dataset.field = "kind";
    r.checked = d.kind === k;
    row.append(r, doc.createTextNode(` ${text}`));
    if (d.kind === k && k !== "manual") {
      const nums = el(doc, "span", "sched-nums");
      if (k === "every") nums.append(numberInput(doc, "every", d.every, t("Every how many")));
      else
        nums.append(
          numberInput(doc, "min", d.min, t("At the earliest")),
          doc.createTextNode(` ${t("and")} `),
          numberInput(doc, "max", d.max, t("At the latest")),
        );
      const unit = el(doc, "select");
      unit.dataset.field = "unit";
      unit.setAttribute("aria-label", t("Unit"));
      if (!d.unit) {
        const o = el(doc, "option", undefined, t("unit"));
        o.value = "";
        o.selected = true;
        o.disabled = true;
        unit.append(o);
      }
      for (const u of UNITS) {
        const o = el(doc, "option", undefined, unitWords(lang)[u][1]);
        o.value = u;
        o.selected = d.unit === u;
        unit.append(o);
      }
      nums.append(unit);
      row.append(nums);
    }
    box.append(row);
  }
  if (d.kind === "window") box.append(el(doc, "p", "sub", t("Due from the earliest, overdue after the latest.")));
  const got = draftBody(d, lang);
  const p = el(doc, "p", "error", "error" in got ? got.error : "");
  p.hidden = !("error" in got) || blank(d);
  const row = el(doc, "div", "row");
  row.append(button(doc, "sched-save", t("Save"), "ok"), button(doc, "sched-cancel", t("Cancel"), "dismiss"));
  box.append(p, row);
  return box;
}

/** The redraw key: everything shown, but not what is being typed (a redraw would lose the caret). */
export function sheetKey(sh: SheetState, d: DueItem | null, today: string, folk: Person[] | null = null, lang = "en"): string {
  return JSON.stringify([sh.applianceId, sh.jobId, sh.changes, sh.busy, sh.error, sh.edit?.kind ?? null, sh.doneDate, !sh.snoozeDate, sh.doneAsk, sh.later, sh.more, d, today, folk, lang]);
}

/** The manual's line for a job it gives no interval. */
function noIntervalLine(doc: Document, lang: string): HTMLElement {
  const m = el(doc, "p", "sched-line");
  m.append(el(doc, "span", "tag", tr(lang, "Manual")), doc.createTextNode(tr(lang, "gives no interval")));
  return m;
}

/** Who does it: the one person reminded of the job, or nobody (everyone who follows the appliance). */
function whoNodes(doc: Document, d: DueItem, folk: Person[], viewer: boolean, busy: boolean, lang: string): HTMLElement {
  const t = (en: string) => tr(lang, en);
  const tf = (en: string, v: Record<string, string | number>) => trf(lang, en, v);
  const box = el(doc, "section", "who");
  box.append(el(doc, "h2", undefined, t("Who does it")));
  const given = folk.find((p) => p.id === d.assignee) ?? null;
  const why = !given
    ? t("Everyone who follows this appliance is reminded.")
    : given.me
      ? t("Only you are reminded.")
      : tf("Only {name} is reminded.", { name: given.name });
  if (viewer) {
    box.append(el(doc, "p", "sched-line", given ? (given.me ? t("You") : given.name) : t("Nobody yet")), el(doc, "p", "sub", why));
    return box;
  }
  const lab = el(doc, "label", "role-pick");
  lab.append(el(doc, "span", "sub", t("Given to")));
  const s = el(doc, "select");
  s.dataset.field = "assignee";
  s.disabled = busy;
  const none = el(doc, "option", undefined, t("Nobody"));
  none.value = "";
  s.append(none);
  for (const p of [...folk].sort((a, b) => Number(b.me) - Number(a.me) || a.name.localeCompare(b.name))) {
    const o = el(doc, "option", undefined, p.me ? tf("{name} (you)", { name: p.name }) : p.name);
    o.value = p.id;
    s.append(o);
  }
  s.value = given?.id ?? "";
  lab.append(s);
  box.append(lab, el(doc, "p", "sub", why));
  return box;
}

/** History and undo. */
function historyNodes(doc: Document, sh: SheetState, today: string, noInterval: boolean, folk: Person[] | null, lang: string, undo = true): HTMLElement {
  const t = (en: string) => tr(lang, en);
  const names = new Map((folk ?? []).map((p) => [p.id, p.name]));
  const hist = el(doc, "section", "sheet-history");
  hist.append(el(doc, "h2", undefined, t("History")));
  const changes = sh.changes ?? [];
  if (changes.length === 0) hist.append(el(doc, "p", "sub", sh.changes === null ? t(SHEET_TEXT.loading) : t("No changes yet.")));
  else {
    const ol = el(doc, "ol", "changes");
    for (const c of changes) {
      const li = el(doc, "li", c.undone ? "undone" : undefined);
      const what = el(doc, "span", "what", changeText(c, today, noInterval, names, lang));
      if (c.undone) what.append(el(doc, "span", "tag", t("undone")));
      li.append(el(doc, "span", "sub", shortDate(c.at.slice(0, 10), today, lang)), what);
      ol.append(li);
    }
    hist.append(ol);
  }
  const u = undo && undoNode(doc, sh, today, noInterval, folk, lang);
  if (u) hist.append(u);
  return hist;
}

/** Undo the last change: kept in view after a tap, not folded away with the history. */
function undoNode(doc: Document, sh: SheetState, today: string, noInterval: boolean, folk: Person[] | null, lang: string): HTMLElement | null {
  const u = accessOf(folk) === "viewer" ? null : undoable(sh.changes);
  if (!u) return null;
  const names = new Map((folk ?? []).map((p) => [p.id, p.name]));
  const b = button(doc, "sheet-undo", trf(lang, "Undo: {change}", { change: changeWhat(u, today, noInterval, names, lang) }), "link");
  b.disabled = sh.busy;
  return b;
}

function errorLine(doc: Document, text: string): HTMLElement {
  const p = el(doc, "p", "error", text);
  p.setAttribute("role", "alert");
  return p;
}

/** A card job whose manual gives no interval and that nobody has scheduled: not on /due, so no date, no Done and no
 * snooze; only a schedule to set, its history, and the job itself. */
function unscheduledNodes(doc: Document, sh: SheetState, u: Unscheduled, today: string, close: HTMLElement, folk: Person[] | null, lang: string): Node[] {
  const t = (en: string) => tr(lang, en);
  const viewer = accessOf(folk) === "viewer";
  const out: Node[] = [close];
  out.push(el(doc, "p", "kicker", u.alias ? `${u.alias.charAt(0).toUpperCase()}${u.alias.slice(1)}` : ""));
  out.push(el(doc, "h1", undefined, u.title));
  out.push(el(doc, "p", "when", t("No schedule yet")));
  const how = el(doc, "section", "sched");
  how.append(el(doc, "h2", undefined, t("How often")));
  how.append(noIntervalLine(doc, lang));
  const y = el(doc, "p", "sched-line");
  y.append(el(doc, "span", "tag yours", t("Yours")), doc.createTextNode(t("not set")));
  how.append(y);
  if (sh.edit) how.append(editor(doc, sh.edit, true, lang));
  else if (viewer) how.append(el(doc, "p", "sub", tr(lang, PEOPLE_TEXT.viewer)));
  else {
    how.append(el(doc, "p", "sub", t("Say how often your home does it, and it joins your list with a date.")));
    const set = button(doc, "sched-edit", t("Set a schedule"), "ok");
    set.disabled = sh.busy;
    how.append(set);
  }
  out.push(how);
  if (sh.error) out.push(errorLine(doc, t(SHEET_TEXT[sh.error])));
  out.push(historyNodes(doc, sh, today, true, folk, lang));
  const more = button(doc, "devices-open", t("The manual, and how this card was checked"), "nav-row");
  more.dataset.appliance = u.applianceId;
  out.push(more);
  const start = button(doc, "start-job", t("Start the job"));
  start.dataset.appliance = u.applianceId;
  start.dataset.job = u.jobId;
  const foot = el(doc, "div", "row");
  if (!viewer) {
    foot.append(start);
    out.push(foot);
  }
  return out;
}

/** The sheet's children for one job; `d` is null until home's list has it, and for `own`, a card job with no interval
 * that nobody has scheduled. */
export function sheetNodes(
  doc: Document,
  sh: SheetState,
  d: DueItem | null,
  today: string,
  lang = "en",
  man: ManualRef | null = null,
  own: Unscheduled | null = null,
  folk: Person[] | null = null,
  from: string | null = null,
): Node[] {
  const t = (en: string) => tr(lang, en);
  const tf = (en: string, v: Record<string, string | number>) => trf(lang, en, v);
  // The way back names where it goes: Home, or the appliance page or list the job was opened from.
  const close = pageBack(doc, "sheet-close", from ?? t("Back"));
  if (!d && own) return unscheduledNodes(doc, sh, own, today, close, folk, lang);
  // Someone who can only view reads the dates, the schedule and the history; every write is left out.
  const viewer = accessOf(folk) === "viewer";
  if (!d) {
    const p = el(doc, "p", sh.error ? "sub" : "sub loading", t(SHEET_TEXT[sh.error ?? "opening"]));
    if (!sh.error) p.setAttribute("role", "status");
    return [close, p];
  }
  const due = d.status === "due" || d.status === "overdue";
  const noInterval = ownOnly(d);
  // The guided repair is the main act: the one primary button on the sheet.
  const start = button(doc, "start-job", t("Start the job"), "ok big");
  start.dataset.appliance = d.applianceId;
  start.dataset.job = d.jobId;
  const out: Node[] = [close];
  out.push(el(doc, "p", "kicker", d.alias ? `${d.alias.charAt(0).toUpperCase()}${d.alias.slice(1)}` : ""));
  out.push(el(doc, "h1", undefined, d.title));
  // When it's due, and when it was last done on the same line.
  const w = el(doc, "p", "when", sheetWhen(d, today, lang));
  w.classList.toggle("due-now", due);
  const row = el(doc, "div", "when-row");
  row.append(w);
  const last = lastDoneLine(d, today, lang);
  if (last) row.append(el(doc, "p", "sub", last));
  out.push(row);
  if (d.unanswered && !viewer) out.push(lastDoneForm(doc, d, today, lang));

  // Start, then Mark done | Later… as on home's next job: Mark done asks which day, Later… offers its quick picks.
  if (!viewer) {
    const acts = el(doc, "section", "sheet-acts");
    acts.append(start);
    const pair = el(doc, "div", "next-acts");
    const mark = button(doc, "sheet-done-ask", t("Mark done"), "text-act");
    mark.setAttribute("aria-expanded", String(sh.doneAsk));
    pair.append(mark);
    if (d.dueOn) {
      const later = button(doc, "sheet-later", t("Later…"), "text-act");
      later.setAttribute("aria-expanded", String(sh.later));
      pair.append(later);
    }
    acts.append(pair);
    if (sh.doneAsk) acts.append(doneChoices(doc, sh.doneDate, today, lang));
    if (d.dueOn) {
      if (sh.later) {
        const box = el(doc, "div", "later");
        const min = snoozeMin(d, today);
        // A snooze moves the date on from when it is due (or today, if that has passed): a day, or a week.
        const from = d.dueOn > today ? d.dueOn : today;
        const day = nextDay(from);
        let week = from;
        for (let i = 0; i < 7; i++) week = nextDay(week);
        const picks = el(doc, "div", "choices");
        const quick: [string, string][] = [
          [day, from === today ? t("Tomorrow") : t("A day later")],
          [week, from === today ? t("Next week") : t("A week later")],
        ];
        for (const [date, label] of quick) {
          if (date < min) continue;
          const b = button(doc, "sheet-snooze-to", "");
          b.dataset.date = date;
          b.append(el(doc, "span", undefined, label), el(doc, "span", "sub", friendlyDate(date, lang)));
          picks.append(b);
        }
        // Any other day is the third choice: picking it snoozes, with no second button to press.
        const other = el(doc, "label", "date-pick other-day");
        const i = el(doc, "input");
        i.type = "date";
        i.dataset.field = "snoozeDate";
        i.min = min;
        i.setAttribute("aria-label", t("Snooze until"));
        other.append(el(doc, "span", undefined, t("Another day…")), i);
        picks.append(other);
        box.append(picks);
        acts.append(box);
      }
    }
    if (d.snoozedUntil) {
      const p = el(doc, "p", "sub", `${tf("Snoozed until {date}", { date: shortDate(d.snoozedUntil, today, lang) })} `);
      p.append(button(doc, "sheet-unsnooze", t("Clear the snooze"), "link"));
      acts.append(p);
    }
    if (sh.busy) acts.setAttribute("aria-busy", "true");
    for (const b of acts.querySelectorAll("button")) b.disabled = sh.busy || b.dataset.empty === "1";
    const undo = undoNode(doc, sh, today, noInterval, folk, lang);
    if (undo) acts.append(undo);
    out.push(acts);
    if (sh.error) {
      // A snooze's refusal names the day it must be after, the same day the picker starts from.
      const text =
        sh.error === "later" && d.dueOn
          ? tf("A snooze only moves it later. Pick a day after {date}.", { date: shortDate(d.dueOn > today ? d.dueOn : today, today, lang) })
          : t(SHEET_TEXT[sh.error]);
      out.push(errorLine(doc, text));
    }
  }

  // How often: the manual's line and its page stay in view; the household's own is under Job settings.
  const how = el(doc, "section", "sched");
  how.append(el(doc, "h2", undefined, t("How often")));
  if (noInterval) how.append(noIntervalLine(doc, lang));
  else if (d.manual) {
    // One row: the interval, and under it where it comes from. The page is a citation: it opens the manual there.
    const m = el(doc, "p", "sched-line sched-row");
    const page = d.manual.page;
    const often = manualText({ ...d.manual, page: null }, lang);
    m.append(el(doc, "strong", undefined, often.charAt(0).toUpperCase() + often.slice(1)));
    const from = el(doc, "span", "sub", t("From the manual"));
    if (page) from.append(doc.createTextNode(" · "), man ? citeNode(doc, d.applianceId, man, page, `p. ${page}`, lang) : el(doc, "span", "mono-page", `p. ${page}`));
    m.append(from);
    how.append(m);
    if (d.manual.quote) {
      // The quote is the citation, verbatim in the manual's language; a translation sits under it, labelled as one.
      const q = el(doc, "blockquote", "sched-quote");
      const v = el(doc, "p", undefined, `“${d.manual.quote}”`);
      if (d.manual.lang) v.lang = d.manual.lang;
      v.translate = false; // the manual's own words, never a browser's rewording of them
      q.append(v);
      const other = d.manual.lang && d.manual.lang.slice(0, 2) !== lang.slice(0, 2);
      if (other && d.manual.quote_en && lang.slice(0, 2) === "en") {
        // Only an English phone gets the English line; a Dutch phone reads the quote as the manual gives it.
        const en = el(doc, "p", "translation");
        en.lang = "en";
        en.append(el(doc, "span", "tag", "In English"), doc.createTextNode(d.manual.quote_en));
        q.append(en);
      }
      // The manual's own words open on a tap; the row above already says what they mean.
      const fold = el(doc, "details", "quote-fold");
      fold.append(el(doc, "summary", undefined, t("The manual's words")), q);
      how.append(fold);
    }
  }
  out.push(how);

  // Job settings: the household's own schedule, who does it, and the history, folded until asked for.
  const open = sh.more || sh.edit !== null;
  const fold = button(doc, "sheet-more", t("Job settings"), "fold");
  fold.setAttribute("aria-expanded", String(open));
  out.push(fold);
  if (open) {
    const box = el(doc, "div", "job-settings");
    const y = el(doc, "p", "sched-line");
    // Yours only once the household has set its own; until then the manual's interval stands, said plainly.
    if (d.schedule) y.append(el(doc, "span", "tag yours", t("Yours")), doc.createTextNode(scheduleText(d.schedule, lang)));
    else y.append(doc.createTextNode(t("Following the manual")));
    box.append(y);
    if (viewer) {
      // nothing to change
    } else if (sh.edit) box.append(editor(doc, sh.edit, noInterval, lang));
    // A job the manual counts in uses may still be given a time, and then has a date.
    else if (noInterval) {
      // Stopping it takes the job off the list again; undo brings it back.
      const row = el(doc, "div", "row sched-links");
      row.append(button(doc, "sched-edit", t("Change how often"), "link"), button(doc, "sched-stop", t("Stop the schedule"), "link"));
      box.append(row);
    } else box.append(button(doc, "sched-edit", t("Change how often"), "link"));
    if (folk?.length) box.append(whoNodes(doc, d, folk, viewer, sh.busy, lang));
    box.append(historyNodes(doc, sh, today, noInterval, folk, lang, false));
    out.push(box);
  }
  if (viewer) {
    out.push(el(doc, "p", "sub", tr(lang, PEOPLE_TEXT.viewer)));
    return out;
  }

  // The appliance's own page: its manual, how its card was checked, and Report a problem.
  const more = button(doc, "devices-open", t("The manual, and how this card was checked"), "nav-row");
  more.dataset.appliance = d.applianceId;
  out.push(more);
  return out;
}

// Home's job list: the most urgent job on its own with Start, then every other job in the home under Overdue, Due now,
// Next 30 days, Later and Needs a date, and last the jobs whose manual gives no interval and that nobody has scheduled
// (No schedule yet), all under Keep it running. Then Something's wrong: a code on the display, looked up in the
// appliance's manual. A row opens the job's sheet; Start is the one primary action on the screen.

import type { Appliance } from "./appliance.ts";
import { GROUP_TEXT, groupOf, manualText, ownOnly, scheduleText, sortDue, whenText, type DueItem, type Group, type Unscheduled } from "./schedule.ts";
import { lastDoneForm } from "./sheet.ts";
import { KEEP_RUNNING, SOMETHING_WRONG } from "./names.ts";
import { tr, trf } from "./words.ts";

// Plain line drawings on a 24 grid, in the text colour: no maker's shapes or marks. Each sits inside the brand's
// lock-on, the two crop corners of the mark, so a kind reads as the lens closing on that appliance.
const LOCK = '<path class="lock" d="M2.5 6.5v-4h4M21.5 17.5v4h-4"/>';
const BODY = '<rect x="5.5" y="4" width="13" height="16" rx="1.5"/>';
const GLYPH: Record<string, string> = {
  // A front loader's tub behind its door, and the racks of a dishwasher behind a door with a handle.
  dishwasher: `${BODY}<path d="M5.5 7.5h13"/><circle cx="8" cy="5.75" r=".5"/><circle cx="10" cy="5.75" r=".5"/><path d="M10 10h4"/><path d="M8 13.5h8M8 16.5h8"/><path d="M9.5 13.5v3M12 13.5v3M14.5 13.5v3"/>`,
  washer: `${BODY}<path d="M5.5 7.5h13"/><circle cx="8" cy="5.75" r=".5"/><path d="M13 5.75h3.5"/><circle cx="12" cy="13.75" r="4"/><path d="M8.9 14.6c1-.7 2.1-.7 3.1 0s2.1.7 3.1 0"/>`,
  // The dryer: the same door, with the drum's hub where the washer has water, and a dial for the time.
  dryer: `${BODY}<path d="M5.5 7.5h13"/><circle cx="8" cy="5.75" r=".5"/><circle cx="16" cy="5.75" r="1"/><circle cx="12" cy="13.75" r="4"/><circle cx="12" cy="13.75" r="1.5"/>`,
  boiler: '<rect x="6.5" y="3.5" width="11" height="15" rx="1.5"/><circle cx="12" cy="9.5" r="2.75"/><path d="M12 9.5l1.4-1.4"/><path d="M9 18.5v2M12 18.5v2M15 18.5v2"/>',
  fridge: '<rect x="6.5" y="3" width="11" height="18" rx="1.5"/><path d="M6.5 9.5h11"/><path d="M9 5.5v2M9 12v3"/>',
  freezer: '<rect x="6.5" y="3" width="11" height="18" rx="1.5"/><path d="M6.5 9h11M6.5 15h11"/><path d="M10.5 6h3M10.5 12h3M10.5 18h3"/>',
  oven: '<rect x="4" y="4.5" width="16" height="15" rx="1.5"/><path d="M4 8h16"/><rect x="7" y="10.5" width="10" height="6" rx="1"/><circle cx="7" cy="6.25" r=".5"/><circle cx="9.5" cy="6.25" r=".5"/><path d="M9 12.5h6"/>',
  hob: '<rect x="4" y="4" width="16" height="16" rx="1.5"/><circle cx="8.5" cy="8.5" r="2.25"/><circle cx="15.5" cy="8.5" r="1.75"/><circle cx="8.5" cy="15.5" r="1.75"/><circle cx="15.5" cy="15.5" r="2.25"/>',
  microwave: '<rect x="3.5" y="6" width="17" height="12" rx="1.5"/><rect x="5.75" y="8.25" width="9.5" height="7.5" rx="1"/><path d="M17.75 8.75v1.5M17.75 12.5v3"/>',
  hood: '<path d="M9.5 3.5h5v6l5 6.5h-15l5-6.5z"/><path d="M4.5 16v2.5h15V16"/><path d="M8 18.5v1.5M12 18.5v1.5M16 18.5v1.5"/>',
  // A heat pump's or air conditioner's outdoor unit: the fan behind its grille.
  heatpump: '<rect x="3.5" y="5.5" width="17" height="13" rx="1.5"/><circle cx="10" cy="12" r="4"/><path d="M10 12l2.5-2M10 12l-.5 3.2M10 12l-3-1"/><path d="M16.5 9.5h1.5M16.5 12h1.5M16.5 14.5h1.5"/>',
  other: '<path d="M14.5 6.5a4 4 0 0 0-5.3 5L4.5 16.2l3.3 3.3 4.7-4.7a4 4 0 0 0 5-5.3l-2.4 2.4-2.6-.4-.4-2.6z"/>',
};

/** The glyph for an appliance's kind ("dishwasher", "washing machine", "cv-ketel"…). */
export function glyphKind(kind: string): keyof typeof GLYPH {
  const k = kind.toLowerCase();
  if (/dish|vaat/.test(k)) return "dishwasher";
  // A washer-dryer washes first; a dryer on its own gets the dryer.
  if (/dry|droog|droger/.test(k) && !/wash|wasm|was-|wasdr/.test(k)) return "dryer";
  if (/wash|was|laundry/.test(k)) return "washer";
  if (/heat ?pump|warmtepomp|air ?con|airco/.test(k)) return "heatpump";
  if (/boil|ketel|heat|cv\b|combi/.test(k)) return "boiler";
  if (/freez|vries|vriez/.test(k)) return "freezer";
  if (/fridge|koel|refrig/.test(k)) return "fridge";
  if (/micro|magnetron/.test(k)) return "microwave";
  if (/hood|extract|afzuig/.test(k)) return "hood";
  if (/hob|cooktop|stove|kookplaat|fornuis|induct/.test(k)) return "hob";
  if (/oven|cook/.test(k)) return "oven";
  return "other";
}

function svg(doc: Document, inner: string, cls: string): Element {
  const t = doc.createElement("template");
  t.innerHTML = `<svg class="${cls}" viewBox="0 0 24 24" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${inner}</svg>`; // fixed markup only
  return t.content.firstElementChild!;
}

const CHEVRON = '<path d="M9 5l7 7-7 7"/>';
// The tools' icons: a display showing a code, and a play button.
const CODE = '<rect x="1.5" y="6" width="21" height="12" rx="1.5"/><text x="12" y="14.4" text-anchor="middle" font-size="7.5" font-family="ui-monospace, monospace" font-weight="600" fill="currentColor" stroke="none">E15</text>';
const PLAY = '<circle cx="12" cy="12" r="9"/><path d="M10 8.5v7l5.5-3.5z" fill="currentColor"/>';

/** An appliance's line drawing, from its kind. */
export function kindGlyph(doc: Document, kind: string): Element {
  return svg(doc, LOCK + GLYPH[glyphKind(kind)]!, "glyph");
}

export function chevron(doc: Document): Element {
  return svg(doc, CHEVRON, "chevron");
}

/** A pushed page's way back, at its top: a chevron and the name of where it goes. */
export function pageBack(doc: Document, act: string, label: string): HTMLButtonElement {
  const b = doc.createElement("button");
  b.type = "button";
  b.className = "page-back";
  b.dataset.act = act;
  const name = doc.createElement("span");
  name.textContent = label;
  b.append(svg(doc, '<path d="M15 5l-7 7 7 7"/>', "back-mark"), name);
  return b;
}

/** A row's own actions, folded behind a ⋯ at its right: label names the row for a screen reader. */
export function rowMore(doc: Document, label: string, items: Node[]): HTMLDetailsElement {
  const d = doc.createElement("details");
  d.className = "row-more";
  const sum = doc.createElement("summary");
  sum.setAttribute("aria-label", label);
  sum.append(svg(doc, '<circle cx="5" cy="12" r="1.4" fill="currentColor"/><circle cx="12" cy="12" r="1.4" fill="currentColor"/><circle cx="19" cy="12" r="1.4" fill="currentColor"/>', "more-mark"));
  const menu = doc.createElement("div");
  menu.className = "row-menu";
  menu.append(...items);
  d.append(sum, menu);
  return d;
}

// Soon and later read as one list, Coming up, each row with its date on the right.
const GROUPS: (Group | "coming")[] = ["overdue", "due", "coming", "undated"];
const sectionOf = (g: Group): Group | "coming" => (g === "soon" || g === "later" ? "coming" : g);

function cap(t: string): string {
  return `${t.charAt(0).toUpperCase()}${t.slice(1)}`;
}

/** The job to do next: the most urgent one with a date, if any is due within the next 30 days. */
export function nextUp(items: DueItem[], on: string): DueItem | null {
  const first = sortDue(items).find((d) => !d.unanswered && d.dueOn !== null);
  return first && groupOf(first, on) !== "later" ? first : null;
}

/** The home list's children. */
/** names: the home's people by id; a job given to someone says who. readOnly: a person who can only view gets no
 * Start and no "when did you last do this?". */
export function homeNodes(
  doc: Document,
  due: DueItem[],
  appliances: Appliance[],
  on: string,
  lang = "en",
  unscheduled: Unscheduled[] = [],
  names: Map<string, string> = new Map(),
  readOnly = false,
  golden = false,
): Node[] {
  const t = (en: string) => tr(lang, en);
  const tf = (en: string, v: Record<string, string | number>) => trf(lang, en, v);
  const out: Node[] = [];
  const kinds = new Map(appliances.map((a) => [a.id, a.kind]));
  const items = sortDue(due);
  const next = nextUp(items, on);
  const name = (d: DueItem) => cap(d.alias);
  const glyph = (d: DueItem) => kindGlyph(doc, kinds.get(d.applianceId) ?? "");

  if (next) {
    const g = groupOf(next, on);
    const box = doc.createElement("section");
    box.className = `next-up ${g}`;
    // The kicker, and on the same line how late it is: amber when it is due or past due.
    const top = doc.createElement("div");
    top.className = "next-top";
    const kicker = doc.createElement("p");
    kicker.className = "kicker";
    kicker.textContent = t("Next up");
    top.append(kicker);
    const late = g === "overdue" || g === "due";
    if (late) {
      const chip = doc.createElement("span");
      chip.className = "due-chip";
      chip.textContent = whenText(next, on, lang);
      top.append(chip);
    }
    const head = doc.createElement("button");
    head.type = "button";
    head.className = "next-head";
    head.dataset.act = "open-sheet";
    head.dataset.appliance = next.applianceId;
    head.dataset.job = next.jobId;
    const title = doc.createElement("strong");
    title.textContent = next.title;
    const sub = doc.createElement("span");
    sub.className = "sub";
    const who = next.assignee ? names.get(next.assignee) : undefined;
    const every = next.manual ? manualText({ ...next.manual, page: null }, lang) : next.schedule ? scheduleText(next.schedule, lang) : null;
    sub.textContent = [name(next), late ? every : whenText(next, on, lang), who && tf("for {name}", { name: who })].filter(Boolean).join(" · ");
    // The interval's page, in mono, as everywhere a page is cited.
    if (late && next.manual?.page != null) {
      const pg = doc.createElement("span");
      pg.className = "mono-page";
      pg.textContent = `p. ${next.manual.page}`;
      sub.append(" · ", pg);
    }
    const txt = doc.createElement("span");
    txt.className = "next-text";
    txt.append(title, sub);
    head.append(glyph(next), txt, svg(doc, CHEVRON, "chevron"));
    const start = doc.createElement("button");
    start.className = "ok big";
    start.dataset.act = "start-job";
    start.dataset.appliance = next.applianceId;
    start.dataset.job = next.jobId;
    start.textContent = t("Start");
    box.append(top, head);
    if (!readOnly) {
      // The two quieter answers to a reminder, as text under Start: each opens the job's sheet, where Undo and the
      // dates are.
      const acts = doc.createElement("div");
      acts.className = "next-acts";
      for (const [act, label] of [["home-done", t("Mark done")], ["home-later", t("Later…")]]) {
        const b = doc.createElement("button");
        b.type = "button";
        b.className = "text-act";
        b.dataset.act = act;
        b.dataset.appliance = next.applianceId;
        b.dataset.job = next.jobId;
        b.textContent = label!;
        acts.append(b);
      }
      box.append(start, acts);
    }
    out.push(box);
  } else if (appliances.length) {
    // Nothing due: say so, where the next job would be, rather than leaving the lists to explain it.
    const box = doc.createElement("section");
    box.className = "all-clear";
    const tick = doc.createElement("span");
    tick.className = "tick";
    tick.setAttribute("aria-hidden", "true");
    tick.textContent = "✓";
    const txt = doc.createElement("div");
    const head = doc.createElement("strong");
    head.textContent = t("Nothing due right now");
    const sub = doc.createElement("p");
    sub.className = "sub";
    sub.textContent = unscheduled.length ? t("Give the jobs below a schedule and the next one shows here.") : t("The next job shows here when it's due.");
    txt.append(head, sub);
    box.append(tick, txt);
    out.push(box);
  }

  for (const g of GROUPS) {
    const rows = items.filter((d) => d !== next && sectionOf(groupOf(d, on)) === g);
    if (!rows.length) continue;
    const h2 = doc.createElement("h2");
    h2.className = `group ${g}`;
    h2.textContent = g === "coming" ? t("Coming up") : tr(lang, GROUP_TEXT[g]);
    out.push(h2);
    for (const d of rows) {
      const row = doc.createElement("div");
      row.className = `item ${groupOf(d, on)}`;
      const open = doc.createElement("button");
      open.type = "button";
      open.className = "item-open";
      open.dataset.act = "open-sheet";
      open.dataset.appliance = d.applianceId;
      open.dataset.job = d.jobId;
      const title = doc.createElement("strong");
      title.textContent = d.title;
      const sub = doc.createElement("span");
      sub.className = "sub";
      // A schedule the home set where the manual gives none says so, so nobody takes it for the maker's.
      const who = d.assignee ? names.get(d.assignee) : undefined;
      sub.textContent = [name(d), !d.unanswered && ownOnly(d) && t("your schedule"), who && tf("for {name}", { name: who })].filter(Boolean).join(" · ");
      const txt = doc.createElement("span");
      txt.className = "item-text";
      txt.append(title, sub);
      // When it is due sits on the right, so the title keeps the width.
      const when = doc.createElement("span");
      when.className = "when";
      when.textContent = d.unanswered ? "" : whenText(d, on, lang);
      open.append(glyph(d), txt, when);
      row.append(open);
      if (d.unanswered && !readOnly) row.append(lastDoneForm(doc, d, on, lang));
      out.push(row);
    }
  }
  if (unscheduled.length) {
    const h2 = doc.createElement("h2");
    h2.className = "group none";
    h2.textContent = t("No schedule yet");
    out.push(h2);
    for (const u of unscheduled) {
      const row = doc.createElement("div");
      row.className = "item none";
      const open = doc.createElement("button");
      open.type = "button";
      open.className = "item-open";
      open.dataset.act = "open-sheet";
      open.dataset.appliance = u.applianceId;
      open.dataset.job = u.jobId;
      const title = doc.createElement("strong");
      title.textContent = u.title;
      const sub = doc.createElement("span");
      sub.className = "sub";
      sub.textContent = tf("{appliance} · the manual gives no interval", { appliance: cap(u.alias) });
      const txt = doc.createElement("span");
      txt.className = "item-text";
      txt.append(title, sub);
      open.append(svg(doc, GLYPH[glyphKind(kinds.get(u.applianceId) ?? "")]!, "glyph"), txt, svg(doc, CHEVRON, "chevron"));
      row.append(open);
      out.push(row);
    }
  }
  if (out.length) {
    const h = doc.createElement("h2");
    h.className = "way";
    h.textContent = tr(lang, KEEP_RUNNING);
    out.unshift(h);
  }
  // Something's wrong needs a card to look a code up in: a row like the jobs, not a button. Then the recorded repair,
  // for anyone who wants to see one first.
  const tool = (el: HTMLElement, icon: string, head: string, line: string): HTMLElement => {
    el.classList.add("item-open");
    const title = doc.createElement("strong");
    title.textContent = head;
    const sub = doc.createElement("span");
    sub.className = "sub";
    sub.textContent = line;
    const txt = doc.createElement("span");
    txt.className = "item-text";
    txt.append(title, sub);
    el.append(svg(doc, icon, "glyph tool"), txt, svg(doc, CHEVRON, "chevron"));
    const row = doc.createElement("div");
    row.className = "item tool";
    row.append(el);
    return row;
  };
  const wrong = appliances.some((a) => a.card_status === "checked");
  if (wrong) {
    const h = doc.createElement("h2");
    h.className = "group wrong";
    h.textContent = tr(lang, SOMETHING_WRONG);
    const b = doc.createElement("button");
    b.type = "button";
    b.dataset.act = "wrong";
    out.push(h, tool(b, CODE, t("Look up an error code"), t("What the manual says it means")));
  }
  if (golden && items.length + unscheduled.length > 0) {
    const a = doc.createElement("a");
    a.href = "?replay=golden";
    if (!wrong) out.push(Object.assign(doc.createElement("h2"), { className: "group tools", textContent: t("See one first") }));
    out.push(tool(a, PLAY, t("Watch a recorded repair"), t("A real job, start to finish")));
  }
  return out;
}

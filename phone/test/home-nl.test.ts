// @vitest-environment jsdom
// Home's job list, a job's sheet and the schedule's words in Dutch: dates as nl-NL writes them, counts and intervals
// in Dutch grammar, and no English left on a Dutch screen. The job titles, appliance names and people are data.
import { describe, expect, it } from "vitest";
import type { Appliance } from "../src/appliance.ts";
import { homeNodes } from "../src/home.ts";
import type { Person } from "../src/people.ts";
import {
  agoText,
  changeText,
  draftBody,
  dueItem,
  fromNow,
  manualText,
  scheduleText,
  sheetWhen,
  shortDate,
  whenText,
  type Change,
  type DueItem,
  type DueWire,
} from "../src/schedule.ts";
import { datePickText, lastDoneForm, sheetInitial, sheetNodes } from "../src/sheet.ts";

const ON = "2026-10-27";

function wire(over: Partial<DueWire> = {}): DueWire {
  return { appliance_id: "a1", job_id: "j", title: "Filters spoelen", due_on: "2026-11-01", reason: "scheduled", ...over };
}
const item = (over: Partial<DueWire> = {}): DueItem => dueItem(wire(over), new Map([["a1", "vaatwasser"]]), new Map(), ON);

function change(over: Partial<Change> = {}): Change {
  const st = { last_done: null, last_done_source: null, schedule: null, snoozed_until: null };
  return { id: 1, at: "2026-10-27T10:00:00Z", kind: "done", by: { kind: "app" }, before: st, after: { ...st, last_done: ON, last_done_source: "user" }, undone: false, ...over };
}

// English a Dutch screen must not show. The fixtures' data (titles, names) is Dutch or a name, so none of it matches.
const ENGLISH =
  /\b(the|your|you|and|with|this|Add|Remove|Close|Save|Cancel|Delete|Done|Edit|Back|Start|Snooze|Undo|Never|Every|History|Manual|Overdue|Due|due|days|weeks|months|by|for|Pick|Nobody|Look|Yours|today|tomorrow|since)\b/;

function text(nodes: Node[]): string {
  const box = document.createElement("div");
  box.append(...nodes);
  const attrs = [...box.querySelectorAll("[aria-label]")].map((e) => e.getAttribute("aria-label"));
  return `${box.textContent} ${attrs.join(" ")}`;
}

describe("the schedule's words in Dutch", () => {
  it("dates in nl-NL, with the year only outside this one", () => {
    expect(shortDate("2026-11-03", ON, "nl")).toMatch(/^3 nov\.?$/);
    expect(shortDate("2027-01-05", ON, "nl")).toMatch(/^5 jan\.? 2027$/);
    expect(shortDate("2026-11-03", ON)).toBe("3 Nov");
  });

  it("how far off, and how long ago, with Dutch plurals", () => {
    expect(fromNow(ON, ON, "nl")).toBe("vandaag");
    expect(fromNow("2026-10-28", ON, "nl")).toBe("morgen");
    expect(fromNow("2026-11-01", ON, "nl")).toBe("over 5 dagen");
    expect(fromNow("2026-11-20", ON, "nl")).toBe("volgende maand");
    expect(fromNow("2027-02-27", ON, "nl")).toBe("over 4 maanden");
    expect(fromNow("2029-02-27", ON, "nl")).toBe("over 2 jaar");
    expect(agoText("2026-10-26", ON, "nl")).toBe("1 dag");
    expect(agoText("2026-10-22", ON, "nl")).toBe("5 dagen");
    expect(agoText("2026-10-06", ON, "nl")).toBe("3 weken");
  });

  it("the row's and the sheet's lines", () => {
    expect(whenText(item({ due_on: "2026-10-06" }), ON, "nl")).toBe("3 weken te laat");
    expect(whenText(item({ due_on: ON }), ON, "nl")).toBe("vandaag aan de beurt");
    expect(whenText(item({ due_on: null, reason: "unanswered" }), ON, "nl")).toBe("wanneer deed je dit voor het laatst?");
    expect(whenText(item({ due_on: "2026-10-01", window_end: "2026-11-05" }), ON, "nl")).toMatch(/^nu aan de beurt, uiterlijk 5 nov\.?$/);
    expect(sheetWhen(item(), ON, "nl")).toMatch(/^Over 5 dagen · 1 nov\.?$/);
    expect(sheetWhen(item({ due_on: "2026-10-20" }), ON, "nl")).toMatch(/^Te laat · sinds 20 okt\.?$/);
    expect(sheetWhen(item({ due_on: "2026-10-01", window_end: "2027-01-05" }), ON, "nl")).toMatch(/^Nu aan de beurt · 1 okt\.? tot 5 jan\.? 2027$/);
  });

  it("intervals: elke, elk jaar, and a window", () => {
    expect(scheduleText({ kind: "every", every: 3, unit: "month" }, "nl")).toBe("elke 3 maanden");
    expect(scheduleText({ kind: "every", every: 1, unit: "week" }, "nl")).toBe("elke week");
    expect(scheduleText({ kind: "every", every: 1, unit: "year" }, "nl")).toBe("elk jaar");
    expect(scheduleText({ kind: "every", every: 2, unit: "year" }, "nl")).toBe("elke 2 jaar");
    expect(scheduleText({ kind: "window", min: 3, max: 6, unit: "month" }, "nl")).toBe("elke 3 tot 6 maanden");
    expect(manualText({ every: 30, unit: "cycle", page: 43, quote: "" }, "nl")).toBe("elke 30 beurten · p. 43");
  });

  it("a history line and the editor's refusals", () => {
    const snooze = change({ kind: "snooze", after: { last_done: null, last_done_source: null, schedule: null, snoozed_until: "2026-12-03" } });
    expect(changeText(snooze, ON, false, new Map(), "nl")).toMatch(/^Uitgesteld tot 3 dec\.?, in de app$/);
    expect(changeText(change({ by: { kind: "agent", label: "Agenda" } }), ON, false, new Map(), "nl")).toMatch(/^Als gedaan gemarkeerd op 27 okt\.?, door een AI-agent \(Agenda\)$/);
    expect(changeText(change({ by: { kind: "ha" } }), ON, false, new Map(), "nl")).toMatch(/via Home Assistant$/);
    expect(draftBody({ kind: "every", every: "x", min: "", max: "", unit: "" }, "nl")).toEqual({ error: "Typ een heel getal vanaf 1." });
    expect(draftBody({ kind: "every", every: "3", min: "", max: "", unit: "" }, "nl")).toEqual({ error: "Kies dagen, weken, maanden of jaren." });
  });
});

const appliances: Appliance[] = [{ id: "a1", alias: "vaatwasser", kind: "dishwasher", brand: "", model: "", plate_confirmed: true, card_status: "checked" }];

describe("home's list in Dutch", () => {
  const due = [
    item({ job_id: "a", title: "Filters spoelen", due_on: "2026-10-06" }),
    item({ job_id: "b", title: "Zout bijvullen", due_on: "2026-11-10", assignee: "p2" }),
    item({ job_id: "c", title: "Machine reinigen", due_on: "2027-03-01" }),
    item({ job_id: "d", title: "Sproeiarmen", due_on: null, reason: "unanswered" }),
  ];
  const unscheduled = [{ applianceId: "a1", jobId: "e", title: "Deurrubber", alias: "vaatwasser" }];
  const names = new Map([["p2", "Sam"]]);

  it("the sections, the next job and Something's wrong, all Dutch", () => {
    const nodes = homeNodes(document, due, appliances, ON, "nl", unscheduled, names);
    const all = text(nodes);
    const box = document.createElement("div");
    box.append(...nodes);
    expect(box.querySelector(".kicker")?.textContent).toBe("Hierna");
    expect(box.querySelector('[data-act="start-job"]')?.textContent).toBe("Beginnen");
    expect([...box.querySelectorAll("h2")].map((h) => h.textContent)).toEqual([
      "Houd het draaiend",
      "Binnenkort",
      "Heeft een datum nodig",
      "Nog geen schema",
      "Er is iets mis",
    ]);
    expect(all).toContain("Vaatwasser · voor Sam");
    expect(all).toContain("volgende maand");
    expect(all).toContain("Vaatwasser · de handleiding geeft geen interval");
    expect(all).toContain("Wanneer deed je dit voor het laatst?");
    expect(box.querySelector('[data-act="wrong"] strong')?.textContent).toBe("Zoek een foutcode op");
    expect(all).not.toMatch(ENGLISH);
  });

  it("English is unchanged", () => {
    const box = document.createElement("div");
    box.append(...homeNodes(document, due, appliances, ON, "en", unscheduled, names));
    expect(box.querySelector(".kicker")?.textContent).toBe("Next up");
    expect(box.querySelector("h2")?.textContent).toBe("Keep it running");
  });

  it("the last-done form, failed", () => {
    const d = { ...item({ due_on: null, reason: "unanswered" }), answer: "failed" as const };
    const f = lastDoneForm(document, d, ON, "nl");
    expect(f.querySelector("legend")?.textContent).toBe("Wanneer deed je dit voor het laatst?");
    expect(f.querySelector(".error")?.textContent).toBe("Opslaan lukte niet. Probeer het opnieuw.");
    expect(f.querySelector("input")?.getAttribute("aria-label")).toBe("Laatst gedaan: Filters spoelen");
    expect(datePickText("", "nl")).toBe("Kies een datum");
    expect(text([f])).not.toMatch(ENGLISH);
  });
});

describe("a job's sheet in Dutch", () => {
  const folk: Person[] = [
    { id: "p1", name: "Noor", role: "admin", view_only: false, me: true },
    { id: "p2", name: "Sam", role: "member", view_only: false, me: false },
  ];

  it("a due job with its manual, its own schedule, who does it, snooze and history", () => {
    const d = {
      ...item({
        due_on: "2026-10-20",
        assignee: "p2",
        snoozed_until: "2026-12-03",
        last_done: { date: "2026-07-20", source: "user" },
        manual: { every: 3, unit: "month", page: 43, quote: "Alle 3 maanden reinigen.", lang: "nl" },
        schedule: { kind: "window", min: 3, max: 6, unit: "month" },
      }),
    };
    const sh = { ...sheetInitial("a1", "j"), changes: [change({ undone: true }), change({ id: 2, kind: "undo" }), change({ id: 3 })], error: "later" as const, later: true, more: true };
    const nodes = sheetNodes(document, sh, d, ON, "nl", null, null, folk);
    const all = text(nodes);
    expect(all).toMatch(/Te laat · sinds 20 okt/);
    expect(all).toMatch(/Laatst gedaan 20 jul/);
    expect(all).toContain("Elke 3 maandenUit de handleiding · p. 43");
    expect(all).toContain("Eigenelke 3 tot 6 maanden");
    expect(all).toContain("Alleen Sam krijgt een herinnering.");
    expect(all).toMatch(/Uitgesteld tot 3 dec\.? Hef het uitstel op/);
    expect(all).toMatch(/Uitstellen kan alleen naar later\. Kies een dag na 27 okt/);
    expect(all).toContain("Ongedaan maken: Als gedaan gemarkeerd op 27 okt");
    expect(all).toContain("Noor (jij)");
    expect(all).toContain("Markeer als gedaanLater…");
    expect(all).toMatch(/Morgen.*Volgende week.*Andere dag…/);
    expect(all).toMatch(/Morgen.*Volgende week/);
    expect(all).toContain("Instellingen van de klus");
    expect(all).not.toMatch(ENGLISH);
  });

  it("the schedule editor, open on a window", () => {
    const sh = { ...sheetInitial("a1", "j"), changes: [], edit: { kind: "window" as const, every: "", min: "6", max: "3", unit: "month" as const } };
    const nodes = sheetNodes(document, sh, item({ manual: { every: 3, unit: "month", page: null, quote: "" } }), ON, "nl");
    const box = document.createElement("div");
    box.append(...nodes);
    expect(box.querySelector(".sched-edit legend")?.textContent).toBe("Hoe vaak in je huis");
    expect([...box.querySelectorAll("option")].map((o) => o.textContent)).toEqual(["dagen", "weken", "maanden", "jaar"]);
    expect(box.querySelector(".sched-edit .error")?.textContent).toBe("Het eerste getal moet het kleinste zijn.");
    expect(text(nodes)).not.toMatch(ENGLISH);
  });

  it("a job nobody has scheduled, and a sheet still loading", () => {
    const own = { applianceId: "a1", jobId: "e", title: "Deurrubber", alias: "vaatwasser" };
    const nodes = sheetNodes(document, { ...sheetInitial("a1", "e"), changes: [] }, null, ON, "nl", null, own);
    const all = text(nodes);
    expect(all).toContain("Nog geen schema");
    expect(all).toContain("Stel een schema in");
    expect(all).toContain("Nog geen wijzigingen.");
    expect(all).not.toMatch(ENGLISH);
    const loading = text(sheetNodes(document, sheetInitial("a1", "j"), null, ON, "nl"));
    expect(loading).toContain("De datums en de geschiedenis van deze klus ophalen…");
    expect(loading).not.toMatch(ENGLISH);
  });
});

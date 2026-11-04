import { describe, expect, it } from "vitest";
import {
  changeText,
  changeWhat,
  draftBody,
  draftOf,
  dueItem,
  fromNow,
  friendlyDate,
  manualText,
  ownOnly,
  parseSheetHash,
  scheduleText,
  sheetHash,
  sheetWhen,
  snoozeMin,
  sortDue,
  statusOf,
  undoable,
  whenText,
  type Change,
  type DueWire,
} from "../src/schedule.ts";

const ON = "2026-10-27";

function wire(over: Partial<DueWire> = {}): DueWire {
  return { appliance_id: "a1", job_id: "j", title: "Clean the filters", due_on: "2026-11-01", reason: "scheduled", ...over };
}

function change(over: Partial<Change> = {}): Change {
  const st = { last_done: null, last_done_source: null, schedule: null, snoozed_until: null };
  return { id: 1, at: "2026-10-27T10:00:00Z", kind: "done", by: { kind: "app" }, before: st, after: { ...st, last_done: ON, last_done_source: "user" }, undone: false, ...over };
}

describe("when a job is due, in words", () => {
  it("says how far off a date is as people say it", () => {
    expect(fromNow("2026-10-27", ON)).toBe("today");
    expect(fromNow("2026-10-28", ON)).toBe("tomorrow");
    expect(fromNow("2026-11-01", ON)).toBe("in 5 days");
    expect(fromNow("2026-12-12", ON)).toBe("in 2 months");
    expect(fromNow("2026-11-20", ON)).toBe("next month");
    expect(fromNow("2027-02-27", ON)).toBe("in 4 months");
    expect(fromNow("2029-03-01", ON)).toBe("in 2 years");
    // Two weeks or more inside this month: weeks.
    expect(fromNow("2026-10-22", "2026-10-01")).toBe("in 3 weeks");
    expect(fromNow("2026-10-29", "2026-10-01")).toBe("in 4 weeks");
  });

  it("names the job as its reminder does (1.40 due_name), else by the card's name, else by the title", () => {
    const card = new Map([["a1/j1", { name: "Clean the filter" }]]);
    const w = (over: Partial<DueWire>) => wire({ appliance_id: "a1", job_id: "j1", title: "Cleaning the filters (monthly)", ...over });
    expect(dueItem(w({ due_name: "Filter clean" }), new Map(), card, ON).title).toBe("Filter clean");
    expect(dueItem(w({}), new Map(), card, ON).title).toBe("Clean the filter");
    expect(dueItem(w({}), new Map(), new Map(), ON).title).toBe("Cleaning the filters (monthly)");
  });

  it("reads overdue, due today, and a window that is due until its latest date", () => {
    expect(whenText(dueItem(wire({ due_on: "2026-10-01" }), new Map(), new Map(), ON), ON)).toBe("overdue by 4 weeks");
    expect(whenText(dueItem(wire({ due_on: "2026-10-26" }), new Map(), new Map(), ON), ON)).toBe("overdue by 1 day");
    // A window is overdue from the day after it closes.
    expect(whenText(dueItem(wire({ due_on: "2026-07-01", window_end: "2026-10-20" }), new Map(), new Map(), ON), ON)).toBe("overdue by 6 days");
    expect(whenText(dueItem(wire({ due_on: ON }), new Map(), new Map(), ON), ON)).toBe("due today");
    const win = dueItem(wire({ due_on: "2026-10-01", window_end: "2027-01-03" }), new Map(), new Map(), ON);
    expect(win.status).toBe("due");
    expect(whenText(win, ON)).toBe("due now, by 3 Jan 2027");
    expect(whenText(dueItem(wire({ due_on: null, reason: "unanswered" }), new Map()), ON)).toBe("when did you last do this?");
    expect(whenText(dueItem(wire({ due_on: null, reason: "cycle" }), new Map()), ON)).toBe("counted in uses, not dates");
  });

  it("takes the server's status, and works it out the same way when an older server sends none", () => {
    expect(statusOf(wire({ status: "upcoming", due_on: "2000-01-01" }), ON)).toBe("upcoming");
    expect(statusOf(wire({ due_on: "2026-11-01" }), ON)).toBe("upcoming");
    expect(statusOf(wire({ due_on: "2026-10-01", window_end: "2026-10-20" }), ON)).toBe("overdue");
    expect(statusOf(wire({ due_on: null }), ON)).toBe("none");
  });

  it("sorts overdue first, then due, then by date, and the jobs with no date last", () => {
    const items = [
      wire({ job_id: "later", due_on: "2027-01-01" }),
      wire({ job_id: "none", due_on: null, reason: "unanswered" }),
      wire({ job_id: "window", due_on: "2026-09-01", window_end: "2026-12-01" }),
      wire({ job_id: "late", due_on: "2026-10-20" }),
      wire({ job_id: "soon", due_on: "2026-11-01" }),
    ].map((w) => dueItem(w, new Map(), new Map(), ON));
    expect(sortDue(items).map((d) => d.jobId)).toEqual(["late", "window", "soon", "later", "none"]);
  });
});

describe("schedules", () => {
  it("words the manual's interval with its page, and the household's own", () => {
    expect(manualText({ every: 3, unit: "month", page: 43, quote: "q" })).toBe("every 3 months · p. 43");
    expect(manualText({ every: 1, unit: "year", page: null, quote: "q" })).toBe("every year");
    expect(manualText({ every: 30, unit: "cycle", page: 12, quote: "q" })).toBe("every 30 uses · p. 12");
    expect(scheduleText({ kind: "every", every: 4, unit: "month" })).toBe("every 4 months");
    expect(scheduleText({ kind: "window", min: 3, max: 6, unit: "month" })).toBe("every 3 to 6 months");
  });

  it("builds the PUT body from the editor, or says why not", () => {
    const base = { every: "4", min: "3", max: "6", unit: "month" as const };
    expect(draftBody({ ...base, kind: "manual" })).toEqual({ body: { kind: "manual" } });
    expect(draftBody({ ...base, kind: "every" })).toEqual({ body: { kind: "every", every: 4, unit: "month" } });
    expect(draftBody({ ...base, kind: "window" })).toEqual({ body: { kind: "window", min: 3, max: 6, unit: "month" } });
    expect(draftBody({ ...base, kind: "every", every: "0" })).toEqual({ error: "Type a whole number from 1." });
    expect(draftBody({ ...base, kind: "every", every: "2.5" })).toEqual({ error: "Type a whole number from 1." });
    expect(draftBody({ ...base, kind: "window", min: "6", max: "3" })).toEqual({ error: "The first number must be the smaller one." });
    expect(draftBody({ ...base, kind: "window", min: "3", max: "3" })).toEqual({ error: "The first number must be the smaller one." });
  });

  it("lets a snooze pick only a date after today and after the current due date", () => {
    expect(snoozeMin(dueItem(wire({ due_on: "2026-11-01" }), new Map()), ON)).toBe("2026-11-02");
    expect(snoozeMin(dueItem(wire({ due_on: "2026-10-01" }), new Map()), ON)).toBe("2026-10-28");
  });
});

describe("history", () => {
  it("says what changed and who changed it", () => {
    expect(changeText(change(), ON)).toBe("Marked done on 27 Oct, in the app");
    expect(changeText(change({ kind: "snooze", by: { kind: "agent" }, after: { ...change().after, snoozed_until: "2026-12-03" } }), ON)).toBe(
      "Snoozed until 3 Dec, by an agent",
    );
    expect(changeText(change({ kind: "schedule", by: { kind: "ha" }, after: { ...change().after, schedule: { kind: "window", min: 3, max: 6, unit: "month" } } }), ON)).toBe(
      "Schedule set to every 3 to 6 months, from Home Assistant",
    );
    expect(changeWhat(change({ kind: "schedule" }), ON)).toBe("Schedule back to the manual's");
    // The token's label or the person's name, as it was then.
    expect(changeText(change({ by: { kind: "app", person_id: "p1", label: "Sam" } }), ON)).toBe("Marked done on 27 Oct, by Sam");
    expect(changeText(change({ by: { kind: "agent", person_id: null, label: "kitchen agent" } }), ON)).toBe("Marked done on 27 Oct, by an agent (kitchen agent)");
    expect(changeWhat(change({ kind: "last_done", after: { ...change().after, last_done: null, last_done_source: "never" } }), ON)).toBe("Last done set: never done");
  });

  it("offers to undo the latest change that is not an undo and not undone", () => {
    expect(undoable(null)).toBeNull();
    expect(undoable([change({ id: 3, kind: "undo" }), change({ id: 2, undone: true }), change({ id: 1, kind: "snooze" })])?.id).toBe(1);
    expect(undoable([change({ id: 2, kind: "undo" }), change({ id: 1, undone: true })])).toBeNull();
  });
});

describe("dates on buttons", () => {
  it("follow the app's language, day before month", () => {
    expect(friendlyDate("2026-11-03", "en")).toBe(["Tue", 3, "Nov"].join(" "));
    expect(friendlyDate("2026-11-03", "nl")).toBe("di 3 nov");
  });
});

describe("the sheet's address", () => {
  it("round-trips appliance and job ids, encoded", () => {
    expect(sheetHash("a 1", "clean/filters")).toBe("job/a%201/clean%2Ffilters");
    expect(parseSheetHash(`#${sheetHash("a 1", "clean/filters")}`)).toEqual({ applianceId: "a 1", jobId: "clean/filters" });
    expect(parseSheetHash("#job/a1")).toBeNull();
    expect(parseSheetHash("")).toBeNull();
    expect(parseSheetHash("#job/%E0/x")).toBeNull();
  });
});

describe("the sheet's date line", () => {
  const item = (over: Partial<DueWire>) => dueItem(wire(over), new Map(), new Map(), ON);
  it("names the date, and a window by both its ends", () => {
    expect(sheetWhen(item({ due_on: "2026-11-01" }), ON)).toBe("In 5 days · 1 Nov");
    expect(sheetWhen(item({ due_on: "2026-10-20" }), ON)).toBe("Overdue · since 20 Oct");
    expect(sheetWhen(item({ due_on: "2026-10-07", window_end: "2027-01-05" }), ON)).toBe("Due now · 7 Oct to 5 Jan 2027");
    expect(sheetWhen(item({ due_on: "2026-12-07", window_end: "2027-03-05" }), ON)).toBe("In 2 months · 7 Dec to 5 Mar 2027");
    expect(sheetWhen(item({ due_on: "2026-07-01", window_end: "2026-10-01" }), ON)).toBe("Overdue · since 1 Oct");
    expect(sheetWhen(item({ due_on: null, reason: "unanswered" }), ON)).toBe("No date yet");
  });
});

describe("a job the manual gives no interval (contracts 1.20)", () => {
  const W = { appliance_id: "a3", job_id: "bleed_radiators", title: "Bleed the radiators", due_on: "2026-11-27", reason: "scheduled" as const, last_done: { date: "2026-10-27", source: "user" as const } };
  const own = dueItem({ ...W, manual: null, schedule: { kind: "every", every: 1, unit: "year" } }, new Map([["a3", "boiler"]]), new Map(), "2026-10-27");

  it("is the household's own schedule only, and never shows a page", () => {
    expect(ownOnly(own)).toBe(true);
    expect(own.manual).toBeNull();
    expect(ownOnly({ ...own, manual: { every: 1, unit: "year", page: 4, quote: "q" } })).toBe(false);
    // An older server sends neither: that is not a schedule of the home's own.
    expect(ownOnly({ ...own, schedule: null })).toBe(false);
    expect(sheetWhen(own, "2026-10-27")).not.toMatch(/p\. null|null/);
  });

  it("starts the editor empty: no number and no unit the phone made up", () => {
    expect(draftOf(null)).toEqual({ kind: "every", every: "", min: "", max: "", unit: "" });
    // Counted in uses: the manual's number is no number of months.
    const cycle = { ...own, schedule: null, manual: { every: 30, unit: "cycle" as const, page: 12, quote: "q" } };
    expect(draftOf(cycle)).toEqual({ kind: "manual", every: "", min: "", max: "", unit: "" });
    expect(draftOf(own)).toEqual({ kind: "every", every: "1", min: "", max: "", unit: "year" });
    expect(draftBody({ kind: "every", every: "2", min: "", max: "", unit: "" })).toEqual({ error: "Pick days, weeks, months or years." });
    expect(draftBody({ kind: "window", min: "1", max: "2", every: "", unit: "" })).toEqual({ error: "Pick days, weeks, months or years." });
  });

  it("calls a schedule taken away 'stopped', not 'back to the manual's'", () => {
    const ST = { last_done: null, last_done_source: null, snoozed_until: null };
    const c = { id: 2, at: "2026-10-27T10:00:00Z", kind: "schedule" as const, by: { kind: "app" as const }, before: { ...ST, schedule: { kind: "every" as const, every: 1, unit: "year" as const } }, after: { ...ST, schedule: null }, undone: false };
    expect(changeWhat(c, "2026-10-27", true)).toBe("Schedule stopped");
    expect(changeWhat(c, "2026-10-27")).toBe("Schedule back to the manual's");
  });
});

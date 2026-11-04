// Your appliances and the card library (contracts 1.25): what the phone reads from a card and its check record, the
// citation that opens the maker's PDF at its page, and the report it sends. Stand-in data only.
import { describe, expect, it } from "vitest";
import { wantsManual } from "../src/appliance.ts";
import {
  cardView,
  faultAnswer,
  faultError,
  keepCodes,
  makerPage,
  parseDevicesHash,
  recordLines,
  reportBody,
  reportError,
  reportInitial,
  safetyAlert,
  type CardRecord,
} from "../src/devices.ts";

export const MAKER_CARD = {
  manual: { source: "maker_site", source_url: "https://acme.example/docs/wm-0000.pdf", title: "WM-0000 user manual", lang: "de", page_count: 48 },
  jobs: [
    { id: "clean_filter", title: { en: "Clean the filter", nl: "Filter reinigen" }, kind: "upkeep", steps: [{ id: "open_flap", text: { en: "Open the flap." }, page: 31 }] },
    { id: "drain_pump", title: { en: "Clear the drain pump" }, kind: "fix", steps: [{ id: "lift", text: { en: "Lift the cover." }, page: 40 }, { id: "rinse", text: { en: "Rinse it." }, page: 41 }] },
  ],
  refused: [{ id: "heater", title: { en: "Replace the heater" }, reason: "mains", page: 44 }],
  checks: { citation: { status: "pass", failures: [] }, rules: { status: "pass", failures: [] } },
};

export const RECORD: CardRecord = {
  card_sha256: "a".repeat(64),
  version: 2,
  built_at: "2026-10-20T09:00:00Z",
  shared: true,
  private_reason: null,
  reviewed: { by_person: false, at: null },
  homes: 3,
  quotes: { checked: 17, passed: 16 },
  reports_open: { total: 0, safety: 0 },
};

describe("the card, as the phone shows it", () => {
  it("keeps the maker's https URL, and opens it at the cited page", () => {
    const v = cardView(MAKER_CARD, "en")!;
    expect(v.manual).toEqual({ source: "maker_site", url: "https://acme.example/docs/wm-0000.pdf", title: "WM-0000 user manual", lang: "de", pages: 48 });
    expect(makerPage(v.manual, 31)).toBe("https://acme.example/docs/wm-0000.pdf#page=31");
    expect(v.jobs.map((j) => [j.id, j.kind, j.steps.length])).toEqual([["clean_filter", "upkeep", 1], ["drain_pump", "fix", 2]]);
    expect(v.refused).toEqual([{ id: "heater", title: "Replace the heater", reason: "mains", page: 44 }]);
    expect(cardView(MAKER_CARD, "nl")!.jobs[0]!.title).toBe("Filter reinigen");
    expect(v.notBuilt).toEqual([]);
  });

  it("keeps a procedure the build couldn't make into steps apart from the jobs refused for safety (contracts 1.27)", () => {
    const v = cardView(
      {
        ...MAKER_CARD,
        refused: [
          { reason: "gas", page: 50 },
          { reason: "not_built", heading: " Den Türgriff austauschen ", page: 38 },
          { reason: "not_built", page: 39 },
        ],
      },
      "en",
    )!;
    expect(v.refused).toEqual([{ id: "", title: "", reason: "gas", page: 50 }]);
    expect(v.notBuilt).toEqual([{ heading: "Den Türgriff austauschen", page: 38 }]);
    const lines = recordLines(v, RECORD, "2026-10-27");
    expect(lines).toContain("1 job left out: it works on the gas supply.");
    expect(lines).toContain("1 procedure in the manual couldn't be made into steps; this page points at its page.");
  });

  it("never links an upload, a URL that isn't https, or a card from before 1.23 without one", () => {
    const up = cardView({ ...MAKER_CARD, manual: { source: "household_upload", source_url: "urn:fixit:upload:" + "b".repeat(64), lang: "en", page_count: 12 } }, "en")!;
    expect(up.manual.url).toBeNull();
    expect(makerPage(up.manual, 3)).toBeNull();
    const http = cardView({ ...MAKER_CARD, manual: { ...MAKER_CARD.manual, source_url: "http://acme.example/m.pdf" } }, "en")!;
    expect(makerPage(http.manual, 3)).toBeNull();
    // Before 1.23 there is no source; those cards were all fetched from the maker's site.
    const old = cardView({ manual: { source_url: "https://acme.example/m.pdf#page=2", lang: "en", page_count: 6 }, jobs: [] }, "en")!;
    expect(makerPage(old.manual, 4)).toBe("https://acme.example/m.pdf#page=4");
    expect(cardView(null, "en")).toBeNull();
    expect(cardView({ jobs: [] }, "en")).toBeNull();
  });

  it("reads #devices and #devices/<id>, and nothing else", () => {
    expect(parseDevicesHash("#devices")).toEqual({ applianceId: null });
    expect(parseDevicesHash("#devices/a%201")).toEqual({ applianceId: "a 1" });
    expect(parseDevicesHash("#job/a1/x")).toBeNull();
    expect(parseDevicesHash("")).toBeNull();
  });
});

describe("how this card was checked", () => {
  const v = cardView(MAKER_CARD, "en")!;
  const today = "2026-10-27";

  it("says, from the record only: the manual, when built, the quotes, the rules, what was left out, review, homes, reports", () => {
    expect(recordLines(v, RECORD, today)).toEqual([
      "Built from “WM-0000 user manual” (48 pages · in German). The maker's own PDF, on acme.example. It opens on the maker's site.",
      expect.stringMatching(/^Built .+, version 2\.$/),
      "Code checked every quote against the text on its page: 16 of 17 matched, and the card keeps only those.",
      "Every step passed the safety rules.",
      "1 job left out: it means opening the mains wiring.",
      "No person has reviewed this card yet; the checks above are code.",
      "Shared with every home that has this model: 3 homes use it.",
      "No open reports.",
    ]);
  });

  it("names why a card is kept to one home, and says an open safety report at the top", () => {
    const priv = { ...RECORD, shared: false, private_reason: "version_conflict", homes: 1, reviewed: { by_person: true, at: "2026-10-26" }, reports_open: { total: 2, safety: 1 } };
    const lines = recordLines(v, priv, today);
    expect(lines).toContain("Kept to your home: this copy of the manual differs from the one other homes use, so it waits for review.");
    expect(lines.some((l) => l.startsWith("A person read this card against the manual"))).toBe(true);
    expect(lines).toContain("2 open reports, 1 about safety. A report never changes the card by itself.");
    expect(safetyAlert(priv)).toBe("A home has reported something unsafe on this card. It is waiting for a person to check it against the manual.");
    expect(safetyAlert(RECORD)).toBeNull();
    expect(safetyAlert("none")).toBeNull();
  });

  it("says how many fault codes were read from a drawing and wait for a person (contracts 1.26)", () => {
    const drawn = { ...RECORD, shared: false, private_reason: "codes_unconfirmed", homes: 1, unconfirmed_codes: 2 };
    const lines = recordLines(v, drawn, today);
    expect(lines).toContain("2 fault codes were read from a drawing in the manual, not its text; no person has confirmed them yet.");
    expect(lines).toContain("Kept to your home until a person confirms the fault codes read from the manual's drawings.");
    expect(recordLines(v, RECORD, today).join(" ")).not.toMatch(/drawing/);
  });

  it("says how many of our own restore steps wait for a person to accept them (contracts 1.28)", () => {
    const ours = { ...RECORD, shared: false, private_reason: "steps_unaccepted", homes: 1, unaccepted_steps: 1 };
    const lines = recordLines(v, ours, today);
    expect(lines).toContain("1 step is ours, not from the manual: added by our code to put the appliance back in use, and no person has accepted it yet.");
    expect(lines).toContain("Kept to your home until a person accepts the steps we added to it.");
    expect(recordLines(v, RECORD, today).join(" ")).not.toMatch(/ours/);
  });

  it("without a record (404), says what the card itself shows and no more", () => {
    const lines = recordLines(v, "none", today);
    expect(lines).toContain("Code checked every quote against the text on its page.");
    expect(lines.join(" ")).not.toMatch(/homes|reviewed|open report/);
    expect(lines.at(-1)).toBe("The rest of the check record isn't available for this card.");
  });
});

describe("report a problem", () => {
  it("builds each kind's body as the contract asks, and says what is missing", () => {
    const d = reportInitial();
    expect(reportBody(d, 48)).toEqual({ error: "Pick what's wrong." });
    expect(reportBody({ ...d, kind: "wrong_step" }, 48)).toEqual({ error: "Pick the step that's wrong." });
    expect(reportBody({ ...d, kind: "wrong_step", step: "drain_pump/lift", note: "  It's the other cover. " }, 48)).toEqual({
      body: { kind: "wrong_step", job_id: "drain_pump", step_id: "lift", note: "It's the other cover." },
    });
    expect(reportBody({ ...d, kind: "wrong_page", page: "49" }, 48)).toEqual({ error: "Type the page the manual should show, 1 to 48." });
    expect(reportBody({ ...d, kind: "wrong_page", page: "12" }, 48)).toEqual({ body: { kind: "wrong_page", page: 12 } });
    expect(reportBody({ ...d, kind: "other" }, 48)).toEqual({ error: "Say what's wrong in a few words." });
    expect(reportBody({ ...d, kind: "unsafe" }, 48)).toEqual({ body: { kind: "unsafe" } });
    expect(reportBody({ ...d, kind: "not_mine" }, 48)).toEqual({ body: {} });
    expect(reportBody({ ...d, kind: "other", note: "x".repeat(600) }, 48)).toEqual({ body: { kind: "other", note: "x".repeat(500) } });
  });

  it("words the server's refusals", () => {
    expect(reportError(429, "too_many_reports")).toMatch(/20 reports waiting/);
    expect(reportError(403, undefined)).toMatch(/only view this home/);
    expect(reportError(409, "no_card")).toMatch(/no card/);
  });

  it("after not_mine the manual is asked for; a maker's terms ask for nothing", () => {
    expect(wantsManual({ kind: "lookup_failed", reason: "not_mine", text: {} })).toBe(true);
    expect(wantsManual({ kind: "lookup_failed", reason: "maker_terms", text: {} })).toBe(false);
  });
});

describe("Something's wrong: the meaning answer", () => {
  it("keeps the card's cited rows in the app's language, and drops what is malformed", () => {
    const a = faultAnswer(
      {
        code: "e24",
        matches: [
          { code: "E24", job_id: "drain_pump", title: { en: "Clear the drain pump", nl: "Pomp vrijmaken" }, page: 40, quote: "E24: pump", meaning: { en: "Not draining", nl: "Pompt niet af" }, cause: null, remedy: { en: "Clean it" }, remedy_kind: "user" },
          { code: "E25", title: { en: "No page" }, quote: "x" },
        ],
        candidates: ["E20", "E21", "E22", "E23", "E24", "E26"],
        refused: [],
        table_page: 0,
        notice: null,
        landlord: true,
      },
      "nl",
    )!;
    expect(a.matches).toEqual([
      { code: "E24", jobId: "drain_pump", title: "Pomp vrijmaken", page: 40, quote: "E24: pump", quoteEn: null, meaning: "Pompt niet af", cause: null, remedy: "Clean it", who: "user", unconfirmed: false },
    ]);
    expect(a.candidates).toHaveLength(5);
    expect(a.tablePage).toBeNull();
    expect(a.landlord).toBe(true);
    expect(faultAnswer(null, "en")).toBeNull();
    expect(faultError(404)).toMatch(/no checked card/);
    expect(faultError(422)).toMatch(/longer than any code/);
    const drawn = faultAnswer({ code: "E20", matches: [{ code: "E20", job_id: null, title: { en: "Drain" }, page: 9, quote: "q", unconfirmed: true }], candidates: [], refused: [], table_page: 9, notice: null, landlord: false }, "en")!;
    expect(drawn.matches[0]!.unconfirmed).toBe(true);
  });

  it("a code in a title never breaks at its hyphen; other hyphens still can", () => {
    expect(keepCodes("Clean the inlet filter (fault E:32-00)")).toBe("Clean the inlet filter (fault E:32\u201100)");
    expect(keepCodes("Reset (faults E:01-00, E:90-10)")).toBe("Reset (faults E:01\u201100, E:90\u201110)");
    expect(keepCodes("Self-clean, F21 and WM-0000")).toBe("Self-clean, F21 and WM-0000");
  });
});

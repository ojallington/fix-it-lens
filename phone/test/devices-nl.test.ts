// @vitest-environment jsdom
// Devices, the appliance page, the code lookup, the card check and the report form, in Dutch. Stand-in data only.
import { describe, expect, it } from "vitest";
import { LANG_NAMES, REFUSED_TEXT, type Appliance } from "../src/appliance.ts";
import {
  applianceNodes,
  cardView,
  devicesInitial,
  devicesNodes,
  FAULT_TEXT,
  faultAnswer,
  faultError,
  manualFacts,
  NO_SCHEDULE_TEXT,
  NOT_BUILT_TEXT,
  PRIVATE_TEXT,
  recordLines,
  reportBody,
  reportError,
  reportInitial,
  REPORT_KIND_TEXT,
  REPORT_TEXT,
  safetyAlert,
  sourceText,
  stateLine,
  type CardRecord,
  type DevicesState,
} from "../src/devices.ts";
import type { DueItem } from "../src/schedule.ts";
import { ALL_NL } from "../src/words.ts";

const TODAY = "2026-10-27";

const CARD = {
  manual: { source: "maker_site", source_url: "https://acme.example/docs/wm-0000.pdf", title: "WM-0000 Gebrauchsanleitung", lang: "de", page_count: 48 },
  jobs: [
    { id: "clean_filter", title: { en: "Clean the filter", nl: "Filter reinigen" }, kind: "upkeep", steps: [{ id: "open_flap", text: { nl: "Klep openen." }, page: 31 }] },
    {
      id: "drain_pump",
      title: { en: "Clear the drain pump", nl: "Afvoerpomp vrijmaken" },
      kind: "fix",
      steps: [
        { id: "lift", text: { nl: "Deksel optillen." }, page: 40 },
        { id: "rinse", text: { nl: "Afspoelen." }, page: 41 },
      ],
    },
  ],
  refused: [
    { id: "heater", title: { en: "Replace the heater", nl: "Verwarming vervangen" }, reason: "mains", page: 44 },
    { reason: "not_built", heading: "Türgriff austauschen", page: 38 },
  ],
  checks: { citation: { status: "pass", failures: [] }, rules: { status: "pass", failures: [] } },
};

const RECORD: CardRecord = {
  card_sha256: "a".repeat(64),
  version: 2,
  built_at: "2026-10-20T09:00:00Z",
  shared: false,
  private_reason: "codes_unconfirmed",
  reviewed: { by_person: false, at: null },
  homes: 1,
  quotes: { checked: 17, passed: 16 },
  reports_open: { total: 3, safety: 2 },
  unconfirmed_codes: 2,
  unaccepted_steps: 1,
};

const APPLIANCE: Appliance = { id: "a1", alias: "vaatwasser", kind: "dishwasher", brand: "Acme", model: "WM-0000", plate_confirmed: true, card_status: "checked" };

const DUE: DueItem = {
  applianceId: "a1",
  jobId: "clean_filter",
  title: "Filter reinigen",
  alias: "vaatwasser",
  dueOn: "2026-11-03",
  reason: "interval",
  status: "soon",
  windowEnd: null,
  snoozedUntil: null,
  lastDone: null,
  manual: null,
  schedule: null,
  assignee: null,
  unanswered: false,
  answer: null,
  picked: "",
} as unknown as DueItem;

/** The data in the fixtures, which stays as given; what is left must be the phone's own Dutch. */
const DATA = ["WM-0000 Gebrauchsanleitung", "Türgriff austauschen", "Verwarming vervangen", "Afvoerpomp vrijmaken", "Filter reinigen", "Vaatwasser", "Acme WM-0000", "Acme", "WM-0000", "acme.example"];
// The names.ts words are in the other area's table; until both land they still read in English here.
const NAMES = ["Keep it running", "Something's wrong", "Upkeep", "Fix"];
const ENGLISH = /\b(the|your|you|and|with|this|Add|Remove|Close|Save|Cancel|Delete|Done|Edit|Back|page|pages|manual|card|step|steps|report|home|appliance|appliances|Send|Pick|Type)\b/;

function strayEnglish(nodes: Node[]): string[] {
  const doc = document;
  const box = doc.createElement("div");
  box.append(...nodes);
  const texts: string[] = [box.textContent ?? ""];
  for (const e of box.querySelectorAll("[aria-label]")) texts.push(e.getAttribute("aria-label")!);
  for (const e of box.querySelectorAll("optgroup")) texts.push((e as HTMLOptGroupElement).label);
  return texts.flatMap((t) => {
    let s = t;
    for (const d of [...DATA, ...NAMES]) s = s.split(d).join(" ");
    const m = ENGLISH.exec(s);
    return m ? [`${m[0]} in: ${s.slice(Math.max(0, m.index - 40), m.index + 40)}`] : [];
  });
}

function page(st: Partial<DevicesState> = {}, a: Appliance = APPLIANCE) {
  const card = cardView(CARD, "nl")!;
  return { a, due: [DUE], unscheduled: [{ applianceId: "a1", jobId: "descale", title: "Ontkalken", alias: "vaatwasser" }], card, st: { ...devicesInitial("a1"), record: RECORD, ...st }, today: TODAY, lang: "nl" };
}

describe("every fixed line of Devices has its Dutch", () => {
  it("the texts tables, drawn through tr, are all in the table", () => {
    const lines = [
      ...Object.values(FAULT_TEXT),
      ...Object.values(PRIVATE_TEXT),
      ...Object.values(REPORT_TEXT),
      ...Object.values(REPORT_KIND_TEXT),
      ...Object.values(REFUSED_TEXT),
      ...Object.values(LANG_NAMES),
      ...Object.values(NOT_BUILT_TEXT),
      NO_SCHEDULE_TEXT,
      "Kept to your home.",
      "our step",
    ];
    expect(lines.filter((l) => !(l in ALL_NL))).toEqual([]);
  });
});

describe("Devices in Dutch", () => {
  it("the list of appliances", () => {
    const nodes = devicesNodes(document, [APPLIANCE], [DUE], TODAY, false, "nl");
    const text = nodes.map((n) => n.textContent).join("\n");
    expect(text).toContain("Je apparaten");
    expect(text).toContain("Apparaat toevoegen");
    expect(text).toContain("Volgende: Filter reinigen");
    expect(strayEnglish(nodes)).toEqual([]);
    // The Add button opens the two ways to add one.
    const add = devicesNodes(document, [APPLIANCE], [DUE], TODAY, false, "nl", true);
    const addText = add.map((n) => n.textContent).join("\n");
    expect(addText).toContain("Gebruik de camera");
    expect(addText).toContain("Richt de camera op het typeplaatje");
    expect(strayEnglish(add)).toEqual([]);
  });

  it("an empty list, and the list asked from Something's wrong", () => {
    expect(devicesNodes(document, [], [], TODAY, false, "nl").map((n) => n.textContent).join("\n")).toContain("Nog geen apparaten. Voeg hierboven het eerste toe.");
    const wrong = devicesNodes(document, [APPLIANCE], [], TODAY, true, "nl");
    expect(wrong.map((n) => n.textContent).join("\n")).toContain("Welk apparaat? Op zijn pagina zoek ik de code op in zijn eigen handleiding.");
    expect(strayEnglish(wrong)).toEqual([]);
  });

  it("each state line of an appliance", () => {
    expect(stateLine(APPLIANCE, [], TODAY, "nl")).toBe("Klaar voor gebruik");
    expect(stateLine({ ...APPLIANCE, card_status: "pages_only" }, [], TODAY, "nl")).toBe("Begeleid vanuit de pagina's van de handleiding");
    expect(stateLine({ ...APPLIANCE, card_status: "failed" }, [], TODAY, "nl")).toBe("Instellen lukte niet");
    expect(stateLine({ ...APPLIANCE, card_status: "ingesting" as Appliance["card_status"] }, [], TODAY, "nl")).toBe("De handleiding lezen…");
    expect(stateLine(APPLIANCE, [], TODAY)).toBe("Ready");
  });
});

describe("the appliance page in Dutch", () => {
  it("its jobs, its manual, how its card was checked, and the safety line", () => {
    const nodes = applianceNodes(document, page());
    const text = nodes.map((n) => n.textContent).join("\n");
    expect(text).toContain("Je apparaten");
    expect(text).toContain("2 huizen hebben iets onveiligs op deze kaart gemeld.");
    expect(text).toContain("Nog geen schema · de handleiding geeft geen interval");
    expect(text).toContain("Reparaties in de handleiding");
    expect(text).toContain("2 stappen · begint op p. 40");
    expect(text).toContain("Voor een vakman");
    expect(text).toContain("Dan moet de elektrische bedrading open · p. 44");
    expect(text).toContain("De handleiding behandelt ook Türgriff austauschen op p. 38; we konden er geen stappen van maken.");
    expect(text).toContain("48 pagina's · in het Duits");
    expect(text).toContain("De eigen pdf van de fabrikant, op acme.example. Hij opent op de site van de fabrikant.");
    expect(text).toContain("Zo is deze kaart gecontroleerd");
    expect(text).toContain("Meld een probleem");
    const cite = nodes.flatMap((n) => [...(n as HTMLElement).querySelectorAll?.("a.cite-link") ?? []])[0]!;
    expect(cite.getAttribute("aria-label")).toBe("p. 44: opent de pdf van de fabrikant op pagina 44");
    expect(strayEnglish(nodes)).toEqual([]);
  });

  it("a code looked up: the answer, what to pick, and the start button", () => {
    const answer = faultAnswer(
      {
        code: "E24",
        matches: [
          { code: "E24", job_id: "drain_pump", title: { nl: "Afvoerpomp vrijmaken" }, page: 40, quote: "Pumpe verstopft", quote_en: "Pump blocked", meaning: { nl: "Pomp verstopt" }, cause: { nl: "Vuil" }, remedy: { nl: "Schoonmaken" }, remedy_kind: "user", unconfirmed: true },
          { code: "E30", page: 41, quote: "Wasser im Boden", meaning: { nl: "Water in de bodem" }, remedy_kind: "technician" },
        ],
        candidates: ["E25"],
        landlord: true,
      },
      "nl",
    );
    const nodes = applianceNodes(document, page({ fault: { code: "E24", busy: false, answer, error: null } }));
    const text = nodes.map((n) => n.textContent).join("\n");
    expect(text).toContain("Code op het display");
    expect(text).toContain("Opzoeken");
    expect(text).toContain("In het EngelsPump blocked");
    expect(text).toContain("De handleiding, p. 40");
    expect(text).toContain("Oorzaak: Vuil");
    expect(text).toContain("Wat te doen: Schoonmaken");
    expect(text).toContain("Start de reparatie: Afvoerpomp vrijmaken");
    expect(text).toContain("Je huurt: volgens de regels is dit iets voor je verhuurder.");
    expect(text).toContain("De handleiding laat dit aan een monteur over.");
    expect(text).toContain("Bedoelde je:");
    const data = ["Pumpe verstopft", "Pump blocked", "Wasser im Boden", "Water in de bodem", "Pomp verstopt"];
    expect(strayEnglish(nodes).filter((l) => !data.some((d) => l.includes(d)))).toEqual([]);
  });

  it("the app's fixed error lines are drawn in Dutch", () => {
    const nodes = applianceNodes(document, page({ fault: { code: "", busy: true, answer: null, error: FAULT_TEXT.empty } }));
    const text = nodes.map((n) => n.textContent).join("\n");
    expect(text).toContain("Typ eerst de code.");
    expect(text).toContain("Zoeken…");
    expect(faultError(404, "nl")).toBe("Dit apparaat heeft nog geen gecontroleerde kaart, dus ik kan geen codes opzoeken.");
    expect(faultError(404)).toBe(FAULT_TEXT.noCard);
  });

  it("an appliance that couldn't be set up", () => {
    const nodes = applianceNodes(document, { ...page(), card: null, a: { ...APPLIANCE, card_status: "failed" } });
    expect(nodes.map((n) => n.textContent).join("\n")).toContain("Ik kon het niet instellen.");
    expect(strayEnglish(nodes)).toEqual([]);
  });
});

describe("how this card was checked, in Dutch", () => {
  it("counts each line right in both languages", () => {
    const v = cardView(CARD, "nl")!;
    const nl = recordLines(v, RECORD, TODAY, "nl");
    expect(nl).toContain("Code heeft elk citaat vergeleken met de tekst op zijn pagina: bij 16 van de 17 klopte het, en de kaart houdt alleen die.");
    expect(nl).toContain("2 foutcodes zijn uit een tekening in de handleiding gelezen, niet uit de tekst; nog geen mens heeft ze bevestigd.");
    expect(nl).toContain("1 klus weggelaten: dan moet de elektrische bedrading open.");
    expect(nl).toContain("1 procedure uit de handleiding kon niet in stappen worden omgezet; hier staat naar welke pagina.");
    expect(nl).toContain("Blijft bij je huis tot een mens de foutcodes bevestigt die uit de tekeningen in de handleiding zijn gelezen.");
    expect(nl).toContain("3 openstaande meldingen, 2 over veiligheid. Een melding verandert de kaart nooit vanzelf.");
    expect(nl[0]).toBe("Gemaakt van “WM-0000 Gebrauchsanleitung” (48 pagina's · in het Duits). De eigen pdf van de fabrikant, op acme.example. Hij opent op de site van de fabrikant.");
    const one = recordLines(v, { ...RECORD, unconfirmed_codes: 1, unaccepted_steps: 2, reports_open: { total: 1, safety: 0 } }, TODAY, "nl");
    expect(one).toContain("1 foutcode is uit een tekening in de handleiding gelezen, niet uit de tekst; nog geen mens heeft hem bevestigd.");
    expect(one).toContain("1 openstaande melding. Een melding verandert de kaart nooit vanzelf.");
    expect(one.some((l) => l.startsWith("2 stappen zijn van ons"))).toBe(true);
    const en = recordLines(v, { ...RECORD, unconfirmed_codes: 1 }, TODAY);
    expect(en).toContain("1 fault code was read from a drawing in the manual, not its text; no person has confirmed it yet.");
    expect(en).toContain("3 open reports, 2 about safety. A report never changes the card by itself.");
  });

  it("the manual's facts and source", () => {
    const m = cardView(CARD, "nl")!.manual;
    expect(manualFacts({ ...m, pages: 1, lang: "nl" }, "nl")).toBe("1 pagina · in het Nederlands");
    expect(manualFacts({ ...m, pages: 1, lang: "nl" })).toBe("1 page · in Dutch");
    expect(sourceText({ ...m, source: "household_upload", url: null }, "nl")).toBe("Je eigen kopie van de pdf. Alleen jouw huis kan hem openen.");
    expect(safetyAlert({ ...RECORD, reports_open: { total: 1, safety: 1 } }, "nl")).toBe(
      "Een huis heeft iets onveiligs op deze kaart gemeld. Hij wacht tot een mens hem met de handleiding vergelijkt.",
    );
  });
});

describe("Report a problem in Dutch", () => {
  it("the form, with each field it can show", () => {
    for (const kind of ["wrong_step", "wrong_page", "not_mine", "other"] as const) {
      const nodes = applianceNodes(document, page({ report: { ...reportInitial(), kind, error: REPORT_TEXT.pickStep } }));
      const text = nodes.map((n) => n.textContent).join("\n");
      expect(text).toContain("Een melding verandert de kaart nooit.");
      expect(text).toContain("Een paginanummer klopt niet");
      expect(text).toContain("Kies de stap die niet klopt.");
      expect(text).toContain("Annuleren");
      if (kind === "wrong_step") expect(text).toContain("Kies een stap");
      if (kind === "wrong_page") expect(text).toContain("De juiste pagina in de handleiding");
      if (kind === "not_mine") expect(text).toContain("Stop met deze kaart");
      if (kind === "other") expect(text).toContain("Verstuur de melding");
      const steps = ["Klep openen.", "Deksel optillen.", "Afspoelen."];
      expect(strayEnglish(nodes).filter((l) => !steps.some((s) => l.includes(s)))).toEqual([]);
    }
  });

  it("what it says before and after sending", () => {
    expect(reportBody({ ...reportInitial(), kind: "wrong_page", page: "99" }, 48, "nl")).toEqual({
      error: "Typ de pagina die de handleiding zou moeten tonen, 1 tot en met 48.",
    });
    expect(reportBody(reportInitial(), 48, "nl")).toEqual({ error: "Kies wat er mis is." });
    expect(reportError(429, undefined, "nl")).toBe("Je huis heeft al 20 meldingen die op controle wachten. Probeer het opnieuw als er een paar beantwoord zijn.");
    expect(reportError(0, undefined, "nl")).toBe("Versturen lukte niet. Controleer de verbinding en probeer het opnieuw.");
    const done = applianceNodes(document, page({ done: "sent" })).map((n) => n.textContent).join("\n");
    expect(done).toContain("Verstuurd. Dank je.");
  });
});

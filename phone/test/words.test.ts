// @vitest-environment jsdom
// The app in Dutch: every line the phone writes itself has its Dutch, counts are written out (1 stap, 3 stappen), and
// an English phone is untouched.
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import type { S2C } from "../../contracts/ts/src/ws.ts";
import { micErrorText } from "../src/audio.ts";
import { initial, reduce } from "../src/session.ts";
import {
  ASK_TEXT,
  barText,
  CAMERA_TEXT,
  completeText,
  HANDS_TEXT,
  HOLD_TEXT,
  INSTALL_WHY,
  localInitial,
  mount,
  NOTICE_TEXT,
  OFFLINE_TEXT,
  REFUSED_TEXT,
  render,
  NO_HOUSEHOLD_TEXT,
  REPLAY_MISSING_TEXT,
  REPLAY_NOTICE_TEXT,
  shareText,
  SNOOZE_FAILED_TEXT,
  SNOOZED_TEXT,
  SKELETON,
  talkText,
} from "../src/ui.ts";
import { ringWarning, VIEW_TEXT } from "../src/viewcheck.ts";
import { ALL_NL, NL, tr, trf } from "../src/words.ts";
import { NL_DEVICES } from "../src/words-devices.ts";
import { NL_HOME } from "../src/words-home.ts";
import { NL_PEOPLE } from "../src/words-people.ts";
import { NL_SHELL } from "../src/words-shell.ts";
import { readdirSync } from "node:fs";
import { PEOPLE_TEXT } from "../src/people.ts";
import { PDF_TEXT, REFUSED_TEXT as REFUSED_JOB_TEXT, STAGE_TEXT } from "../src/appliance.ts";
import { RETURN_LINK_TEXT } from "../src/household.ts";

const src = (f: string) => readFileSync(resolve("src", f), "utf8") // vitest runs in phone/;

describe("every line of the session has its Dutch", () => {
  it("the fixed tables, the skeleton's marked parts, and each line render and the app ask for by name", () => {
    const lines = [
      ...Object.values(NOTICE_TEXT),
      ...Object.values(REPLAY_NOTICE_TEXT),
      ...Object.values(HOLD_TEXT),
      ...Object.values(ASK_TEXT),
      ...Object.values(VIEW_TEXT),
      ...Object.values(INSTALL_WHY),
      OFFLINE_TEXT,
      CAMERA_TEXT,
      HANDS_TEXT,
      REFUSED_TEXT,
      PEOPLE_TEXT.viewerSession,
    ] as string[];
    const doc = new DOMParser().parseFromString(`<main>${SKELETON}</main>`, "text/html");
    for (const el of doc.querySelectorAll<HTMLElement>("[data-t]")) lines.push(el.innerHTML);
    for (const el of doc.querySelectorAll<HTMLElement>("[data-t-aria]")) lines.push(el.getAttribute("aria-label") ?? el.getAttribute("alt") ?? "");
    for (const [f, re] of [
      ["ui.ts", /\bt\("((?:[^"\\]|\\.)+)"\)/g],
      ["ui.ts", /\bt\([^"()]*\? "((?:[^"\\]|\\.)+)" : "((?:[^"\\]|\\.)+)"\)/g],
      ["app.ts", /tr\(this\.l\.lang, "((?:[^"\\]|\\.)+)"\)/g],
      ["audio.ts", /tr\(lang, "((?:[^"\\]|\\.)+)"\)/g],
    ] as const) {
      for (const m of src(f).matchAll(re)) lines.push(...m.slice(1).filter((x): x is string => !!x).map((x) => x.replace(/\\"/g, '"')));
    }
    expect(lines.length).toBeGreaterThan(70);
    const missing = [...new Set(lines)].filter((x) => !(x in ALL_NL));
    expect(missing).toEqual([]);
  });

  it("an English phone reads English, and a line with no Dutch stays as it was", () => {
    expect(tr("en-GB", "Hold to talk")).toBe("Hold to talk");
    expect(tr("nl-BE", "Hold to talk")).toBe("Houd vast om te praten");
    expect(tr("nl", "A line nobody translated")).toBe("A line nobody translated");
  });
});

describe("the whole app has its Dutch", () => {
  it("every line the code asks to translate, in any file, has its Dutch", () => {
    // t("…") is a drawing's own shorthand for tr(lang, "…"); tr and trf take the line second.
    const res = [/\bt\(\s*"((?:[^"\\]|\\.)+)"\s*[,)]/g, /\btrf?\([^,()]+,\s*"((?:[^"\\]|\\.)+)"/g, /\btf\(\s*"((?:[^"\\]|\\.)+)"/g,
      // and either side of a choice: t(x ? "…" : "…"), tr(lang, x ? "…" : "…").
      /\b(?:t\(|trf?\([^,()]+,)\s*[^"()]*\?\s*"((?:[^"\\]|\\.)+)"\s*:\s*"((?:[^"\\]|\\.)+)"/g,
    ];
    const missing: string[] = [];
    for (const f of readdirSync(resolve("src")).filter((x) => x.endsWith(".ts") && !x.startsWith("words"))) {
      for (const re of res)
        for (const m of src(f).matchAll(re))
          for (const x of m.slice(1)) if (x && !(x.replace(/\\"/g, '"') in ALL_NL)) missing.push(`${f}: ${x}`);
    }
    expect(missing).toEqual([]);
  });

  it("every placeholder in the skeleton, and every fixed text the app draws through tr by name, has its Dutch", () => {
    const doc = new DOMParser().parseFromString(`<main>${SKELETON}</main>`, "text/html");
    const lines = [
      ...[...doc.querySelectorAll<HTMLElement>("[data-t-ph]")].map((el) => el.getAttribute("placeholder") ?? ""),
      SNOOZED_TEXT,
      SNOOZE_FAILED_TEXT,
      REPLAY_MISSING_TEXT,
      NO_HOUSEHOLD_TEXT,
      ...Object.values(STAGE_TEXT),
      ...Object.values(PDF_TEXT),
      ...Object.values(REFUSED_JOB_TEXT),
      ...Object.values(RETURN_LINK_TEXT),
    ];
    expect(lines.filter((x) => !(x in ALL_NL))).toEqual([]);
  });

  it("a line in two tables says the same in both, and keeps its placeholders", () => {
    const tables = [NL, NL_SHELL, NL_HOME, NL_DEVICES, NL_PEOPLE];
    const seen = new Map<string, string>();
    const clash: string[] = [];
    for (const t of tables)
      for (const [en, nl] of Object.entries(t)) {
        if (seen.has(en) && seen.get(en) !== nl) clash.push(en);
        seen.set(en, nl);
        const holes = (x: string) => [...x.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join();
        if (holes(en) !== holes(nl)) clash.push(`placeholders: ${en}`);
      }
    expect(clash).toEqual([]);
    expect(trf("nl", "{n} unknown {x}", { n: 2 })).toBe("2 unknown {x}");
  });
});

describe("counts are written out in both languages", () => {
  it("Job done: one step or several, checked or ended by the person", () => {
    expect(completeText(1, 0, "your", "nl")).toBe("Gecontroleerd met je handleiding.");
    expect(completeText(3, 0, "the", "nl")).toBe("Alle 3 stappen gecontroleerd met de handleiding.");
    expect(completeText(1, 1, "your", "nl")).toBe("Klaar. Je hebt de stap zelf bevestigd.");
    expect(completeText(3, 1, "your", "nl")).toBe("Alle 3 stappen klaar: 2 gecontroleerd met je handleiding, 1 door jou bevestigd.");
    expect(completeText(3, 0, "your")).toBe("All 3 steps checked against your manual.");
  });

  it("Share never says 'all 1 steps'", () => {
    expect(shareText(1)).toBe("Done with Fix-It Lens: the step was checked against the manual.");
    expect(shareText(4)).toBe("Done with Fix-It Lens: all 4 steps checked against the manual.");
    expect(shareText(1, 0, "nl")).toBe("Klaar met Fix-It Lens: de stap is gecontroleerd met de handleiding.");
    expect(shareText(4, 0, "nl")).toBe("Klaar met Fix-It Lens: alle 4 stappen gecontroleerd met de handleiding.");
  });

  it("Share never calls a step the person confirmed checked", () => {
    expect(shareText(4, 1)).toBe("Done with Fix-It Lens: 3 of 4 steps checked against the manual, the rest confirmed by me.");
    expect(shareText(1, 1)).toBe("Done with Fix-It Lens: confirmed by me.");
    expect(shareText(4, 2, "nl")).toBe("Klaar met Fix-It Lens: 2 van 4 stappen gecontroleerd met de handleiding, de rest door mij bevestigd.");
    expect(completeText(3, 1, "your")).toBe("All 3 steps done: 2 checked against your manual, 1 confirmed by you.");
  });

  it("the amber bar, the talk button, the ring warning and the microphone", () => {
    const h = { rule_id: "nl.mains.casing", line: "Achter het paneel zit netspanning." } as Parameters<typeof barText>[0];
    expect(barText(h, "words", 1, "nl")).toBe("Ik heb een zin weggelaten: Achter het paneel zit netspanning.");
    expect(barText(h, "words", 2, "nl")).toMatch(/^Ik heb 2 zinnen weggelaten: /);
    expect(barText(h, "words", 2, "en")).toMatch(/^I left out 2 lines: /);
    expect(talkText(0, 65_000, "nl")).toBe("Luistert 1:05 · laat los om te sturen");
    // The rule id's label is English: the Dutch warning names "the part" instead of mixing languages.
    expect(ringWarning(3, "mains heating element", "nl")).toBe(
      "Let op: hier is de rode zone maar 3 cm rond het onderdeel. Werk ernaast; raak het onderdeel niet aan.",
    );
    expect(micErrorText({ name: "NotAllowedError" }, "nl")).toMatch(/^De microfoon is geblokkeerd/);
  });
});

describe("a session in Dutch", () => {
  const step: S2C = {
    type: "step",
    seq: 1,
    t_ms: 10,
    job_id: "clean_filter",
    step_id: "lift_filter",
    index: 1,
    of: 3,
    text: "Til het filter eruit.",
    page: 43,
    quote: "Filters uitnemen.",
    view: "front",
    power: "any",
  };

  function show(lang: string) {
    document.body.innerHTML = "<main id=app></main>";
    const els = mount(document.getElementById("app")!);
    const s = initial();
    reduce(s, { type: "hello_ok", seq: 0, t_ms: 0, session_id: "s1", resumed: false, mode: "api" });
    reduce(s, step);
    const l = { ...localInitial(), lang, live: true };
    render(els, s, l, "words");
    return { els, s, l };
  }

  it("the card, the controls and the ask are Dutch, and the page says lang=nl", () => {
    const { els } = show("nl");
    expect(els.cardIndex.textContent).toBe("Stap 2 van 3");
    expect(els.talk.textContent).toBe("Houd vast om te praten");
    expect(els.typeModel.textContent).toBe("Typ het model");
    expect(document.querySelector("#gas-tap p")?.textContent).toBe("Zit de rode lijn om de gaskraan?");
    expect(document.querySelector('[data-tap="no"]')?.textContent).toBe("Nee");
    expect(document.querySelector("#install-ios span")?.innerHTML).toBe("Tik in Safari op <b>Deel</b>. Op nieuwere iPhones tik je eerst op <b>⋯</b>.");
    expect(document.getElementById("page-prev")?.getAttribute("aria-label")).toBe("Vorige pagina");
    expect(els.card.lang).toBe("nl");
    expect(els.controls.lang).toBe("nl");
    expect(document.documentElement.lang).toBe("nl");
    expect(document.querySelector("#settings h2")?.textContent).toBe("Over");
  });

  it("an English session is untouched", () => {
    const { els } = show("en");
    expect(els.cardIndex.textContent).toBe("Step 2 of 3");
    expect(els.talk.textContent).toBe("Hold to talk");
    expect(document.querySelector("#install-ios span")?.innerHTML).toBe("Tap <b>Share</b> in Safari. On newer iPhones, tap <b>⋯</b> first.");
    expect(els.card.lang).toBe("en");
  });

  it("a notice and a check's verdict come out in Dutch", () => {
    const { els, s, l } = show("nl");
    reduce(s, { type: "notice", seq: 2, t_ms: 20, kind: "hand_lost" } as S2C);
    reduce(s, { type: "verdict", seq: 3, t_ms: 30, verdict: { step_id: "lift_filter", result: "cant_see", ask_for: "light" } } as never);
    render(els, s, l, "words");
    expect(els.notice.textContent).toBe("Ik zie je hand niet, dus ik heb gepauzeerd.");
    expect(els.verdict.textContent).toBe("Ik zie het niet. Zorg voor meer licht.");
  });
});

describe("the frame around the screens in Dutch", () => {
  function frame(lang: string) {
    document.body.innerHTML = "<main id=app></main>";
    const els = mount(document.getElementById("app")!);
    const ap = {
      id: "a1",
      alias: "vaatwasser",
      kind: "dishwasher",
      brand: "Bosch",
      model: "SMV4HAX40N",
      plate_confirmed: true,
      card_status: "pending",
      ingest: { stage: "ingest", progress: 0.4, eta_s: 150, manual: { pages: 1, lang: "en", user_lang: false }, quotes: { checked: 4, passed: 3 }, jobs_refused: [{ id: "x", reason: "gas" }] },
    };
    const l = { ...localInitial(), lang, add: { photoId: null, label: null, appliance: ap as never, error: null, busy: false, nextPollAt: 0, jobs: null, upload: null, thumb: null } };
    render(els, initial(), l, "words");
    return els;
  }

  it("the add screen, its wait and About read in Dutch, and the page says so", () => {
    const els = frame("nl");
    expect(document.documentElement.lang).toBe("nl");
    expect(els.addHead.textContent).toBe("Voeg dit apparaat toe");
    expect(els.addStages.querySelector(".now")?.textContent).toBe("De handleiding lezen…");
    expect(els.addEta.textContent).toBe("Nog ongeveer 3 min");
    expect(els.addPages.textContent).toBe("Handleiding gevonden: 1 pagina, in het Engels.");
    expect(els.addQuotes.textContent).toBe("Citaten nagekeken in de pagina's: 3 van 4 gevonden.");
    expect(document.querySelector("#add-refused li")?.textContent).toBe("1 klus weggelaten: het gaat om de gastoevoer.");
    expect(document.getElementById("add-name")?.getAttribute("placeholder")).toBe("vaatwasser");
    expect(document.querySelector("#add-name-row span")?.textContent).toBe("Geef het een naam (niet verplicht)");
    expect(document.querySelector("#settings h2")?.textContent).toBe("Over");
    expect(document.querySelector("#another h3")?.textContent).toBe("Open op een andere telefoon");
    // Nothing the phone writes itself is left in English: only the data (the appliance's brand and model) and names.
    const text = [els.add, els.settings, els.home, els.tabs].map((x) => x.textContent ?? "").join(" ").replace(/Bosch|SMV4HAX40N|Fix-It Lens|Fix‑It Lens|Meta|Samsung|English|Nederlands/g, "");
    expect(text.match(/.{0,30}\b(the|your|you|and|with|this|Add|Remove|Close|Save|Cancel|Delete|Done|Edit|Back|About|Make)\b.{0,30}/g)).toBeNull();
  });

  it("an English phone keeps its English, and its page says lang=en", () => {
    const els = frame("en");
    expect(document.documentElement.lang).toBe("en");
    expect(els.addPages.textContent).toBe("Manual found: 1 page, in English.");
    expect(document.querySelector("#settings h2")?.textContent).toBe("About");
    expect(document.getElementById("add-model")?.getAttribute("placeholder")).toBe("e.g. SMV4HAX40N");
  });
});

describe("the model label is named in words people know", () => {
  it("no line says 'plate' alone; the trade term only follows the plain words", () => {
    for (const [en, nl] of Object.entries(ALL_NL)) {
      expect(en.replace(/\(the rating plate[;)]/g, ""), en).not.toMatch(/\bplate\b/i);
      expect(nl.replace(/het typeplaatje \(de sticker met het modelnummer/g, ""), nl).not.toMatch(/plaatje/i);
    }
  });
});

// @vitest-environment jsdom
// The render path: a held sentence is never drawn and never spoken; its place is the amber bar.
import { describe, expect, it } from "vitest";
import type { S2C } from "../../contracts/ts/src/ws.ts";
import { initial, reduce } from "../src/session.ts";
import { Speech, type SynthLike } from "../src/speech.ts";
import { barText, talkText, localInitial, mount, render, type BarLayout } from "../src/ui.ts";
import { cardView } from "../src/devices.ts";

// What the model wrote. The server's G0 passed the first and third sentences and held the second; the held text exists
// only here, in the test, to prove it cannot reach the screen or the speaker.
const HELD_TEXT = "Unscrew the back panel and check the pump wiring";
const PASSED = ["Let's look at the filter first.", "Turn it anticlockwise to lift it out."];

const session: S2C[] = [
  { type: "hello_ok", seq: 0, t_ms: 0, session_id: "s1", resumed: false, mode: "api" },
  {
    type: "step",
    seq: 1,
    t_ms: 10,
    job_id: "clean_filter",
    step_id: "lift_filter",
    index: 0,
    of: 3,
    text: "Lift out the filter.",
    page: 43,
    quote: "Filters uitnemen.",
    view: "front",
    power: "any",
  },
  { type: "sentence", seq: 2, t_ms: 20, id: "u1.s1", text: PASSED[0]! },
  {
    type: "held",
    seq: 3,
    t_ms: 30,
    sentence_id: "u1.s2",
    rule_id: "nl.mains.casing",
    line: "Behind the back panel is mains power. That's a technician.",
    words: 9,
  },
  { type: "sentence", seq: 4, t_ms: 40, id: "u1.s3", text: PASSED[1]! },
  { type: "rail", seq: 5, t_ms: 50, tool: "locate", args_short: "filter system", result_short: "box", ms: 2140 },
  { type: "checking", seq: 6, t_ms: 60, step_id: "lift_filter" },
];

function play(layout: BarLayout) {
  document.body.innerHTML = "<main id=app></main>";
  const els = mount(document.getElementById("app")!);
  const said: string[] = [];
  const synth: SynthLike = { speak: (u) => void said.push(u.text), cancel: () => undefined };
  const speech = new Speech(synth, "en");
  const s = initial();
  for (const m of session) {
    reduce(s, m);
    if (m.type === "sentence") speech.say(m);
    render(els, s, localInitial(), layout);
  }
  return { els, said, speech, s };
}

describe("render path", () => {
  for (const layout of ["rule", "words"] as const) {
    it(`a held sentence is never drawn or spoken; the bar shows instead (${layout} layout)`, () => {
      const { els, said } = play(layout);
      const dom = document.body.textContent ?? "";
      expect(dom).not.toContain(HELD_TEXT);
      expect(dom).not.toContain("pump wiring");
      expect(said.join(" ")).not.toContain("pump");
      expect(said).toEqual(PASSED);
      expect(els.bar.hidden).toBe(false);
      expect(els.bar.textContent).toContain("Behind the back panel is mains power.");
      expect(els.bar.textContent).toBe(barText(session[3] as never, layout));
    });
  }

  it("the bar layout changes the words only, never whether the sentence is held", () => {
    const a = play("rule");
    const b = play("words");
    expect(a.els.bar.hidden).toBe(b.els.bar.hidden);
    expect(a.said).toEqual(b.said);
    expect(a.els.bar.textContent).toContain("nl.mains.casing");
    expect(b.els.bar.textContent).toBe("I left out a line: Behind the back panel is mains power. That's a technician.");
    expect(b.els.bar.textContent).not.toContain("nl.mains");
  });

  it("shows Checking… at once and the step card with its page; the rail only with ?debug=1, in words", () => {
    const { els, s } = play("rule");
    expect(els.checking.hidden).toBe(false);
    expect(els.cardPage.textContent).toBe("p. 43");
    expect(els.rail.hidden).toBe(true);
    const l = localInitial();
    l.debug = true;
    render(els, s, l, "words");
    expect(els.rail.hidden).toBe(false);
    expect(els.rail.textContent).toBe("Found the part (filter system): box · 2.1 s");
    expect(els.bar.textContent).toContain("rule nl.mains.casing");
  });

  it("a stop turns the screen red with the fixed script and silences speech", () => {
    const { els, speech, said } = play("rule");
    const s = initial();
    for (const m of session) reduce(s, m);
    reduce(s, {
      type: "safety",
      seq: 7,
      t_ms: 70,
      state: { state: "STOP", rule_id: "nl.gas.tap", evidence: "hand at tap", script: "Hands back. Close the tap on the water hose." },
    });
    speech.silence();
    speech.say({ type: "sentence", seq: 8, t_ms: 80, id: "u2.s1", text: "More words." });
    render(els, s, localInitial(), "rule");
    expect(els.stop.hidden).toBe(false);
    expect(els.stopText.textContent).toBe("Hands back. Close the tap on the water hose.");
    expect(said).not.toContain("More words.");
  });

  it("server text never becomes markup", () => {
    document.body.innerHTML = "<main id=app></main>";
    const els = mount(document.getElementById("app")!);
    const s = initial();
    reduce(s, { type: "sentence", seq: 0, t_ms: 0, id: "x", text: "<img src=x onerror=alert(1)>" });
    render(els, s, localInitial(), "rule");
    // The only <img>s are the fixed page viewer and the manual's thumbnail, so any other came from server text.
    expect(document.querySelector("img:not(#page-img):not(#add-thumb)")).toBeNull();
    expect(document.querySelector("[onerror]")).toBeNull();
  });
});

describe("about", () => {
  it("says the app is not affiliated with the makers or Meta, in the first-run screen's words", () => {
    document.body.innerHTML = '<main id="app"></main>';
    const e = mount(document.getElementById("app")!);
    expect(e.settings.textContent).toContain("Not affiliated with Bosch, Samsung, the boiler makers or Meta.");
  });
});

describe("the talk button while recording", () => {
  it("counts the seconds held", () => {
    expect(talkText(1000, 1000)).toBe("Listening 0:00 · release to send");
    expect(talkText(1000, 4500)).toBe("Listening 0:03 · release to send");
    expect(talkText(0, 75_000)).toBe("Listening 1:15 · release to send");
  });
});

describe("lookup failed", () => {
  const els = () => {
    const root = document.createElement("div");
    document.body.replaceChildren(root);
    return mount(root);
  };
  const lay: BarLayout = "rule";
  const run = (mode: "api" | "replay"): string => {
    const e = els();
    let s = initial();
    s = reduce(s, { type: "hello_ok", seq: 0, t_ms: 0, session_id: "s1", resumed: false, mode } as S2C);
    s = reduce(s, { type: "notice", seq: 1, t_ms: 5, kind: "lookup_failed" } as S2C);
    render(e, s, localInitial(), lay);
    return e.state.querySelector(".state-line")?.textContent ?? "";
  };
  it("live, it names what to tap; in a replay, where the controls are hidden, it names none", () => {
    expect(run("api")).toContain("Tap Type the model");
    expect(run("replay")).toBe("I couldn't find the manual online.");
  });
  it("sits in the card with a way on, not over the camera", () => {
    const e = els();
    let s = initial();
    s = reduce(s, { type: "hello_ok", seq: 0, t_ms: 0, session_id: "s1", resumed: false, mode: "api" } as S2C);
    s = reduce(s, { type: "notice", seq: 1, t_ms: 5, kind: "lookup_failed" } as S2C);
    render(e, s, localInitial(), lay);
    expect(e.notice.hidden).toBe(true);
    expect(e.state.hidden).toBe(false);
    expect(e.state.querySelector<HTMLElement>("[data-act=type-model]")?.textContent).toBe("Type the model");
  });
});

describe("the daily limit", () => {
  const capped = (live: boolean, mode: "api" | "replay", lang: "en" | "nl" = "en") => {
    const root = document.createElement("div");
    document.body.replaceChildren(root);
    const e = mount(root);
    let s = initial();
    s = reduce(s, { type: "hello_ok", seq: 0, t_ms: 0, session_id: "s1", resumed: false, mode } as S2C);
    s = reduce(s, { type: "notice", seq: 1, t_ms: 5, kind: "cap_reached" } as S2C);
    const l = localInitial();
    l.live = live;
    l.lang = lang;
    render(e, s, l, "rule");
    return e;
  };
  it("in a live session offers End, which closes the session, beside the line", () => {
    const e = capped(true, "api");
    const box = e.card.hidden ? e.state : e.cardState;
    expect(box.querySelector(".state-line")?.textContent).toBe("Today's limit is reached.");
    expect(box.querySelector<HTMLElement>("[data-act=finish]")?.textContent).toBe("End");
    expect(e.camBack.hidden).toBe(false);
    expect(e.camBack.textContent).toBe("End");
    expect(capped(true, "api", "nl").camBack.textContent).toBe("Afsluiten");
  });
  it("in a replay offers no End in the line (there is no session to close)", () => {
    const e = capped(false, "replay");
    expect(e.state.querySelector("[data-act=finish]")).toBeNull();
  });
});

describe("a step of our own (1.26 origin fixit)", () => {
  // Putting back what a power-off step took away: the manual has no page for it, so the card says it is ours.
  const own = { type: "step", seq: 9, t_ms: 90, job_id: "clean_filter", step_id: "power_on", index: 2, of: 3, text: "Plug the machine back in.", origin: "fixit", view: "front", power: "any" } as unknown as S2C;

  it("says 'Our step, not from the manual' where the page goes, with no quote and no page to open", () => {
    const { els, s } = play("rule");
    reduce(s, own);
    const l = localInitial();
    l.live = true;
    render(els, s, l, "rule");
    expect(els.cardPage.textContent).toBe("Our step, not from the manual");
    expect(els.cardPage.textContent).not.toMatch(/undefined|null|p\./);
    expect(els.cardText.textContent).toBe("Plug the machine back in.");
    expect(els.cardQuote.hidden).toBe(true);
    expect(els.showPage.hidden).toBe(true);
    expect(els.cardMaker.hidden).toBe(true);
    expect(els.card.classList.contains("our-step")).toBe(true);
    l.lang = "nl";
    render(els, s, l, "rule");
    expect(els.cardPage.textContent).toBe("Onze stap, niet uit de handleiding");
  });

  it("keeps the step on the card's job list, with no page", () => {
    const v = cardView(
      { manual: { source_url: "https://acme.example/m.pdf", lang: "en", page_count: 8 }, jobs: [{ id: "j", title: { en: "J" }, kind: "fix", steps: [{ id: "off", text: { en: "Unplug it." }, page: 3 }, { id: "on", text: { en: "Plug it in." }, page: null, origin: "fixit" }] }] },
      "en",
    )!;
    expect(v.jobs[0]!.steps).toEqual([{ id: "off", text: "Unplug it.", page: 3 }, { id: "on", text: "Plug it in.", page: null }]);
  });
});

describe("the phone draws what the server has", () => {
  it("a box from the locate role (no SAM mask, no confidence) is drawn like a mask", () => {
    const s = initial();
    reduce(s, { type: "outline", seq: 1, t_ms: 1, part_id: "filter", kind: "target", mask_svg: "M80 80L320 80L320 320L80 320Z", frame_id: "f1", conf: null } as S2C);
    const o = s.outlines.get("filter");
    expect(o?.polys).toHaveLength(1);
    expect(o?.polys[0]).toHaveLength(4);
  });
});

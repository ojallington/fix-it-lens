// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";
import example from "../../contracts/examples/g1/example.json";
import { App, type Deps } from "../src/app.ts";
import { PDF_TEXT } from "../src/appliance.ts";
import { APP_CONSENT_PLACEHOLDER, JOIN_TEXT } from "../src/household.ts";
import type { TokenStore } from "../src/household.ts";
import type { InstallHost, InstallKind } from "../src/install.ts";
import { friendlyDate, fromNow, shortDate, today } from "../src/schedule.ts";
import { chooseLang, pickLang, type LangSlot, type LangStore } from "../src/lang.ts";
import { asked } from "../src/session.ts";
import { browserSynth } from "../src/speech.ts";
import { completeText, replayTitle } from "../src/ui.ts";
import { JOIN_TEXT as PEOPLE_JOIN_TEXT, PEOPLE_TEXT } from "../src/people.ts";
import type { S2C } from "../../contracts/ts/src/ws.ts";
import preview from "../../contracts/examples/ws/JobPreview--cleaner.json";

const C = example.constants;

const REC = {
  meta: { session_id: "m1" },
  events: [
    { t_ms: 0, dir: "s2c", kind: "hello_ok", msg: { type: "hello_ok", seq: 0, t_ms: 0, session_id: "m1", resumed: false, mode: "api" } },
    { t_ms: 10, dir: "c2s", kind: "frame", msg: { type: "frame", frame_id: "f1", jpeg_1024: null, phash: "0000000000000000" } },
    {
      t_ms: 20,
      dir: "s2c",
      kind: "step",
      msg: { type: "step", seq: 1, t_ms: 20, job_id: "j", step_id: "lift", index: 0, of: 2, text: "Lift out the filter.", page: 43, view: "front", power: "any" },
    },
    { t_ms: 30, dir: "s2c", kind: "checking", msg: { type: "checking", seq: 2, t_ms: 30, step_id: "lift" } },
    { t_ms: 40, dir: "c2s", kind: "g1", msg: { type: "g1", seq: 5, t_ms: 40, zone_id: "z", kind: "stop", ttc_ms: 300, dist_cm: 18, frame_id: "f1", reason: "ttc" } },
  ],
};

function tokens(t: string | null): TokenStore & { now: () => string | null } {
  let v = t;
  return { get: () => v, set: (x) => void (v = x), now: () => v };
}

function deps(over: Partial<Deps> = {}) {
  const rafs: ((t: number) => void)[] = [];
  const calls = { camera: 0, socket: 0, fetched: [] as string[], alarm: 0 };
  const d: Deps = {
    doc: document,
    search: "?replay=m1",
    wsUrl: "wss://x/ws",
    fetch: (async (u: string) => {
      calls.fetched.push(String(u));
      return new Response(JSON.stringify(REC), { status: 200 });
    }) as typeof fetch,
    synth: null,
    alarm: { fire: () => void calls.alarm++, canVibrate: true },
    openSocket: () => {
      calls.socket++;
      throw new Error("no socket in replay");
    },
    openCamera: async () => {
      calls.camera++;
    },
    eachFrame: () => () => undefined,
    loadHands: async () => ({ detect: () => ({ points: [], hands: 0, ms: 0 }) }),
    flowWorker: () => ({ postMessage: () => undefined, onmessage: null }),
    jpeg: async () => "",
    bitmap: async () => ({ close: () => undefined }) as unknown as ImageBitmap,
    heading: () => null,
    now: () => 0,
    raf: (fn) => void rafs.push(fn),
    g1: C,
    disclaimers: null,
    household: tokens(null),
    origin: "https://fil.example:8443",
    copy: async () => undefined,
    navigate: () => undefined,
    recorder: () => ({ start: async () => undefined, stop: async () => null }),
    firstRunSeen: () => true,
    markFirstRun: () => undefined,
    lang: "en",
    barLayout: "rule",
    dev: false,
    ...over,
  };
  const run = (t: number): void => {
    for (const fn of rafs.splice(0)) fn(t);
  };
  return { d, calls, run };
}

beforeEach(() => {
  document.body.innerHTML = '<video id="cam"></video><canvas id="overlay"></canvas><main id="app"></main>';
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
});

describe("?replay=<id>", () => {
  it("plays the recorded session with the REPLAY badge, opening no camera and no socket", async () => {
    const { d, calls, run } = deps();
    const app = new App(d);
    await app.boot();
    for (let t = 1000; t <= 1100; t += 16) run(t);
    expect(calls.camera).toBe(0);
    expect(calls.socket).toBe(0);
    expect(calls.fetched).toEqual(["/api/v1/replay/m1"]);
    expect(app.els.replay.hidden).toBe(false);
    expect(app.els.cardText.textContent).toBe("Lift out the filter.");
    expect(app.els.checking.hidden).toBe(false);
    expect((document.querySelector(".replay-frame") as HTMLImageElement).src).toContain("/api/v1/replay/m1/frames/f1.jpg");
    // The recorded phone stop shows again, with its alarm.
    expect(app.els.stop.hidden).toBe(false);
    expect(calls.alarm).toBe(1);
  });
});

describe("?replay=<id> of this home's own session", () => {
  it("fetches each frame with the home's token and shows it from a blob", async () => {
    const asked: { url: string; auth: string | null }[] = [];
    const base = deps().d.fetch;
    const f = (async (u: string, init?: RequestInit) => {
      asked.push({ url: String(u), auth: new Headers(init?.headers).get("authorization") });
      return String(u).includes("/frames/") ? new Response(new Blob(["jpeg"]), { status: 200 }) : base(u, init);
    }) as typeof fetch;
    const made: Blob[] = [];
    const { d, run } = deps({ fetch: f, household: tokens("tok"), objectUrl: { make: (b) => (made.push(b), `blob:f${made.length}`), drop: () => undefined } });
    const app = new App(d);
    await app.boot();
    for (let t = 1000; t <= 1100; t += 16) run(t);
    for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
    expect(asked.find((x) => x.url.endsWith("/api/v1/replay/m1/frames/f1.jpg"))?.auth).toBe("Bearer tok");
    expect((document.querySelector(".replay-frame") as HTMLImageElement).src).toBe("blob:f1");
  });
});

describe("?replay=<id> polish", () => {
  it("maps outlines with the recorded frame's own size (a 1600x1200 frame, not the default 16:9)", async () => {
    const { d, run } = deps();
    const app = new App(d);
    await app.boot();
    for (let t = 1000; t <= 1100; t += 16) run(t);
    const img = document.querySelector(".replay-frame") as HTMLImageElement;
    Object.defineProperty(img, "naturalWidth", { value: 1600 });
    Object.defineProperty(img, "naturalHeight", { value: 1200 });
    img.dispatchEvent(new Event("load"));
    expect(app.img).toEqual({ w: 1600, h: 1200 });
  });

  it("an unknown replay id says so, with a way back, instead of a blank screen", async () => {
    const { d, run } = deps({ fetch: (async () => new Response("{}", { status: 404 })) as typeof fetch });
    const app = new App(d);
    await app.boot();
    run(0);
    expect(app.els.missing.hidden).toBe(false);
    expect(app.els.missing.textContent).toContain("This replay isn't available.");
    expect(app.els.replay.hidden).toBe(true);
    expect(app.els.home.hidden).toBe(true);
  });

  it("the REPLAY chip sits in the header, never over the status text", async () => {
    const { d, run } = deps();
    const app = new App(d);
    await app.boot();
    run(1000);
    expect(app.els.replay.closest("header")).toBe(app.els.top);
  });

  it("the header names the appliance and the job from the recording's card", async () => {
    const rec = { ...REC, replay_id: "abc", summary: { appliance_alias: "dishwasher", job_title: "Clean the filters", steps_total: 8 } };
    const { d, run } = deps({ fetch: (async () => new Response(JSON.stringify(rec), { status: 200 })) as typeof fetch });
    const app = new App(d);
    await app.boot();
    run(1000);
    expect(app.els.replayTitle.textContent).toBe("Dishwasher · Clean the filters");
    expect(app.els.replayTitle.closest("header")).toBe(app.els.top);
  });

  it("in Dutch the header names a bare kind in Dutch, and keeps a name the home gave", () => {
    expect(replayTitle({ appliance_alias: "dishwasher", job_title: "Zeven reinigen" }, "nl")).toBe("Vaatwasser · Zeven reinigen");
    expect(replayTitle({ appliance_alias: "Washing machine", job_title: null }, "nl-NL")).toBe("Wasmachine");
    expect(replayTitle({ appliance_alias: "upstairs dishwasher", job_title: null }, "nl")).toBe("Upstairs dishwasher");
    expect(replayTitle({ appliance_alias: "dishwasher", job_title: null }, "en")).toBe("Dishwasher");
  });

  it("a new phone's first screen is in Dutch with ?lang=nl", async () => {
    const { d, run } = deps({ search: "", path: "/start", lang: "nl", household: tokens(null) });
    const app = new App(d);
    await app.boot();
    run(0);
    expect(app.els.firstRun.hidden).toBe(false);
    expect(app.els.firstRunTitle.textContent).toBe("Voordat je begint");
    expect(app.els.firstRunGo.textContent).toBe("Vink het vakje aan om verder te gaan");
    expect(app.els.firstRun.lang).toBe("nl");
  });

  it("a recording without a card shows no header line", async () => {
    const { d, run } = deps();
    const app = new App(d);
    await app.boot();
    run(1000);
    expect(app.els.replayTitle.hidden).toBe(true);
  });

  it("the tool rail stays hidden without ?debug=1", async () => {
    const { d, run } = deps();
    const app = new App(d);
    await app.boot();
    app.onMessage({ type: "rail", seq: 9, t_ms: 9, tool: "check_step", args_short: "", result_short: "cant see it yet", ms: 2900 });
    run(1000);
    expect(app.els.rail.hidden).toBe(true);
  });
});

describe("camera and hand tracking failures", () => {
  it("no camera: says why and keeps Type the model", async () => {
    const { d, run } = deps({ search: "", openSocket: () => new Sock(), openCamera: async () => { throw new DOMException("no device", "NotFoundError"); } });
    const app = new App(d);
    await app.boot();
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    await app.startLive();
    run(0);
    expect(app.els.notice.textContent).toContain("Can't open the camera");
    expect(app.els.controls.hidden).toBe(false);
    expect(app.els.typeModel.hidden).toBe(false);
  });

  it("hand tracking that throws (no WebGL) is caught: the phone carries on and says it can't watch hands", async () => {
    const { d, run } = deps({ search: "", openSocket: () => new Sock(), loadHands: async () => { throw new Error("no webgl"); } });
    const app = new App(d);
    await app.boot();
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    await app.startLive();
    run(0);
    expect(app.els.notice.textContent).toContain("Hand tracking didn't start");
  });

  it("the hand-tracking banner shows for 6 s, then folds into a header chip that brings it back", async () => {
    let clock = 0;
    const { d, run } = deps({ search: "", now: () => clock, openSocket: () => new Sock(), loadHands: async () => { throw new Error("no webgl"); } });
    const app = new App(d);
    await app.boot();
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    await app.startLive();
    run(0);
    expect(app.els.notice.textContent).toContain("Hand tracking didn't start");
    expect(app.els.handsChip.hidden).toBe(true);
    clock = 6100;
    run(16);
    expect(app.els.notice.hidden).toBe(true);
    expect(app.els.handsChip.hidden).toBe(false);
    expect(app.els.handsChip.closest("header")).toBe(app.els.top);
    app.els.handsChip.click();
    run(32);
    expect(app.els.notice.textContent).toContain("Hand tracking didn't start");
  });
});

describe("replay leaves out the recording's connection trouble", () => {
  it("a recorded slow connection or missing recording is not shown to the viewer", async () => {
    const quiet = (kind: string, t: number) => ({ t_ms: t, dir: "s2c", kind: "notice", msg: { type: "notice", seq: 90 + t, t_ms: t, kind } });
    const rec = { ...REC, events: [...REC.events.slice(0, 3), quiet("slow_connection", 25), quiet("no_recording", 26)] };
    const { d, run } = deps({ fetch: (async () => new Response(JSON.stringify(rec), { status: 200 })) as typeof fetch });
    const app = new App(d);
    await app.boot();
    for (let t = 1000; t <= 1100; t += 16) run(t);
    expect(app.els.notice.hidden).toBe(true);
    expect(app.els.cardText.textContent).toBe("Lift out the filter.");
  });
});

describe("the recorded run on home", () => {
  for (const there of [true, false])
    it(`offers ?replay=golden only when the server has one (${there ? "it does" : "not yet"})`, async () => {
      const { d, run } = deps({ search: "", golden: async () => there });
      const app = new App(d);
      await app.boot();
      await new Promise((r) => setTimeout(r, 0));
      run(0);
      expect(app.els.home.hidden).toBe(false);
      expect(app.els.watch.hidden).toBe(!there);
      expect(app.els.watch.getAttribute("href")).toBe("?replay=golden");
    });
});

describe("notification landing", () => {
  it("records the open once, with its channel, and asks Start?", async () => {
    const { d, calls, run } = deps({ search: "?appliance=a1&job=clean_filter&reminder=a1:clean_filter:2026-10-28&ch=ics&title=Clean%20the%20filter" });
    const app = new App(d);
    await app.boot();
    run(0);
    expect(calls.fetched).toEqual(["/api/v1/reminders/a1%3Aclean_filter%3A2026-10-28/opened"]);
    expect(app.els.landing.hidden).toBe(false);
    expect(app.els.landingTitle.textContent).toBe("Clean the filter");
    expect(calls.camera).toBe(0);
    // Opened from the calendar: only push sends again, so there is nothing to snooze.
    expect(app.els.landingSnooze.hidden).toBe(true);
  });

  it("shows the appliance, why it is due and how long it takes; Not now goes home", async () => {
    const why = "Last done: 3 Sep. Your manual says every month (p. 43).";
    const { d, run } = deps({ search: `?appliance=a1&job=clean_filters&reminder=k&alias=dishwasher&why=${encodeURIComponent(why)}&min=10&title=Clean%20the%20filters` });
    const app = new App(d);
    await app.boot();
    run(0);
    expect(app.els.landingAlias.textContent).toBe("Dishwasher");
    expect(app.els.landingWhy.textContent).toBe(why);
    expect(app.els.landingMin.textContent).toBe("About 10 min");
    (app.els.landing.querySelector('[data-act="landing-dismiss"]') as HTMLButtonElement).click();
    run(16);
    expect(app.els.landing.hidden).toBe(true);
    expect(app.els.home.hidden).toBe(false);
  });

  it("Remind me tomorrow snoozes the reminder for 24 h and says so; a refused snooze says it stays as planned", async () => {
    for (const [status, text] of [[204, "I'll remind you again tomorrow."], [404, "Couldn't move the reminder. It will come again as planned."]] as const) {
      const sent: { url: string; body: unknown; auth: string | null }[] = [];
      const f = (async (u: string, init?: RequestInit) => {
        if (String(u).endsWith("/snooze"))
          sent.push({ url: String(u), body: JSON.parse(String(init?.body)), auth: new Headers(init?.headers).get("authorization") });
        return new Response(String(u).endsWith("/snooze") ? null : "[]", { status: String(u).endsWith("/snooze") ? status : 200 });
      }) as typeof fetch;
      const { d, run } = deps({ search: "?appliance=a1&job=j&reminder=a1:j:2026-10-28", fetch: f, household: tokens("tok") });
      const app = new App(d);
      await app.boot();
      run(0);
      app.els.landingSnooze.click();
      await new Promise((r) => setTimeout(r, 0));
      run(16);
      expect(sent).toEqual([{ url: "/api/v1/reminders/a1%3Aj%3A2026-10-28/snooze", body: { hours: 24 }, auth: "Bearer tok" }]);
      expect(app.els.landingSnoozed.textContent).toBe(text);
      expect(app.els.landingSnooze.hidden).toBe(status === 204);
    }
  });
});

class Sock {
  readyState = 1;
  bufferedAmount = 0;
  sent: Record<string, unknown>[] = [];
  onopen: ((e: unknown) => void) | null = null;
  onmessage: ((e: { data: unknown }) => void) | null = null;
  onclose: ((e: unknown) => void) | null = null;
  onerror: ((e: unknown) => void) | null = null;
  send(d: string): void {
    this.sent.push(JSON.parse(d));
  }
  close(): void {}
}

async function live(points: () => { x: number; y: number }[] = () => [], over: Partial<Deps> = {}) {
  const sock = new Sock();
  let frameFn: ((t: { captureMs: number; ageMs: number; frameId: string }) => void) | null = null;
  let now = 0;
  const { d, calls, run } = deps({
    search: "",
    openSocket: () => sock,
    eachFrame: (_v, fn) => {
      frameFn = fn;
      return () => undefined;
    },
    loadHands: async () => ({ detect: () => ({ points: points(), hands: 1, ms: 1 }) }),
    now: () => now,
    ...over,
  });
  const app = new App(d);
  await app.boot();
  await app.startLive();
  sock.onopen?.({});
  sock.onmessage?.({ data: JSON.stringify({ type: "hello_ok", seq: 0, t_ms: 0, session_id: "s1", resumed: false, mode: "api" }) });
  const frame = async (id: string, t: number): Promise<void> => {
    now = t + 10;
    frameFn!({ captureMs: t, ageMs: 10, frameId: id });
    await new Promise((r) => setTimeout(r, 0));
  };
  return { app, sock, calls, run, frame };
}

describe("live", () => {
  it("a passed step shows a tick and buzzes; the last one opens the job-done screen with the next due date", async () => {
    let buzz = 0;
    const store = tokens("tok");
    const f = (async (u: string) =>
      new Response(String(u).includes("/due") ? JSON.stringify([{ appliance_id: "a1", job_id: "j", title: "Clean", due_on: "2026-11-27" }]) : "[]", { status: 200 })) as typeof fetch;
    const { app, run } = await live(() => [], { vibrate: () => void buzz++, household: store, fetch: f, search: "?appliance=a1&job=j" });
    const v = { check_id: "c", attempt: 1, judge: "done", clauses: [], result: "done" } as const;
    // The job's card gives the camera its header, where the steps show as segments.
    (app as unknown as { l: { cards: Record<string, unknown> } }).l.cards.a1 = { jobs: [{ id: "j", title: "Clean" }] };
    app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "a", index: 0, of: 2, text: "One.", page: 43, view: "front", power: "any" });
    run(0);
    expect([...app.els.camSegs.children].map((x) => x.className)).toEqual(["now", ""]);
    app.onMessage({ type: "verdict", seq: 2, t_ms: 2, verdict: { ...v, step_id: "a" } } as never);
    run(0);
    expect(app.els.passed.textContent).toBe("✓ Step 1 done");
    expect(buzz).toBe(1);
    // The passed step's segment fills while the next step is on its way, and the next one is current once it comes.
    const segs = () => [...app.els.camSegs.children].map((x) => x.className);
    expect(segs()).toEqual(["done", ""]);
    app.onMessage({ type: "step", seq: 3, t_ms: 3, job_id: "j", step_id: "b", index: 1, of: 2, text: "Two.", page: 44, view: "front", power: "any" });
    run(8);
    expect(segs()).toEqual(["done", "now"]);
    app.onMessage({ type: "verdict", seq: 4, t_ms: 4, verdict: { ...v, step_id: "b" } } as never);
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    run(16);
    expect(app.els.complete.hidden).toBe(false);
    expect(app.els.card.hidden).toBe(true);
    expect(app.els.completeText.textContent).toBe("All 2 steps checked against your manual.");
    expect(app.els.completeDue.textContent).toBe("27 November");
    expect(app.els.completeNext.hidden).toBe(false);
    // The proof: each step done, with its page.
    expect([...app.els.completeSteps.querySelectorAll("li")].map((x) => x.textContent)).toEqual(["1✓One.p. 43", "2✓Two.p. 44"]);
    expect(app.els.top.classList.contains("is-complete")).toBe(true);
  });

  it("Job done says how long the job took, from its first step, only once it reaches a minute", async () => {
    let clock = 0;
    const { app, run } = await live(() => [], { now: () => clock });
    const v = { check_id: "c", attempt: 1, judge: "done", clauses: [], result: "done" } as const;
    app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "a", index: 0, of: 2, text: "One.", page: 43, view: "front", power: "any" });
    app.onMessage({ type: "verdict", seq: 2, t_ms: 2, verdict: { ...v, step_id: "a" } } as never);
    clock = 6 * 60_000 + 10_000;
    app.onMessage({ type: "step", seq: 3, t_ms: 3, job_id: "j", step_id: "b", index: 1, of: 2, text: "Two.", page: 44, view: "front", power: "any" });
    app.onMessage({ type: "verdict", seq: 4, t_ms: 4, verdict: { ...v, step_id: "b" } } as never);
    run(clock);
    expect(app.els.completeText.textContent).toBe("All 2 steps checked against your manual · 6 min");
  });

  it("our own step ended by the person says confirmed by you, and Job done claims no camera check for it (contracts 1.26)", async () => {
    const { app, run } = await live();
    const cam = { check_id: "c", attempt: 1, judge: "done", clauses: [], result: "done", witnesses: { judge: "done", sam: "video" } } as const;
    const you = { check_id: "d", attempt: 1, clauses: [], result: "done", witnesses: { sam: "none", by_person: true } } as const;
    app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "a", index: 0, of: 2, text: "One.", page: 43, view: "front", power: "any" });
    app.onMessage({ type: "verdict", seq: 2, t_ms: 2, verdict: { ...cam, step_id: "a" } } as never);
    run(0);
    expect(app.els.passed.textContent).toBe("✓ Step 1 done");
    app.onMessage({ type: "step", seq: 3, t_ms: 3, job_id: "j", step_id: "b", index: 1, of: 2, text: "Ours.", origin: "fixit", view: "front", power: "any" });
    app.onMessage({ type: "verdict", seq: 4, t_ms: 4, verdict: { ...you, step_id: "b" } } as never);
    run(16);
    expect(app.els.passed.textContent).toBe("✓ Step 2 confirmed by you");
    expect(app.els.completeText.textContent).toBe("All 2 steps done: 1 checked against your manual, 1 confirmed by you.");
    // In the list, the step the person ended has no tick and no page: nothing checked it against the manual.
    expect([...app.els.completeSteps.querySelectorAll("li")].map((x) => x.textContent)).toEqual(["1✓One.p. 43", "2youOurs."]);
    expect(completeText(1, 1, "your")).toBe("Done. You confirmed the step yourself.");
    expect(completeText(3, 0, "the")).toBe("All 3 steps checked against the manual.");
  });

  it("I did it — continue is on every step, sends did_it, and is hidden while a safety hold stands", async () => {
    const { app, sock, run } = await live();
    app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "a", index: 0, of: 2, text: "One.", page: 43, view: "front", power: "any" });
    run(0);
    expect(app.els.didIt.hidden).toBe(false);
    expect(app.els.didIt.textContent).toBe("I did it — continue");
    app.onMessage({ type: "safety", seq: 2, t_ms: 2, state: { state: "HOLD", precondition: "fresh_frame" } } as never);
    run(16);
    expect(app.els.didIt.hidden).toBe(true);
    app.onMessage({ type: "safety", seq: 3, t_ms: 3, state: { state: "OK" } } as never);
    run(32);
    expect(app.els.didIt.hidden).toBe(false);
    app.els.didIt.click();
    expect(sock.sent.at(-1)).toMatchObject({ type: "tap", action: "did_it" });
    app.onMessage({ type: "checking", seq: 4, t_ms: 4, step_id: "a" } as never);
    run(48);
    expect(app.els.didIt.hidden).toBe(true); // not while a check runs: the server refuses it then
  });

  it("after I did it the phone waits for the next step: no second tap lands on a step not yet shown", async () => {
    const { app, sock, run, frame } = await live();
    const step = (seq: number, id: string, index: number) =>
      app.onMessage({ type: "step", seq, t_ms: seq, job_id: "j", step_id: id, index, of: 4, text: `Step ${id}.`, page: 42, view: "front", power: "any" });
    step(1, "c", 2);
    run(0);
    app.els.didIt.click();
    expect(sock.sent.filter((m) => m.action === "did_it")).toEqual([expect.objectContaining({ type: "tap", action: "did_it", step_id: "c" })]);
    // Its verdict comes at once, the next step only after the server has looked at earlier steps again.
    app.onMessage({ type: "verdict", seq: 2, t_ms: 2, verdict: { step_id: "c", check_id: "k", attempt: 1, result: "done", clause_ids: [], witnesses: { sam: "none", by_person: true }, latency_ms: 0 } } as never);
    app.onMessage({ type: "checking", seq: 3, t_ms: 3, step_id: "b" } as never);
    run(16);
    expect(app.els.didIt.hidden).toBe(true);
    expect(app.els.checking.hidden).toBe(false);
    step(4, "d", 3);
    run(32);
    expect(app.els.checking.hidden).toBe(true);
    expect(app.els.didIt.hidden).toBe(false);
    // With no step at all the button comes back after a while, so the person is never left without a way on.
    app.els.didIt.click();
    run(48);
    expect(app.els.didIt.hidden).toBe(true);
    await frame("f1", 31_000);
    run(64);
    expect(app.els.didIt.hidden).toBe(false);
  });

  it("a tap the server refuses as for the step before says so, in the phone's language", async () => {
    for (const [lang, line] of [["en", "That tap was for the step before. You're on this step now."], ["nl", "Die tik was voor de vorige stap. Je bent nu bij deze stap."]] as const) {
      const { app, run } = await live(undefined, { lang });
      app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "d", index: 3, of: 4, text: "Step d.", page: 42, view: "front", power: "any" });
      app.onMessage({ type: "notice", seq: 2, t_ms: 2, kind: "tap_not_current", text: null } as never);
      run(0);
      expect(app.els.root.textContent).toContain(line);
    }
  });

  it("the same step sent again ends a check; a step after Job done opens the job again on it", async () => {
    const { app, run } = await live();
    const step = (seq: number, id: string, index: number) =>
      app.onMessage({ type: "step", seq, t_ms: seq, job_id: "j", step_id: id, index, of: 2, text: `Step ${id}.`, page: 42, view: "front", power: "any" });
    step(1, "a", 0);
    app.onMessage({ type: "checking", seq: 2, t_ms: 2, step_id: "a" } as never);
    step(3, "a", 0);
    run(0);
    expect(app.els.checking.hidden).toBe(true);
    step(4, "b", 1);
    app.onMessage({ type: "verdict", seq: 5, t_ms: 5, verdict: { step_id: "b", check_id: "k", attempt: 1, result: "done", clause_ids: [], witnesses: { sam: "none", by_person: true }, latency_ms: 0 } } as never);
    run(16);
    expect(app.els.complete.hidden).toBe(false);
    app.onMessage({ type: "checking", seq: 6, t_ms: 6, step_id: "a" } as never);
    run(32);
    expect(app.els.checking.hidden).toBe(true); // nothing to check on a finished job
    step(7, "a", 0);
    run(48);
    expect(app.els.complete.hidden).toBe(true);
    expect(app.els.cardText.textContent).toBe("Step a.");
  });

  it("a hold after Job done opens the job again: the hold and its ask show, not Job done and not the last step", async () => {
    const { app, run } = await live();
    app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "b", index: 1, of: 2, text: "Step b.", page: 42, view: "front", power: "any" });
    app.onMessage({ type: "verdict", seq: 2, t_ms: 2, verdict: { step_id: "b", check_id: "k", attempt: 1, result: "done", clause_ids: [], witnesses: { sam: "none", by_person: true }, latency_ms: 0 } } as never);
    run(0);
    expect(app.els.complete.hidden).toBe(false);
    app.onMessage({ type: "sentence", seq: 3, t_ms: 3, id: "s1", text: "Step 1 is not done after all." });
    app.onMessage({ type: "safety", seq: 4, t_ms: 4, state: { state: "HOLD", precondition: "power_off_seen" } } as never);
    app.onMessage({ type: "sentence", seq: 5, t_ms: 5, id: "s2", text: "Pull the plug out, then tap yes." });
    run(16);
    expect(app.els.complete.hidden).toBe(true);
    expect(app.els.card.hidden).toBe(true);
    expect(app.els.pause.hidden).toBe(false);
    expect(app.els.pause.textContent).toBe("Show me the plug out of the socket.");
    expect(app.els.confirm.hidden).toBe(false);
    expect(app.els.sentences.textContent).toBe("Pull the plug out, then tap yes.");
    app.onMessage({ type: "safety", seq: 6, t_ms: 6, state: { state: "OK" } } as never);
    app.onMessage({ type: "step", seq: 7, t_ms: 7, job_id: "j", step_id: "a", index: 0, of: 2, text: "Step a.", page: 42, view: "front", power: "off" });
    run(32);
    expect(app.els.cardText.textContent).toBe("Step a.");
  });

  it("camera only: a tap on the picture tucks the text away, a badge says what came in, a hold brings it all back", async () => {
    const { app, run } = await live();
    const body = app.els.root.ownerDocument.body;
    app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "a", index: 0, of: 2, text: "One.", page: 43, view: "front", power: "any" });
    run(0);
    expect(app.els.clearHint.hidden).toBe(false);
    expect(app.els.tuck.hidden).toBe(false);
    app.els.root.ownerDocument.getElementById("cam")!.click();
    run(16);
    expect(body.classList.contains("clear")).toBe(true);
    expect(app.els.unclear.hidden).toBe(false);
    expect(app.els.unclearNews.hidden).toBe(true);
    expect(app.els.clearHint.hidden).toBe(true);
    app.onMessage({ type: "sentence", seq: 2, t_ms: 2, id: "s1", text: "Pull it towards you." });
    run(32);
    expect(app.els.unclearNews.textContent).toBe("New from the guide");
    app.onMessage({ type: "step", seq: 3, t_ms: 3, job_id: "j", step_id: "b", index: 1, of: 2, text: "Two.", page: 44, view: "front", power: "any" });
    run(48);
    expect(app.els.unclearNews.textContent).toBe("Next step");
    // A safety hold is never tucked away: everything shows while it stands, and camera only comes back after.
    app.onMessage({ type: "safety", seq: 4, t_ms: 4, state: { state: "HOLD", precondition: "power_off_seen" } } as never);
    run(64);
    expect(body.classList.contains("clear")).toBe(false);
    expect(app.els.unclear.hidden).toBe(true);
    app.onMessage({ type: "safety", seq: 5, t_ms: 5, state: { state: "OK" } } as never);
    run(80);
    expect(body.classList.contains("clear")).toBe(true);
    app.els.unclear.click();
    run(96);
    expect(body.classList.contains("clear")).toBe(false);
    expect(app.els.cardText.textContent).toBe("Two.");
    expect(app.els.clearHint.hidden).toBe(true); // used once: the hint stays away
    app.els.tuck.click();
    run(112);
    expect(body.classList.contains("clear")).toBe(true);
  });

  it("the manual's words in one language: an English phone reads the English, the manual's own a tap away; a Dutch phone reads them once", async () => {
    const step = { type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "a", index: 0, of: 1, text: "Close the door.", page: 39, view: "front", power: "any", quote: "Controleer of de deur goed gesloten is.", quote_en: "Make sure that the door is properly closed." } as const;
    const en = await live();
    en.app.onMessage(step);
    en.run(0);
    expect(en.app.els.cardQuote.textContent).toBe("“Make sure that the door is properly closed.”");
    expect(en.app.els.cardOrig.hidden).toBe(false);
    expect(en.app.els.cardQuoteOrig.textContent).toBe("“Controleer of de deur goed gesloten is.”");
    expect(en.app.els.card.textContent).not.toContain("Make sure that the door is properly closed.”“");
    const nl = await live(undefined, { lang: "nl" });
    nl.app.onMessage(step);
    nl.run(0);
    expect(nl.app.els.cardQuote.textContent).toBe("“Controleer of de deur goed gesloten is.”");
    expect(nl.app.els.cardOrig.hidden).toBe(true);
    expect(nl.app.els.card.textContent).not.toContain("Make sure");
    // The preview's rows the same way.
    const pv = { ...preview, seq: 2, steps: [{ ...preview.steps[0]!, origin: undefined, quote: step.quote, quote_en: step.quote_en }] };
    for (const [p, shown, gone] of [[en, step.quote_en, step.quote], [nl, step.quote, step.quote_en]] as const) {
      p.app.onMessage(pv as never);
      p.run(16);
      const q = p.app.els.preview.querySelector(".preview-steps blockquote")!;
      expect(q.firstElementChild!.textContent).toBe(`“${shown}”`);
      const fold = q.querySelector("details");
      if (p === en) expect(fold!.textContent).toContain(gone);
      else expect(q.textContent).not.toContain(gone);
    }
  });

  it("under a hold the arrow still clears the view: the step's words go, the hold's line stays; the hold's end lets go", async () => {
    const { app, run } = await live();
    const body = app.els.root.ownerDocument.body;
    app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "a", index: 0, of: 2, text: "One.", page: 43, view: "front", power: "any" });
    app.onMessage({ type: "safety", seq: 2, t_ms: 2, state: { state: "HOLD", precondition: "power_off_seen" } } as never);
    run(0);
    expect(app.els.tuck.hidden).toBe(false);
    app.els.tuck.click();
    run(16);
    expect(body.classList.contains("clear-hold")).toBe(true);
    expect(body.classList.contains("clear")).toBe(false);
    expect(app.els.tuck.getAttribute("aria-pressed")).toBe("true");
    expect(app.els.cardPause.hidden).toBe(false); // what the hold asks stays in view
    app.els.tuck.click();
    run(32);
    expect(body.classList.contains("clear-hold")).toBe(false);
    app.els.tuck.click();
    run(48);
    app.onMessage({ type: "safety", seq: 3, t_ms: 3, state: { state: "OK" } } as never);
    run(64);
    expect(body.classList.contains("clear-hold")).toBe(false);
    expect(body.classList.contains("clear")).toBe(false);
    app.onMessage({ type: "safety", seq: 4, t_ms: 4, state: { state: "HOLD", precondition: "power_off_seen" } } as never);
    run(80);
    expect(body.classList.contains("clear-hold")).toBe(false);
  });

  it("a hold shows on the step card, not over it, and the same words are not repeated below", async () => {
    const { app, run } = await live();
    app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "a", index: 0, of: 2, text: "One.", page: 43, view: "front", power: "any" });
    app.onMessage({ type: "safety", seq: 2, t_ms: 2, state: { state: "HOLD", precondition: "power_off_seen" } } as never);
    app.onMessage({ type: "sentence", seq: 3, t_ms: 3, id: "s", text: "Show me the plug out of the socket." });
    run(0);
    expect(app.els.cardPause.textContent).toBe("Show me the plug out of the socket.");
    expect(app.els.pause.hidden).toBe(true);
    expect(app.els.sentences.textContent).toBe("");
    expect(app.els.done.hidden).toBe(true);
  });

  it("the step stays the card's title; the guide's newest line is a row under it, never in its place, no caption", async () => {
    const { app, run } = await live();
    app.onMessage({ type: "sentence", seq: 1, t_ms: 1, id: "s0", text: "Let's clean the filter." });
    run(0);
    expect(app.els.sentences.textContent).toBe("Let's clean the filter.");
    app.onMessage({ type: "step", seq: 2, t_ms: 2, job_id: "j", step_id: "a", index: 0, of: 2, text: "Remove filter system.", page: 43, view: "front", power: "any" });
    run(0);
    expect(app.els.cardText.textContent).toBe("Remove filter system.");
    expect(app.els.cardSaid.hidden).toBe(true);
    expect(app.els.sentences.textContent).toBe("");
    app.onMessage({ type: "sentence", seq: 3, t_ms: 3, id: "s1", text: "Lift the whole filter system out." });
    run(0);
    app.onMessage({ type: "sentence", seq: 4, t_ms: 4, id: "s2", text: "Move the camera closer to the part, please." });
    run(0);
    expect(app.els.cardText.textContent).toBe("Remove filter system.");
    expect(app.els.cardSaid.textContent).toBe("Move the camera closer to the part, please.");
    expect(app.els.cardStep.hidden).toBe(true);
    expect(app.els.sentences.textContent).toBe("");
    app.onMessage({ type: "step", seq: 5, t_ms: 5, job_id: "j", step_id: "b", index: 1, of: 2, text: "Rinse it.", page: 44, view: "front", power: "any" });
    run(0);
    expect(app.els.cardText.textContent).toBe("Rinse it.");
    expect(app.els.cardSaid.hidden).toBe(true);
  });

  it("a step the server reopens draws again at its earlier index, with nothing left over from the later step", async () => {
    const { app, run } = await live();
    app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "a", index: 0, of: 3, text: "Remove filter system.", page: 43, view: "front", power: "any" });
    app.onMessage({ type: "step", seq: 2, t_ms: 2, job_id: "j", step_id: "b", index: 1, of: 3, text: "Refit the filters.", page: 44, view: "front", power: "any" });
    app.onMessage({ type: "sentence", seq: 3, t_ms: 3, id: "s1", text: "Now close the door." });
    run(0);
    app.onMessage({ type: "step", seq: 4, t_ms: 4, job_id: "j", step_id: "a", index: 0, of: 3, text: "Remove filter system.", page: 43, view: "front", power: "any" });
    run(0);
    expect(app.els.cardIndex.textContent).toBe("Step 1 of 3");
    expect(app.els.cardText.textContent).toBe("Remove filter system.");
    expect(app.els.cardSaid.hidden).toBe(true);
    expect(app.els.cardProgress.style.width).toBe("33%");
  });

  it("a spoken answer is its own row under the step, never the title; it clears on tap and at the next step", async () => {
    const { app, run } = await live();
    app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "a", index: 0, of: 2, text: "Remove filter system.", page: 43, view: "front", power: "any" });
    app.onMessage({ type: "sentence", seq: 2, t_ms: 2, id: "u1.s1", text: "Lift the whole filter system out." });
    asked(app.s);
    app.onMessage({ type: "sentence", seq: 3, t_ms: 3, id: "u2.s1", text: "Yes, it lifts straight up." });
    app.onMessage({ type: "sentence", seq: 4, t_ms: 4, id: "u2.s2", text: "No tools needed." });
    run(0);
    expect(app.els.cardText.textContent).toBe("Remove filter system.");
    expect(app.els.cardSaid.textContent).toBe("Lift the whole filter system out.");
    expect(app.els.cardAnswer.hidden).toBe(false);
    expect(app.els.cardAnswerText.textContent).toBe("Yes, it lifts straight up. No tools needed.");
    expect(app.els.sentences.textContent).toBe("");
    app.els.cardAnswer.click();
    run(0);
    expect(app.els.cardAnswer.hidden).toBe(true);
    expect(app.els.cardText.textContent).toBe("Remove filter system.");
    asked(app.s);
    app.onMessage({ type: "sentence", seq: 5, t_ms: 5, id: "u3.s1", text: "Turn it a quarter turn." });
    run(0);
    expect(app.els.cardAnswer.hidden).toBe(false);
    app.onMessage({ type: "step", seq: 6, t_ms: 6, job_id: "j", step_id: "b", index: 1, of: 2, text: "Rinse it.", page: 44, view: "front", power: "any" });
    run(0);
    expect(app.els.cardAnswer.hidden).toBe(true);
    expect(app.els.cardText.textContent).toBe("Rinse it.");
  });

  it("the stop screen does not repeat 'Stop', keeps each number on one line and calls it with one tap", async () => {
    const { app, run } = await live(() => [], { numbers: [{ display: "0800-9009", tel: "08009009" }, { display: "112", tel: "112" }] });
    app.onMessage({ type: "safety", seq: 1, t_ms: 1, state: { state: "STOP", rule_id: "nl.gas.smell", evidence: "e", script: "Stop. Get everyone outside. Then call 0800-9009 from outside. If there is fire, call 112." } } as never);
    run(0);
    expect(app.els.stopText.textContent).toBe("Get everyone outside. Then call 0800-9009 from outside. If there is fire, call 112.");
    expect([...app.els.stopText.querySelectorAll(".nowrap")].map((x) => x.textContent)).toEqual(["0800-9009", "112"]);
    const calls = [...app.els.stopCalls.querySelectorAll("a")];
    expect(calls.map((a) => [a.textContent, a.getAttribute("href")])).toEqual([["Call 0800-9009", "tel:08009009"], ["Call 112", "tel:112"]]);
  });

  it("a STOP with no number says who to call and offers a way out; a recording offers itself again", async () => {
    const went: string[] = [];
    const mains = { type: "safety", seq: 1, t_ms: 1, state: { state: "STOP", rule_id: "nl.mains.panel", evidence: "e", script: "Stop. Mains power. Leave the back panel on; that's a technician." } } as never;
    const { app, run } = await live(() => [], { navigate: (x) => void went.push(x) });
    app.onMessage(mains);
    run(0);
    expect(app.els.stopWho.textContent).toBe("Who to call: the maker's service, or a qualified technician.");
    expect(app.els.stopWays.hidden).toBe(false);
    expect(app.els.stopAgain.hidden).toBe(true);
    expect(app.els.stopBack.textContent).toBe("Back to my jobs");
    app.els.stopBack.click();
    expect(went).toEqual([""]);
    // A stop with numbers names them, so the line isn't added.
    const gas = await live(() => [], { numbers: [{ display: "112", tel: "112" }] });
    gas.app.onMessage({ type: "safety", seq: 1, t_ms: 1, state: { state: "STOP", rule_id: "nl.gas.smell", evidence: "e", script: "Stop. Get everyone outside and call 112." } } as never);
    gas.run(0);
    expect(gas.app.els.stopWho.hidden).toBe(true);
    expect(gas.app.els.stopWays.hidden).toBe(false);

    const rec = deps({ navigate: (x) => void went.push(x) });
    const replay = new App(rec.d);
    await replay.boot();
    replay.onMessage(mains);
    rec.run(0);
    expect(replay.els.stopAgain.textContent).toBe("Watch again");
    expect(replay.els.stopBack.textContent).toBe("Go to the start");
    replay.els.stopAgain.click();
    expect(went.at(-1)).toBe("?replay=m1");
  });

  it("Done sends the done tap; Checking… hides it; a not-done or can't-see verdict says so in the phone's words", async () => {
    const { app, sock, run } = await live();
    app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "lift", index: 0, of: 2, text: "Lift.", page: 43, view: "front", power: "any" });
    run(0);
    expect(app.els.done.hidden).toBe(false);
    expect(app.els.done.textContent).toBe("Done");
    app.els.done.click();
    expect(sock.sent.at(-1)).toMatchObject({ type: "tap", action: "done", step_id: expect.any(String) });
    app.onMessage({ type: "checking", seq: 2, t_ms: 2, step_id: "lift" });
    run(16);
    expect(app.els.done.hidden).toBe(true);
    expect(app.els.checking.hidden).toBe(false);
    const v = { step_id: "lift", check_id: "c", attempt: 1, judge: "cant_see", clauses: [] } as const;
    app.onMessage({ type: "verdict", seq: 3, t_ms: 3, verdict: { ...v, result: "cant_see", ask_for: "light" } } as never);
    run(32);
    expect(app.els.verdict.textContent).toBe("I can't see it. Add more light.");
    expect(app.els.done.hidden).toBe(false);
    app.onMessage({ type: "verdict", seq: 4, t_ms: 4, verdict: { ...v, result: "not_done" } } as never);
    run(48);
    expect(app.els.verdict.textContent).toBe("Not done yet. Have another look at the step.");
    expect(app.els.verdict.classList.contains("not_done")).toBe(true);
  });

  it("asks 'Is this the gas tap?' on the server's hold, and sends the one-tap confirm", async () => {
    const { app, sock, run } = await live();
    app.onMessage({ type: "safety", seq: 1, t_ms: 1, state: { state: "HOLD", precondition: "user_confirm", rule_id: "nl.gas.tap" } });
    run(0);
    expect(app.els.gasTap.hidden).toBe(false);
    (app.els.gasTap.querySelector('[data-tap="confirm_gas_tap"]') as HTMLButtonElement).click();
    expect(sock.sent.at(-1)).toMatchObject({ type: "tap", action: "confirm_gas_tap" });
    expect(app.els.confirm.hidden).toBe(true);
  });

  it("answers the session's other user_confirm holds with yes, never as a gas-tap confirm", async () => {
    const { app, sock, run } = await live();
    app.onMessage({ type: "safety", seq: 1, t_ms: 1, state: { state: "HOLD", precondition: "user_confirm", detail: "ring step" } });
    run(0);
    expect(app.els.gasTap.hidden).toBe(true);
    expect(app.els.confirm.hidden).toBe(false);
    expect(app.els.confirmBtn.textContent).toBe("Yes");
    app.els.confirmBtn.click();
    expect(sock.sent.at(-1)).toMatchObject({ type: "tap", action: "yes" });
    app.onMessage({ type: "safety", seq: 2, t_ms: 2, state: { state: "HOLD", precondition: "user_confirm", detail: "stopped by the user" } });
    run(16);
    expect(app.els.confirmBtn.textContent).toBe("Resume");
  });

  it("a power_off_seen hold asks for yes once the plug is shown, and the yes goes to the server to look", async () => {
    const { app, sock, run } = await live();
    app.onMessage({ type: "safety", seq: 1, t_ms: 1, state: { state: "HOLD", precondition: "power_off_seen" } } as never);
    app.onMessage({ type: "sentence", seq: 2, t_ms: 2, id: "s", text: "Pull the plug out of the socket, show me the plug and the socket, then tap yes." });
    run(0);
    expect(app.els.gasTap.hidden).toBe(true);
    expect(app.els.confirm.hidden).toBe(false);
    expect(app.els.confirmBtn.textContent).toBe("Yes");
    app.els.confirmBtn.click();
    expect(sock.sent.at(-1)).toMatchObject({ type: "tap", action: "yes" });
    expect(sock.sent.at(-1)).not.toHaveProperty("step_id"); // not a step tap: no step
    // The yes clears nothing on the phone: the hold stands until the server says otherwise.
    run(16);
    expect(app.els.confirm.hidden).toBe(false);
    expect(app.els.didIt.hidden).toBe(true);
    app.onMessage({ type: "safety", seq: 3, t_ms: 3, state: { state: "OK" } } as never);
    run(32);
    expect(app.els.confirm.hidden).toBe(true);
  });

  it("yes on a plug hold shows Checking until the server answers, and takes no second tap meanwhile", async () => {
    const { app, sock, run, frame } = await live();
    const hold = (seq: number) => app.onMessage({ type: "safety", seq, t_ms: seq, state: { state: "HOLD", precondition: "power_off_seen" } } as never);
    hold(1);
    run(0);
    app.els.confirmBtn.click();
    expect(sock.sent.filter((m) => m.action === "yes")).toHaveLength(1);
    // The look at the plug: only its rail row so far, which is no answer.
    app.onMessage({ type: "rail", seq: 2, t_ms: 2, kind: "power", label: "plug and socket", text: "looking", conf: 0 } as never);
    run(16);
    expect(app.els.confirmBtn.textContent).toBe("Checking…");
    expect((app.els.confirmBtn as HTMLButtonElement).disabled).toBe(true);
    app.els.confirmBtn.click();
    expect(sock.sent.filter((m) => m.action === "yes")).toHaveLength(1);
    // The answer: it could not see the plug out. Yes is back for another try.
    app.onMessage({ type: "sentence", seq: 3, t_ms: 3, id: "s1", text: "I can't see the plug out of the socket yet." });
    run(32);
    expect(app.els.confirmBtn.textContent).toBe("Yes");
    expect((app.els.confirmBtn as HTMLButtonElement).disabled).toBe(false);
    // With no answer at all, Yes comes back after a while.
    app.els.confirmBtn.click();
    run(48);
    expect(app.els.confirmBtn.textContent).toBe("Checking…");
    await frame("f1", 21_000);
    run(64);
    expect(app.els.confirmBtn.textContent).toBe("Yes");
    // A yes on any other hold (resuming, the ring step) is answered at once and shows no Checking.
    app.onMessage({ type: "safety", seq: 4, t_ms: 4, state: { state: "HOLD", precondition: "user_confirm", detail: "ring" } } as never);
    run(80);
    app.els.confirmBtn.click();
    run(96);
    expect(app.els.confirmBtn.textContent).toBe("Yes");
  });

  it("a hold for the next step shows that step's card from the preview, not the finished step's", async () => {
    const { app, run } = await live();
    const ps = (step_id: string, text: string, power: string) => ({ step_id, text, page: 40, quote: text, power, camera: "check", warnings: [] });
    app.onMessage({
      type: "job_preview", seq: 1, t_ms: 1, job_id: "j", title: "Clean the pump.", needs: [],
      steps: [ps("warn", "Take care.", "any"), ps("unplug", "Unplug the dishwasher.", "off"), ps("open", "Open the cover.", "off")],
    } as never);
    app.onMessage({ type: "step", seq: 2, t_ms: 2, job_id: "j", step_id: "warn", index: 0, of: 3, text: "Take care.", page: 40, view: "front", power: "any" });
    run(0);
    app.els.didIt.click();
    app.onMessage({ type: "verdict", seq: 3, t_ms: 3, verdict: { step_id: "warn", check_id: "k", attempt: 1, result: "done", clause_ids: [], witnesses: { sam: "none", by_person: true }, latency_ms: 0 } } as never);
    app.onMessage({ type: "safety", seq: 4, t_ms: 4, state: { state: "HOLD", precondition: "power_off_seen" } } as never);
    // Before its ask names the step, the finished step's card does not stand under the hold.
    run(16);
    expect(app.els.card.hidden).toBe(true);
    expect(app.els.pause.textContent).toBe("Show me the plug out of the socket.");
    app.onMessage({ type: "sentence", seq: 5, t_ms: 5, id: "s1", step_id: "unplug", text: "Pull the plug out of the socket, show me the plug and the socket, then tap yes." });
    run(32);
    expect(app.els.card.hidden).toBe(false);
    expect(app.els.cardIndex.textContent).toBe("Step 2 of 3");
    expect(app.els.cardText.textContent).toBe("Unplug the dishwasher.");
    expect(app.els.cardPause.textContent).toBe("Show me the plug out of the socket.");
    expect(app.els.didIt.hidden).toBe(true);
    expect(app.els.confirm.hidden).toBe(false);
    // The gate passes: the step's own message, the same card.
    app.onMessage({ type: "safety", seq: 6, t_ms: 6, state: { state: "OK" } } as never);
    app.onMessage({ type: "step", seq: 7, t_ms: 7, job_id: "j", step_id: "unplug", index: 1, of: 3, text: "Unplug the dishwasher.", page: 40, view: "front", power: "off" });
    run(48);
    expect(app.els.cardIndex.textContent).toBe("Step 2 of 3");
    expect(app.els.cardPause.hidden).toBe(true);
    // A hold in the middle of a step stays on that step's card.
    app.onMessage({ type: "safety", seq: 8, t_ms: 8, state: { state: "HOLD", precondition: "fresh_frame" } } as never);
    app.onMessage({ type: "sentence", seq: 9, t_ms: 9, id: "s2", step_id: "unplug", text: "Hold the camera still for a moment." });
    run(64);
    expect(app.els.cardIndex.textContent).toBe("Step 2 of 3");
    expect(app.els.cardText.textContent).toBe("Unplug the dishwasher.");
  });

  it("the line about a tap for the step before goes after a few seconds, or when the next step comes", async () => {
    const { app, run, frame } = await live();
    const line = "That tap was for the step before. You're on this step now.";
    const step = (seq: number, id: string, index: number) =>
      app.onMessage({ type: "step", seq, t_ms: seq, job_id: "j", step_id: id, index, of: 4, text: `Step ${id}.`, page: 42, view: "front", power: "any" });
    step(1, "b", 1);
    await frame("f0", 1_000);
    app.onMessage({ type: "notice", seq: 2, t_ms: 2, kind: "tap_not_current", text: null } as never);
    run(1_016);
    expect(app.els.root.textContent).toContain(line);
    step(3, "c", 2);
    run(1_032);
    expect(app.els.root.textContent).not.toContain(line);
    app.onMessage({ type: "notice", seq: 4, t_ms: 4, kind: "tap_not_current", text: null } as never);
    run(1_048);
    expect(app.els.root.textContent).toContain(line);
    await frame("f1", 8_000);
    run(8_016);
    expect(app.els.root.textContent).not.toContain(line);
  });

  it("shows the plate card, asks the unsure character, and confirms with one tap", async () => {
    const { app, sock, run } = await live();
    app.onMessage({
      type: "plate_read",
      seq: 1,
      t_ms: 1,
      photo_id: "p1",
      brand: "acme",
      model: "WM-OOOO",
      fields: [{ name: "model", value: "WM-OOOO", uncertain: [{ index: 3, options: ["O", "0"] }] }],
    });
    run(0);
    expect(app.els.plate.textContent).toContain("Letter O or zero?");
    (app.els.plate.querySelector('[data-option="0"]') as HTMLButtonElement).click();
    (app.els.plate.querySelector('[data-act="confirm-plate"]') as HTMLButtonElement).click();
    expect(sock.sent.at(-1)).toMatchObject({ type: "tap", action: "confirm_plate", arg: '{"model":["0"]}' });
  });

  it("the plate card names each field in words", async () => {
    const { app, run } = await live();
    app.onMessage({
      type: "plate_read",
      seq: 1,
      t_ms: 1,
      photo_id: "p1",
      brand: "acme",
      model: "WM-0000",
      fields: [
        { name: "model", value: "WM-0000", uncertain: [] },
        { name: "E-Nr", value: "WM-0000/01", uncertain: [] },
        { name: "FD", value: "9001", uncertain: [{ index: 0, options: ["9", "8"] }] },
      ],
    });
    run(0);
    const text = app.els.plate.textContent ?? "";
    expect(text).toContain("E-Nr (product number)");
    expect(text).toContain("FD (production date)");
    expect(app.els.plate.hidden).toBe(false);
  });

  it("a stop cancels speech; the phone's own stop stays silent, the session's STOP script is heard", async () => {
    const said: string[] = [];
    let cancels = 0;
    let x = 0;
    const { app, frame } = await live(() => [{ x, y: 320 }], { synth: { speak: (u) => void said.push(u.text), cancel: () => void cancels++ } });
    app.tracker.push({ frameId: "f0", delta: [1, 0, 0, 0, 1, 0], points: 50, inliers: 50 });
    app.onMessage({ type: "zone", seq: 1, t_ms: 1, zone_id: "z1", rule_id: "nl.gas.tap", ring_cm: 3, mask_svg: '<path d="M500 300 L540 300 L540 340 L500 340 Z"/>', frame_id: "f0", known_width_cm: 4 });
    app.onMessage({ type: "sentence", seq: 2, t_ms: 2, id: "a", text: "Turn the dial." });
    await frame("f1", 0);
    x = 100;
    await frame("f2", 33); // the phone's own G1 stop
    expect(app.l.guard.kind).toBe("stop");
    expect(cancels).toBe(1);
    // The session doesn't know yet: its next sentence is not said.
    app.onMessage({ type: "sentence", seq: 3, t_ms: 3, id: "b", text: "Reach in now." });
    // The session's STOP: its script follows as a sentence, and is heard.
    app.onMessage({ type: "safety", seq: 4, t_ms: 4, state: { state: "STOP", rule_id: "nl.gas.tap", evidence: "hand", script: "Hands back." } });
    app.onMessage({ type: "sentence", seq: 5, t_ms: 5, id: "c", text: "Hands back." });
    expect(said).toEqual(["Turn the dial.", "Hands back."]);
  });

  it("fast path: a hand reaching a tracked red zone stops on the phone, sounds the alarm, and reports g1", async () => {
    let x = 0;
    const { app, sock, calls, run, frame } = await live(() => [{ x, y: 320 }]);
    // The tracker knows frame f0 (no motion) and is confident.
    app.tracker.push({ frameId: "f0", delta: [1, 0, 0, 0, 1, 0], points: 50, inliers: 50 });
    app.onMessage({
      type: "zone",
      seq: 1,
      t_ms: 1,
      zone_id: "z1",
      rule_id: "nl.gas.tap",
      ring_cm: 3,
      mask_svg: '<path d="M500 300 L540 300 L540 340 L500 340 Z"/>',
      frame_id: "f0",
      known_width_cm: 4,
    });
    x = 0; // 50 cm out
    await frame("f1", 0);
    expect(app.l.guard.kind).toBe("clear");
    x = 100; // 37 cm out, closing at 1.5 m/s
    await frame("f2", 33);
    run(50);
    expect(app.l.guard.kind).toBe("stop");
    expect(app.els.stop.hidden).toBe(false);
    expect(calls.alarm).toBe(1);
    expect(sock.sent.find((m) => m.type === "g1")).toMatchObject({ kind: "stop", zone_id: "z1", reason: "ttc", frame_id: "f2" });
    // The paint that showed red sends its time into the session record, with the landmark times so far.
    const timed = sock.sent.filter((m) => m.type === "timings");
    expect(timed.flatMap((m) => m.stops)).toEqual([{ frame_id: "f2", ms: expect.any(Number) }]);
    expect(timed.flatMap((m) => m.hands_ms).length).toBeGreaterThan(0);
  });
});

const D = {
  version: "test",
  first_run: { en: "FIRST RUN en", nl: "FIRST RUN nl" },
  spoken: { en: "SPOKEN en", nl: "SPOKEN nl" },
  step_citation: { en: "Manual p.{page}", nl: "Handleiding p.{page}" },
  terms: { en: "TERMS en" },
};

// Twenty days from now.
const SOON = new Date(Date.now() + 20 * 86_400_000).toISOString().slice(0, 10);

describe("home", () => {
  it("names the app, puts the most urgent job first with the one Start, and groups the rest by when", async () => {
    const went: string[] = [];
    const f = (async (u: string) =>
      new Response(
        String(u).includes("/due")
          ? JSON.stringify([
              { appliance_id: "a1", job_id: "salt", title: "Top up the salt", due_on: SOON, reason: "scheduled" },
              { appliance_id: "a1", job_id: "clean_filters", title: "Clean the filters", due_on: "2000-01-01", reason: "scheduled" },
              { appliance_id: "a1", job_id: "descale", title: "Descale", due_on: "2099-02-01", reason: "scheduled" },
              { appliance_id: "a1", job_id: "arms", title: "Spray arms", due_on: null, reason: "cycle" },
            ])
          : JSON.stringify([{ id: "a1", alias: "dishwasher", kind: "dishwasher", brand: "bosch", model: "SMV4HAX40N", plate_confirmed: true, card_status: "checked" }]),
        { status: 200 },
      )) as typeof fetch;
    const { d, run } = deps({ search: "", fetch: f, household: tokens("tok"), navigate: (x) => void went.push(x) });
    const app = new App(d);
    await app.boot();
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    run(0);
    expect(app.els.top.querySelector(".lockup")?.getAttribute("aria-label")).toBe("Fix-It Lens");
    expect(app.els.home.hidden).toBe(false);
    // The overdue job is next up, on its own, with the screen's only Start.
    const next = app.els.homeList.querySelector(".next-up") as HTMLElement;
    expect(next.classList.contains("overdue")).toBe(true);
    expect(next.querySelector(".kicker")?.textContent).toBe("Next up");
    expect(next.querySelector(".due-chip")?.textContent).toMatch(/^overdue by \d+ years$/);
    expect(next.querySelector(".next-head")?.textContent).toBe("Clean the filtersDishwasher");
    expect(app.els.homeList.querySelectorAll('[data-act="start-job"]').length).toBe(1);
    // The rest of the home by when, each row a tap to its sheet; a job counted in uses has no date and comes last.
    expect([...app.els.homeList.querySelectorAll("h2")].map((x) => x.textContent)).toEqual(["Keep it running", "Coming up", "Needs a date", "Something's wrong"]);
    const rows = [...app.els.homeList.querySelectorAll(".item:not(.tool)")].map((x) => x.textContent);
    expect(rows).toEqual([
      `Top up the saltDishwasher${fromNow(SOON, today())}`,
      `DescaleDishwasher${fromNow("2099-02-01", today())}`,
      "Spray armsDishwashercounted in uses, not dates",
    ]);
    expect(app.els.homeList.querySelectorAll('.item [data-act="open-sheet"] svg.glyph').length).toBe(3);
    (app.els.homeList.querySelector('[data-act="start-job"]') as HTMLButtonElement).click();
    expect(went).toEqual(["?appliance=a1&job=clean_filters"]);
    // Adding another appliance is on the Appliances tab once the home has jobs.
    expect(app.els.start.hidden).toBe(true);
  });

  it("a named job is shown up front: needs to tick, every step in full; the camera starts only on I'm ready, inside the tap", async () => {
    const sock = new Sock();
    let handsLoads = 0;
    const loadHands = async () => (handsLoads++, { detect: () => ({ points: [], hands: 0, ms: 0 }) });
    const { d, calls, run } = deps({ search: "?appliance=a1&job=clean_debris_filter", household: tokens("tok"), openSocket: () => sock, loadHands });
    const app = new App(d);
    await app.boot();
    await app.startLive();
    // The hand model loads while the person reads; the camera waits.
    expect(handsLoads).toBe(1);
    sock.onopen?.({});
    sock.onmessage?.({ data: JSON.stringify({ type: "hello_ok", seq: 0, t_ms: 0, session_id: "s1", resumed: false, mode: "api" }) });
    sock.onmessage?.({ data: JSON.stringify({ type: "held", seq: 1, t_ms: 1, rule_id: "nl.mains.wiring", line: "That's electrical work." }) });
    sock.onmessage?.({ data: JSON.stringify({ ...preview, seq: 2 }) });
    run(0);
    expect(calls.camera).toBe(0);
    expect(app.els.bar.hidden).toBe(false);
    expect(app.els.preview.hidden).toBe(false);
    expect(app.els.controls.hidden).toBe(true);
    const txt = app.els.preview.textContent!;
    expect(txt).toContain("Clean the debris filter");
    for (const st of preview.steps) expect(txt).toContain(st.text);
    for (const st of preview.steps) if ("quote" in st && st.quote) expect(txt).toContain(st.quote);
    expect(txt).toContain("The camera shows where");
    expect(txt).toContain("Do not open the casing.");
    expect(txt).toContain("Are you ready?");
    const box = app.els.preview.querySelector<HTMLInputElement>('input[data-act="need"][value="towel"]')!;
    box.checked = true;
    box.dispatchEvent(new Event("change", { bubbles: true }));
    run(16);
    app.els.preview.querySelector<HTMLButtonElement>('[data-act="job-ready"]')!.click();
    expect(sock.sent.at(-1)).toMatchObject({ type: "job_ready", job_id: "clean_debris_filter", ready: ["towel"] });
    expect(calls.camera).toBe(1); // opened inside the tap
    expect(handsLoads).toBe(1); // the one loaded during the preview
    run(32);
    expect(app.els.preview.hidden).toBe(true);
    // The preview's held line went with it; one held after Ready shows.
    expect(app.els.bar.hidden).toBe(true);
    sock.onmessage?.({ data: JSON.stringify({ type: "held", seq: 3, t_ms: 3, step_id: "s1", rule_id: "nl.mains.wiring", line: "That's electrical work." }) });
    run(48);
    expect(app.els.bar.hidden).toBe(false);
  });

  it("while a check's result shows, the guide's line saying the same again waits", async () => {
    const { app, run } = await live();
    app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "a", index: 0, of: 2, text: "Clean the filters.", page: 42, view: "front", power: "any" });
    app.onMessage({ type: "verdict", seq: 2, t_ms: 2, verdict: { step_id: "a", check_id: "k1", attempt: 1, result: "cant_see", ask_for: "closer", clause_ids: [], witnesses: {} } } as never);
    app.onMessage({ type: "sentence", seq: 3, t_ms: 3, id: "s1", text: "Move the camera closer to the part, please." });
    run(0);
    expect(app.els.verdict.textContent).toBe("I can't see it. Move closer.");
    expect(app.els.cardSaid.hidden).toBe(true);
    app.onMessage({ type: "checking", seq: 4, t_ms: 4, step_id: "a", check_id: "k2" } as never);
    run(16);
    expect(app.els.cardSaid.textContent).toBe("Move the camera closer to the part, please.");
  });

  it("a line about a step the job has left is neither shown nor said; a line tied to no step always is", async () => {
    const said: string[] = [];
    const { app, run } = await live(undefined, { synth: { speak: (u) => void said.push(u.text), cancel: () => undefined } });
    app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "a", index: 0, of: 2, text: "Clean the filters.", page: 42, view: "front", power: "any" });
    app.onMessage({ type: "step", seq: 2, t_ms: 2, job_id: "j", step_id: "b", index: 1, of: 2, text: "Screw it in.", page: 42, view: "front", power: "any" });
    app.onMessage({ type: "sentence", seq: 3, t_ms: 3, id: "s1", step_id: "a", text: "Now the filter is clean." });
    app.onMessage({ type: "held", seq: 4, t_ms: 4, step_id: "a", rule_id: "R1", line: "late" } as never);
    run(0);
    expect(app.els.cardSaid.hidden).toBe(true);
    expect(app.els.bar.hidden).toBe(true);
    expect(said).toEqual([]);
    app.onMessage({ type: "sentence", seq: 5, t_ms: 5, id: "s2", step_id: "b", text: "Turn it clockwise." });
    run(16);
    expect(app.els.cardSaid.textContent).toBe("Turn it clockwise.");
    app.onMessage({ type: "sentence", seq: 6, t_ms: 6, id: "s3", text: "Hands away from the drum." });
    run(32);
    expect(app.els.cardSaid.textContent).toBe("Hands away from the drum.");
    // The next step's ask comes before the step itself: it is about what comes, so it shows.
    app.onMessage({ type: "sentence", seq: 7, t_ms: 7, id: "s4", step_id: "c", text: "Unplug it first." });
    run(48);
    expect(app.els.cardSaid.textContent).toBe("Unplug it first.");
    expect(said).toEqual(["Turn it clockwise.", "Hands away from the drum.", "Unplug it first."]);
    // Taken up again, a step's lines show again.
    app.onMessage({ type: "step", seq: 8, t_ms: 8, job_id: "j", step_id: "a", index: 0, of: 2, text: "Clean the filters.", page: 42, view: "front", power: "any" });
    app.onMessage({ type: "sentence", seq: 9, t_ms: 9, id: "s5", step_id: "a", text: "Once more, the filter." });
    run(64);
    expect(app.els.cardSaid.textContent).toBe("Once more, the filter.");
  });

  it("the session's hold for a fresh frame asks the gate for one; its end lets go", async () => {
    const { app } = await live();
    app.onMessage({ type: "safety", seq: 1, t_ms: 1, state: { state: "HOLD", precondition: "fresh_frame" } } as never);
    expect(app.gate.wantsFresh).toBe(true);
    app.onMessage({ type: "safety", seq: 2, t_ms: 2, state: { state: "OK" } } as never);
    expect(app.gate.wantsFresh).toBe(false);
  });

  it("on a step the camera does not check, one button: I did it, no Done", async () => {
    const { app, run } = await live();
    app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "a", index: 0, of: 2, text: "Open the flap.", page: 4, view: "front", power: "any", camera: "find" } as never);
    run(0);
    expect(app.els.done.hidden).toBe(true);
    expect(app.els.didIt.hidden).toBe(false);
    app.onMessage({ type: "step", seq: 2, t_ms: 2, job_id: "j", step_id: "b", index: 1, of: 2, text: "Screw it in.", page: 4, view: "front", power: "any" });
    run(16);
    expect(app.els.done.hidden).toBe(false);
  });

  it("a job's Start opens the session on that job inside the press, with no reload", async () => {
    const went: string[] = [];
    const swapped: string[] = [];
    const sock = new Sock();
    const f = (async (u: string) =>
      new Response(
        String(u).includes("/due")
          ? JSON.stringify([{ appliance_id: "a1", job_id: "clean_filters", title: "Clean the filters", due_on: "2000-01-01", reason: "scheduled" }])
          : JSON.stringify([{ id: "a1", alias: "dishwasher", kind: "dishwasher", brand: "bosch", model: "SMV4HAX40N", plate_confirmed: true, card_status: "checked" }]),
        { status: 200 },
      )) as typeof fetch;
    const { d, calls, run } = deps({
      search: "",
      fetch: f,
      household: tokens("tok"),
      openSocket: () => sock,
      navigate: (x) => void went.push(x),
      replaceSearch: (x) => void swapped.push(x),
    });
    const app = new App(d);
    await app.boot();
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    run(0);
    (app.els.homeList.querySelector('[data-act="start-job"]') as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 0));
    expect(went).toEqual([]);
    expect(swapped).toEqual(["?appliance=a1&job=clean_filters"]);
    expect(calls.camera).toBe(0); // the job is shown up front first: the camera waits for Ready (or the first step)
    sock.onopen?.({});
    expect(sock.sent[0]).toMatchObject({ type: "hello", appliance_id: "a1", job_id: "clean_filters" });
    // Before the first step the camera says what it is waiting on, not to photograph a label it doesn't need.
    (app as unknown as { l: { cards: Record<string, unknown> } }).l.cards.a1 = { jobs: [{ id: "clean_filters", title: "Clean the filters" }] };
    sock.onmessage?.({ data: JSON.stringify({ type: "hello_ok", seq: 0, t_ms: 0, session_id: "s1", resumed: false, mode: "api" }) });
    run(0);
    expect(app.els.hint.textContent).toBe("Getting the first step from the manual…");
    expect(app.els.hint.classList.contains("wait")).toBe(true);
    expect(app.els.moreRow.hidden).toBe(true);
    // A dropped line says it is reconnecting, with the same quiet turn.
    sock.onclose?.({ code: 1006 });
    run(0);
    expect(app.els.notice.textContent).toBe("Offline — reconnecting…");
    expect(app.els.notice.classList.contains("wait")).toBe(true);
  });

  it("a camera opened with no job asks for the label until a step comes", async () => {
    const sock = new Sock();
    const f = (async (u: string) =>
      new Response(String(u).includes("/due") ? "[]" : JSON.stringify([{ id: "a1", alias: "dishwasher", kind: "dishwasher", brand: "bosch", model: "SMV4HAX40N", plate_confirmed: true, card_status: "checked" }]), { status: 200 })) as typeof fetch;
    const { d, run } = deps({ search: "", fetch: f, household: tokens("tok"), openSocket: () => sock });
    const app = new App(d);
    await app.boot();
    await new Promise((r) => setTimeout(r, 0));
    run(0);
    // The way in from a home with appliances: the Appliances tab, Add an appliance, Use the camera.
    (app.els.root.querySelector('[data-act="devices"]') as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 0));
    run(0);
    (app.els.root.querySelector('[data-act="devices-add-open"]') as HTMLButtonElement).click();
    run(0);
    (app.els.root.querySelector('#devices [data-act="start"]') as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 0));
    sock.onopen?.({});
    sock.onmessage?.({ data: JSON.stringify({ type: "hello_ok", seq: 0, t_ms: 0, session_id: "s1", resumed: false, mode: "api" }) });
    run(0);
    expect(app.els.hint.textContent).toMatch(/^Point the camera at the label with the model number/);
    expect(app.els.hint.classList.contains("wait")).toBe(false);
    expect(app.els.moreRow.hidden).toBe(false);
  });

  it("asks when an unanswered job was last done, and the server's answer becomes its due date", async () => {
    const sent: { url: string; method: string; body: string; auth: string | null }[] = [];
    let status = 200;
    const f = (async (u: string, init?: RequestInit) => {
      const url = String(u);
      if (init?.method === "PUT") {
        sent.push({ url, method: "PUT", body: String(init.body), auth: new Headers(init.headers).get("authorization") });
        if (status !== 200) return new Response("{}", { status });
        return new Response(JSON.stringify({ appliance_id: "a1", job_id: "machine_cleaning", title: "Run the machine care programme", due_on: SOON, reason: "scheduled" }), { status: 200 });
      }
      if (url.endsWith("/appliances/a1/card")) return new Response(JSON.stringify({ jobs: [{ id: "machine_cleaning", due_name: { en: "Machine Clean", nl: "machinereiniging" } }] }), { status: 200 });
      if (url.includes("/due")) {
        expect(url).not.toContain("within_days");
        return new Response(JSON.stringify([{ appliance_id: "a1", job_id: "machine_cleaning", title: "Run the machine care programme", due_on: null, reason: "unanswered" }]), { status: 200 });
      }
      return new Response(JSON.stringify([{ id: "a1", alias: "dishwasher", kind: "dishwasher", brand: "bosch", model: "X", plate_confirmed: true, card_status: "checked" }]), { status: 200 });
    }) as typeof fetch;
    const { d, run } = deps({ search: "", fetch: f, household: tokens("tok") });
    const app = new App(d);
    await app.boot();
    const tick = () => new Promise((r) => setTimeout(r, 0));
    await tick();
    await tick();
    run(0);
    const row = () => app.els.homeList.querySelector(".item") as HTMLElement;
    // The card's due_name, as the push names it, over the step title.
    expect(row().querySelector("strong")?.textContent).toBe("Machine Clean");
    expect(row().querySelector("legend")?.textContent).toBe("When did you last do this?");
    expect([...row().querySelectorAll(".last-done button")].map((b) => b.textContent)).toEqual(["Never", "Don't know", "Save date"]);
    const date = () => row().querySelector('input[type="date"]') as HTMLInputElement;
    expect(date().max).toMatch(/^\d{4}-\d{2}-\d{2}$/);

    // Save waits for a date, and with none picked sends nothing.
    expect((row().querySelector('[data-act="last-date"]') as HTMLButtonElement).disabled).toBe(true);
    (row().querySelector('[data-act="last-date"]') as HTMLButtonElement).click();
    expect(sent).toEqual([]);

    status = 500;
    (row().querySelector('[data-act="last-never"]') as HTMLButtonElement).click();
    await tick();
    run(16);
    expect(row().textContent).toContain("Couldn't save that. Try again.");

    status = 422;
    date().value = "2026-09-03";
    date().dispatchEvent(new Event("input", { bubbles: true }));
    (row().querySelector('[data-act="last-date"]') as HTMLButtonElement).click();
    await tick();
    run(32);
    expect(row().textContent).toContain("That date is still to come.");
    expect(date().value).toBe("2026-09-03");

    status = 200;
    (row().querySelector('[data-act="last-unknown"]') as HTMLButtonElement).click();
    await tick();
    await tick();
    run(48);
    expect(sent.map((x) => [x.url, x.body, x.auth])).toEqual([
      ["/api/v1/appliances/a1/jobs/machine_cleaning/last-done", '{"source":"never"}', "Bearer tok"],
      ["/api/v1/appliances/a1/jobs/machine_cleaning/last-done", '{"source":"user","date":"2026-09-03"}', "Bearer tok"],
      ["/api/v1/appliances/a1/jobs/machine_cleaning/last-done", '{"source":"unknown"}', "Bearer tok"],
    ]);
    expect(app.els.homeList.querySelector(".last-done")).toBeNull();
    // Answered, it is the next job up.
    expect(app.els.homeList.querySelector(".next-head")?.textContent).toBe(`Machine CleanDishwasher · ${fromNow(SOON, today())}`);
  });

  it("with no household, how a repair goes and Point at an appliance", async () => {
    const { d, run } = deps({ search: "" });
    const app = new App(d);
    await app.boot();
    run(0);
    expect(app.els.homeIntro.hidden).toBe(false);
    expect(app.els.start.textContent).toBe("Point at an appliance");
  });
});

describe("disclaimer and AI disclosure", () => {
  it("shows first_run until accepted, and Start waits for it", async () => {
    let marked = 0;
    const { d, calls, run } = deps({ search: "", disclaimers: D, firstRunSeen: () => false, markFirstRun: () => void marked++ });
    const app = new App(d);
    await app.boot();
    run(0);
    expect(app.els.firstRun.hidden).toBe(false);
    expect(app.els.firstRunText.textContent).toBe("FIRST RUN en");
    expect(app.els.settings.hidden).toBe(true);
    expect(app.els.tabs.hidden).toBe(true);
    expect(app.els.gear.hidden).toBe(true);
    expect(app.els.start.hidden).toBe(true);
    await app.startLive();
    expect(calls.camera).toBe(0);
    (app.els.firstRun.querySelector('[data-act="accept-first-run"]') as HTMLButtonElement).click();
    run(16);
    expect(marked).toBe(1);
    expect(app.els.firstRun.hidden).toBe(true);
    expect(app.els.start.hidden).toBe(false);
    expect(app.els.terms.textContent).toBe("TERMS en");
  });

  it("in local mode asks for an on-device voice; with none it stays silent, shows the line, and tells only the operator", async () => {
    const asked: { text: string; onDeviceOnly: boolean }[] = [];
    const synth = { speak: (u: { text: string; onDeviceOnly: boolean }) => (asked.push({ text: u.text, onDeviceOnly: u.onDeviceOnly }), false), cancel: () => undefined };
    for (const debug of [false, true]) {
      asked.length = 0;
      const { d, run } = deps({ search: debug ? "?debug=1" : "", disclaimers: D, synth, openSocket: () => new Sock() });
      const app = new App(d);
      await app.boot();
      await app.startLive();
      app.onMessage({ type: "hello_ok", seq: 0, t_ms: 0, session_id: "s9", resumed: false, mode: "local" });
      app.onMessage({ type: "sentence", seq: 1, t_ms: 1, id: "s1", text: "Lift out the filter." });
      run(0);
      expect(asked).toEqual([{ text: "Lift out the filter.", onDeviceOnly: true }]);
      expect(app.els.sentences.textContent).toContain("Lift out the filter.");
      expect(app.els.root.textContent?.includes("No on-device voice")).toBe(debug);
    }
  });

  it("in local mode with no on-device voice, a STOP script line is still heard (fallback voice); other lines stay silent", async () => {
    const said: { text: string; voice: string | null }[] = [];
    const ss = {
      getVoices: () => [{ lang: "en-US", name: "Google US English", localService: false }] as SpeechSynthesisVoice[],
      speak: (u: SpeechSynthesisUtterance) => void said.push({ text: u.text, voice: u.voice?.name ?? null }),
      cancel: () => undefined,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    };
    const utter = (text: string) => ({ text, lang: "", voice: null }) as unknown as SpeechSynthesisUtterance;
    const { d } = deps({ search: "", disclaimers: D, synth: browserSynth(ss, utter), openSocket: () => new Sock() });
    const app = new App(d);
    await app.boot();
    await app.startLive();
    const script = "Leave the house now. Call 0800-9009 from outside.";
    app.onMessage({ type: "hello_ok", seq: 0, t_ms: 0, session_id: "s9", resumed: false, mode: "local" });
    app.onMessage({ type: "sentence", seq: 1, t_ms: 1, id: "s1", text: "Lift out the filter." });
    app.onMessage({ type: "safety", seq: 2, t_ms: 2, state: { state: "STOP", rule_id: "nl.gas.smell", evidence: "user said: I smell gas", script } } as S2C);
    app.onMessage({ type: "sentence", seq: 3, t_ms: 3, id: "s2", text: "Leave the house now." });
    app.onMessage({ type: "sentence", seq: 4, t_ms: 4, id: "s3", text: "Call 0800-9009 from outside." });
    app.onMessage({ type: "sentence", seq: 5, t_ms: 5, id: "s4", text: "I'm sorry about the filter." });
    expect(said).toEqual([
      { text: "Leave the house now.", voice: "Google US English" },
      { text: "Call 0800-9009 from outside.", voice: "Google US English" },
    ]);
  });

  it("outside local mode any voice will do", async () => {
    const asked: boolean[] = [];
    const { d } = deps({ search: "", disclaimers: D, synth: { speak: (u) => void asked.push(u.onDeviceOnly), cancel: () => undefined }, openSocket: () => new Sock() });
    const app = new App(d);
    await app.boot();
    await app.startLive();
    app.onMessage({ type: "hello_ok", seq: 0, t_ms: 0, session_id: "s9", resumed: false, mode: "api" });
    app.onMessage({ type: "sentence", seq: 1, t_ms: 1, id: "s1", text: "Lift out the filter." });
    expect(asked).toEqual([false]);
  });

  it("leaves the spoken line to the session (it says only sentences), and cites the page on the step card", async () => {
    const said: string[] = [];
    const { d, run } = deps({ search: "", lang: "nl", disclaimers: D, synth: { speak: (u) => void said.push(u.text), cancel: () => undefined }, openSocket: () => new Sock() });
    const app = new App(d);
    await app.boot();
    await app.startLive();
    app.onMessage({ type: "hello_ok", seq: 0, t_ms: 0, session_id: "s9", resumed: false, mode: "api" });
    app.onMessage({ type: "sentence", seq: 1, t_ms: 1, id: "s1", text: "Draai de kraan dicht." });
    app.onMessage({ type: "step", seq: 2, t_ms: 2, job_id: "j", step_id: "s", index: 0, of: 1, text: "Lift.", page: 43, view: "front", power: "any" });
    run(0);
    expect(said).toEqual(["Draai de kraan dicht."]);
    expect(app.els.cardPage.textContent).toBe("Handleiding p.43");
    // Show page opens the session's page image (the route serves only pages the card cites), and closes.
    expect(app.els.showPage.hidden).toBe(false);
    app.els.showPage.click();
    run(16);
    expect(app.els.pageView.hidden).toBe(false);
    expect(app.els.pageImg.getAttribute("src")).toBe("/api/v1/sessions/s9/pages/43.png");
    (app.els.pageView.querySelector('[data-act="close-page"]') as HTMLButtonElement).click();
    run(32);
    expect(app.els.pageView.hidden).toBe(true);
    // A language the file lacks falls back to English.
    expect(app.els.terms.textContent).toBe("TERMS en");
  });
});

describe("household token (pilot door)", () => {
  function server(status = 201) {
    const seen: { url: string; auth: string | null; body: unknown }[] = [];
    const f = (async (u: string, init?: RequestInit) => {
      const auth = new Headers(init?.headers).get("authorization");
      seen.push({ url: String(u), auth, body: init?.body ? JSON.parse(String(init.body)) : null });
      if (String(u).endsWith("/households")) return new Response(JSON.stringify({ id: "h1", token: "tok-1" }), { status });
      if (String(u).includes("/note")) return new Response("{}", { status: auth === "Bearer tok-1" ? 200 : 401 });
      if (String(u).endsWith("/household")) return new Response("{}", { status: auth === "Bearer stale" ? 401 : 200 });
      return new Response("{}", { status: 200 });
    }) as typeof fetch;
    return { f, seen };
  }

  it("joins from an invite link with one tap, keeps the token, and sends it in hello and as the bearer", async () => {
    const { f, seen } = server();
    const store = tokens(null);
    const sock = new Sock();
    const copied: string[] = [];
    const { d, run } = deps({
      search: "?invite=INV-42",
      fetch: f,
      household: store,
      disclaimers: { version: "t", first_run: { en: "FIRST" }, spoken: {}, step_citation: {}, terms: {} },
      firstRunSeen: () => true, // an invite asks for consent even on a phone that saw the screen
      openSocket: () => sock,
      copy: async (t) => void copied.push(t),
    });
    const app = new App(d);
    await app.boot();
    run(0);
    expect(app.els.firstRun.hidden).toBe(false);
    expect(app.els.connLink.hidden).toBe(true);
    // Consent is its own tick box (the in-app consent's wording; a visible placeholder until it is set): no tick, nothing is sent.
    expect(app.els.consent.hidden).toBe(false);
    expect(app.els.consentText.textContent).toContain("App consent");
    const go = app.els.firstRun.querySelector('[data-act="accept-first-run"]') as HTMLButtonElement;
    // Continue waits for the tick: disabled, and a tap on it sends nothing.
    expect(go.disabled).toBe(true);
    go.click();
    await new Promise((r) => setTimeout(r, 0));
    run(8);
    expect(seen).toEqual([]);
    app.els.consentBox.click();
    run(12);
    expect(go.disabled).toBe(false);
    go.click();
    await new Promise((r) => setTimeout(r, 0));
    run(16);
    expect(seen[0]).toMatchObject({ url: "/api/v1/households", body: { invite_code: "INV-42", consent: true, lang: "en" } });
    expect(store.now()).toBe("tok-1");
    expect(app.els.firstRun.hidden).toBe(true);
    // With a home: the tabs and Settings. Household offers Connections, and nothing shows the household token itself.
    run(32);
    expect(app.els.tabs.hidden).toBe(false);
    expect(app.els.gear.hidden).toBe(false);
    expect(app.els.connLink.hidden).toBe(true);
    app.route("#people");
    run(40);
    expect(app.els.connLink.hidden).toBe(false);
    expect(app.els.root.textContent).not.toContain("tok-1");
    expect(copied).toEqual([]);
    await app.startLive();
    sock.onopen?.({});
    expect(sock.sent[0]).toMatchObject({ type: "hello", household: "tok-1" });
  });

  it("a used invite says so and keeps the screen; a 401 forgets the token and asks to join again", async () => {
    const { f } = server(403);
    const { d, run } = deps({ search: "?invite=OLD", fetch: f, household: tokens(null) });
    const app = new App(d);
    await app.boot();
    app.els.consentBox.click();
    (app.els.firstRun.querySelector('[data-act="accept-first-run"]') as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 0));
    run(0);
    expect(app.els.joinError.textContent).toContain("already used");
    expect(app.els.firstRun.hidden).toBe(false);

    const store = tokens("stale");
    const b = deps({ search: "", fetch: server().f, household: store });
    const app2 = new App(b.d);
    await app2.boot();
    const r = await app2.api("/api/v1/sessions/s/note", { method: "POST" });
    await new Promise((r) => setTimeout(r, 0));
    b.run(0);
    expect(r.status).toBe(401);
    expect(store.now()).toBeNull();
    expect(app2.els.firstRun.hidden).toBe(false);
    expect(app2.els.joinError.textContent).toBe(JOIN_TEXT.lost);
  });

  it("a person removed while the door is shut is told to ask for a new invite, not that the door is shut", async () => {
    const base = server().f;
    const f = (async (u: string, init?: RequestInit) =>
      String(u).endsWith("/households/door") ? new Response(JSON.stringify({ state: "shut", reopens_at: null }), { status: 200 }) : base(u, init)) as typeof fetch;
    const store = tokens("stale");
    const b = deps({ search: "", fetch: f, household: store, golden: async () => true });
    const app = new App(b.d);
    await app.boot();
    await app.api("/api/v1/sessions/s/note", { method: "POST" });
    for (let i = 0; i < 6; i++) await new Promise((r) => setTimeout(r, 0));
    b.run(0);
    expect(store.now()).toBeNull();
    expect(app.els.firstRun.hidden).toBe(false);
    expect(app.els.joinError.textContent).toBe(JOIN_TEXT.lostShut);
    expect(app.els.doorWatch.hidden).toBe(false);
  });

  it("a stray 401 is asked again with GET /household: a token that still opens the home is kept", async () => {
    const store = tokens("still-good");
    const b = deps({ search: "", fetch: server().f, household: store });
    const app = new App(b.d);
    await app.boot();
    const r = await app.api("/api/v1/sessions/s/note", { method: "POST" });
    await new Promise((r) => setTimeout(r, 0));
    b.run(0);
    expect(r.status).toBe(401);
    expect(store.now()).toBe("still-good");
    expect(app.els.joinError.textContent).not.toBe(JOIN_TEXT.lost);
  });

  it("each new token takes this browser's push subscription with it, from the token before", async () => {
    const moved: [string | null, string][] = [];
    const { f } = server();
    const { d, run } = deps({ search: "?invite=INV-42", fetch: f, household: tokens(null), pushMove: async (a, b) => void moved.push([a, b]) });
    const app = new App(d);
    await app.boot();
    app.els.consentBox.click();
    run(4);
    (app.els.firstRun.querySelector('[data-act="accept-first-run"]') as HTMLButtonElement).click();
    await new Promise((r) => setTimeout(r, 0));
    expect(moved).toEqual([[null, "tok-1"]]);
  });

  /** A door server: each path's reply, and every request seen. */
  function door(replies: Record<string, [number, unknown]>) {
    const seen: { url: string; auth: string | null; body: unknown }[] = [];
    const f = (async (u: string, init?: RequestInit) => {
      const url = String(u);
      seen.push({ url, auth: new Headers(init?.headers).get("authorization"), body: init?.body ? JSON.parse(String(init.body)) : null });
      const hit = Object.keys(replies).find((k) => url.endsWith(k));
      if (hit) return new Response(JSON.stringify(replies[hit]![1]), { status: replies[hit]![0] });
      return new Response("[]", { status: 200 });
    }) as typeof fetch;
    return { f, seen };
  }
  const tick = () => new Promise((r) => setTimeout(r, 0));
  async function consentAndGo(app: App, run: (t: number) => void): Promise<void> {
    app.els.consentBox.click();
    run(4); // Continue waits for the tick
    (app.els.firstRun.querySelector('[data-act="accept-first-run"]') as HTMLButtonElement).click();
    for (let i = 0; i < 4; i++) await tick();
  }

  it("the site's Start opens the open door: consent and age, then the household, then the camera", async () => {
    const { f, seen } = door({ "/households": [201, { id: "h1", token: "tok-open" }] });
    const store = tokens(null);
    let cleared = 0;
    const { d, run, calls } = deps({ search: "", path: "/start", fetch: f, household: store, firstRunSeen: () => true, clearDoor: () => void cleared++, openSocket: () => new Sock() });
    const app = new App(d);
    await app.boot();
    run(0);
    expect(cleared).toBe(1);
    expect(app.els.firstRun.hidden).toBe(false);
    expect(app.els.consent.hidden).toBe(false);
    await consentAndGo(app, run);
    run(16);
    expect(seen.find((x) => x.url === "/api/v1/households")?.body).toEqual({ consent: true, age_ok: true, lang: "en" });
    expect(store.now()).toBe("tok-open");
    expect(app.els.firstRun.hidden).toBe(true);
    expect(calls.camera).toBe(1);
  });

  it("a new home goes straight to the camera after consent: no name is asked until the first invite", async () => {
    const me = { id: "p1", name: "Admin", role: "admin", view_only: false, me: true };
    const { f, seen } = door({ "/households": [201, { id: "h1", token: "tok-open" }], "/household/people": [200, [me]] });
    const { d, run, calls } = deps({ search: "", path: "/start", fetch: f, household: tokens(null), firstRunSeen: () => true, openSocket: () => new Sock() });
    const app = new App(d);
    await app.boot();
    await consentAndGo(app, run);
    run(16);
    expect(app.els.join.hidden).toBe(true);
    expect(calls.camera).toBe(1);
    expect(seen.some((x) => x.url.includes("/household/people/"))).toBe(false);
  });

  it("every door shows the in-app consent, a pilot invite too: never a line about a signed form", async () => {
    const app_ = { text: "I am 18 or older and agree to this.", status: "Draft", heading: "Before you start, please", body: ["Pictures of the appliance leave this phone for Meta's AI service."] };
    const open = async (over: Partial<Parameters<typeof deps>[0]>) => {
      const { d, run } = deps({ household: tokens(null), appConsent: app_, openSocket: () => new Sock(), ...over });
      const app = new App(d);
      await app.boot();
      run(0);
      expect(app.els.consent.hidden).toBe(false);
      return app.els.consentText.textContent ?? "";
    };
    for (const door of [{ search: "?invite=INV-42" }, { search: "?start" }, { search: "", path: "/start" }, { search: "", path: "/try", hash: "#k=abc" }]) {
      const text = await open(door);
      expect(text).toBe(app_.text);
      expect(text).not.toContain("pilot form");
    }
    // The in-app screen's paragraphs sit above the box as ruled facts, under its own heading, with its draft tag.
    const s = deps({ search: "?start", household: tokens(null), appConsent: app_ });
    const app = new App(s.d);
    await app.boot();
    s.run(0);
    expect(app.els.firstRunTitle.textContent).toBe("Before you start, please");
    expect([...app.els.consentBody.querySelectorAll("li")].map((p) => p.textContent)).toEqual(app_.body);
    expect(app.els.consentStatus.textContent).toBe("Draft");
    // The box says 18 or older, so the button only continues, and says it waits for the tick.
    expect(app.els.firstRunGo.textContent).toBe("Tick the box to continue");
    expect(app.els.firstRunGo.disabled).toBe(true);
    // Before the in-app line is published, the door shows its own placeholder.
    const placeholder = await open({ search: "?start", appConsent: null });
    expect(placeholder).toBe(APP_CONSENT_PLACEHOLDER);
  });

  it("a printed QR's bare /try is the open door: open joins, shut or capped offers the replay, #k= stays a judges' link", async () => {
    const via = async (path: string, hash: string, reply: [number, unknown]) => {
      const { f, seen } = door({ "/households": reply, "/households/try": [201, { id: "h", token: "tok-try" }] });
      const { d, run, calls } = deps({ search: "", path, hash, fetch: f, household: tokens(null), golden: async () => true, firstRunSeen: () => true, openSocket: () => new Sock() });
      const app = new App(d);
      await app.boot();
      run(0);
      expect(app.els.firstRun.hidden).toBe(false);
      await consentAndGo(app, run);
      run(16);
      return { app, seen, calls };
    };
    const open = await via("/try", "", [201, { id: "h", token: "tok-open" }]);
    expect(open.seen.find((x) => x.url === "/api/v1/households")?.body).toEqual({ consent: true, age_ok: true, lang: "en" });
    expect(open.calls.camera).toBe(1);

    for (const reply of [[403, { error: "door_closed" }], [429, { error: "global_cap", reopens_at: null }]] as [number, unknown][]) {
      const shut = await via("/try", "", reply);
      expect(shut.app.els.firstRun.hidden).toBe(false);
      expect(shut.app.els.joinError.hidden).toBe(false);
      expect(shut.app.els.doorWatch.hidden).toBe(false);
      expect(shut.calls.camera).toBe(0);
    }

    const judges = await via("/try", "#k=JUDGE1", [500, {}]);
    expect(judges.seen.find((x) => x.url.endsWith("/households/try"))?.body).toMatchObject({ code: "JUDGE1" });
    expect(judges.seen.some((x) => x.url === "/api/v1/households")).toBe(false);
  });

  it("the open door reads its state first: shut or capped offers the replay with no consent asked, open asks as usual", async () => {
    const at = async (state: unknown) => {
      const { f, seen } = door({ "/households/door": [200, state] });
      const { d, run } = deps({ search: "", path: "/try", fetch: f, household: tokens(null), golden: async () => true });
      const app = new App(d);
      await app.boot();
      for (let i = 0; i < 4; i++) await tick();
      run(0);
      return { app, seen };
    };
    const shut = await at({ state: "shut", reopens_at: null });
    expect(shut.app.els.firstRun.hidden).toBe(false);
    expect(shut.app.els.consent.hidden).toBe(true);
    expect(shut.app.els.firstRunGo.hidden).toBe(true);
    expect(shut.app.els.joinError.textContent).toBe(JOIN_TEXT.door_closed);
    expect(shut.app.els.doorWatch.hidden).toBe(false);
    expect(shut.seen.some((x) => x.url === "/api/v1/households")).toBe(false);

    const capped = await at({ state: "capped", reopens_at: null });
    expect(capped.app.els.consent.hidden).toBe(true);
    expect(capped.app.els.joinError.textContent).toContain("No time is known yet");
    expect(capped.app.els.doorWatch.hidden).toBe(false);

    const open = await at({ state: "open", reopens_at: null });
    expect(open.app.els.consent.hidden).toBe(false);
    expect(open.app.els.firstRunGo.hidden).toBe(false);
    expect(open.app.els.joinError.hidden).toBe(true);
  });

  it("with no door in the address, a phone without a household shows no door", async () => {
    const { d, run } = deps({ search: "", path: "/", household: tokens(null), firstRunSeen: () => true });
    const app = new App(d);
    await app.boot();
    run(0);
    expect(app.els.firstRun.hidden).toBe(true);
  });

  it("a shut door says it is by invitation; a global cap says when it opens; both offer the recorded run", async () => {
    const shut = door({ "/households": [403, { error: "door_closed" }] });
    const a = deps({ search: "?start", fetch: shut.f, household: tokens(null), golden: async () => true });
    const app = new App(a.d);
    await app.boot();
    await consentAndGo(app, a.run);
    a.run(0);
    expect(app.els.joinError.textContent).toBe(JOIN_TEXT.door_closed);
    expect(app.els.firstRun.hidden).toBe(false);
    expect(app.els.doorWatch.hidden).toBe(false);

    const full = door({ "/households": [429, { error: "global_cap", reopens_at: null }] });
    const b = deps({ search: "?start", fetch: full.f, household: tokens(null), golden: async () => true });
    const app2 = new App(b.d);
    await app2.boot();
    await consentAndGo(app2, b.run);
    b.run(0);
    // No home yet, so nothing of its own is promised.
    expect(app2.els.joinError.textContent).toBe(
      "Fix-It Lens has reached today's limit for everyone. Guided help opens again later. You can still watch the demo replay.",
    );
    expect(app2.els.doorWatch.hidden).toBe(false);
  });

  it("a Dutch phone reads the door's no in Dutch, and drops the replay it can't offer", async () => {
    const shut = door({ "/households": [403, { error: "door_closed" }] });
    const a = deps({ search: "?start", lang: "nl-NL", fetch: shut.f, household: tokens(null), golden: async () => false });
    const app = new App(a.d);
    await app.boot();
    await consentAndGo(app, a.run);
    a.run(0);
    expect(app.els.joinError.textContent).toBe("Fix-It Lens is voorlopig alleen op uitnodiging open.");

    const full = door({ "/households": [429, { error: "global_cap", reopens_at: null }] });
    const b = deps({ search: "?start", lang: "nl-NL", fetch: full.f, household: tokens(null), golden: async () => true });
    const app2 = new App(b.d);
    await app2.boot();
    await consentAndGo(app2, b.run);
    b.run(0);
    expect(app2.els.joinError.textContent).toBe(
      "Fix-It Lens heeft de limiet van vandaag voor iedereen bereikt. Begeleide hulp gaat later weer open. Je kunt nog wel de demo-opname bekijken.",
    );

    const used = door({ "/households": [403, { error: "invite_used" }] });
    const c = deps({ search: "?invite=X", lang: "nl", fetch: used.f, household: tokens(null) });
    const app3 = new App(c.d);
    await app3.boot();
    await consentAndGo(app3, c.run);
    c.run(0);
    expect(app3.els.joinError.textContent).toBe("Die uitnodiging is al gebruikt, of klopt niet. Vraag om een nieuwe link.");
  });

  it("with no recorded run on the server, a shut door doesn't promise one", async () => {
    const shut = door({ "/households": [403, { error: "door_closed" }] });
    const a = deps({ search: "?start", fetch: shut.f, household: tokens(null), golden: async () => false });
    const app = new App(a.d);
    await app.boot();
    await consentAndGo(app, a.run);
    a.run(0);
    expect(app.els.joinError.textContent).toBe("Fix-It Lens is open by invitation only for now.");
    expect(app.els.doorWatch.hidden).toBe(true);
  });

  it("a mistyped or used door offers no replay in its place", async () => {
    const gone = door({ "/households/try": [404, { error: "not_found" }] });
    const a = deps({ search: "", path: "/try", hash: "#k=abc", fetch: gone.f, household: tokens(null), golden: async () => true, openSocket: () => new Sock() });
    const app = new App(a.d);
    await app.boot();
    await consentAndGo(app, a.run);
    a.run(0);
    expect(app.els.joinError.textContent).toBe(JOIN_TEXT.tryGone);
    expect(app.els.doorWatch.hidden).toBe(true);
  });

  it("a judges' link reads its code from #k=, takes it out of the address, and posts it to /households/try", async () => {
    const { f, seen } = door({ "/households/try": [201, { id: "h2", token: "tok-try" }] });
    const store = tokens(null);
    let cleared = 0;
    const { d, run } = deps({ search: "", path: "/try", hash: "#k=JUDGE-code_1", fetch: f, household: store, clearDoor: () => void cleared++, openSocket: () => new Sock() });
    const app = new App(d);
    await app.boot();
    expect(cleared).toBe(1);
    await consentAndGo(app, run);
    run(0);
    expect(seen.find((x) => x.url.endsWith("/households/try"))?.body).toEqual({ code: "JUDGE-code_1", consent: true, age_ok: true, lang: "en" });
    expect(store.now()).toBe("tok-try");

    const gone = door({ "/households/try": [404, {}] });
    const b = deps({ search: "", path: "/try", hash: "#k=OLD", fetch: gone.f, household: tokens(null) });
    const app2 = new App(b.d);
    await app2.boot();
    await consentAndGo(app2, b.run);
    b.run(0);
    expect(app2.els.joinError.textContent).toBe(JOIN_TEXT.tryGone);
  });

  it("a return link signs this phone in to the same household with no consent asked again; a used one says how to get another", async () => {
    const { f, seen } = door({ "/households/return": [201, { id: "h1", token: "tok-back" }] });
    const store = tokens(null);
    const { d, run } = deps({ search: "", hash: "#return/abc123", fetch: f, household: store, firstRunSeen: () => false });
    const app = new App(d);
    await app.boot();
    for (let i = 0; i < 4; i++) await tick();
    run(0);
    expect(seen.find((x) => x.url.endsWith("/households/return"))?.body).toEqual({ code: "abc123" });
    expect(store.now()).toBe("tok-back");
    expect(app.els.firstRun.hidden).toBe(true);

    const used = door({ "/households/return": [404, {}] });
    const b = deps({ search: "", hash: "#return/abc123", fetch: used.f, household: tokens(null) });
    const app2 = new App(b.d);
    await app2.boot();
    for (let i = 0; i < 4; i++) await tick();
    b.run(0);
    expect(app2.els.joinError.textContent).toBe(JOIN_TEXT.gone);
  });

  it("a return link is followed even with a dead token stored: it replaces the token, and the 401s don't cover it", async () => {
    const back = door({ "/households/return": [201, { id: "h1", token: "tok-back" }] });
    const seen = back.seen;
    // The old token is refused everywhere; the one the link gives works.
    const f = (async (u: string, init?: RequestInit) =>
      new Headers(init?.headers).get("authorization") === "Bearer tok-old" ? (seen.push({ url: String(u), auth: "Bearer tok-old", body: null }), new Response("{}", { status: 401 })) : back.f(u, init)) as typeof fetch;
    const store = tokens("tok-old");
    let cleared = 0;
    const { d, run } = deps({ search: "", hash: "#return/abc123", fetch: f, household: store, clearDoor: () => void cleared++, firstRunSeen: () => false });
    const app = new App(d);
    await app.boot();
    for (let i = 0; i < 6; i++) await tick();
    run(0);
    expect(cleared).toBe(1);
    expect(seen.find((x) => x.url.endsWith("/households/return"))?.body).toEqual({ code: "abc123" });
    expect(store.now()).toBe("tok-back");
    expect(app.els.firstRun.hidden).toBe(true);
    expect(app.els.joinError.textContent).not.toBe(JOIN_TEXT.lost);
  });

  it("a used return link on a phone whose home still works keeps that home", async () => {
    const { f } = door({ "/households/return": [404, {}] });
    const store = tokens("tok");
    const { d, run } = deps({ search: "", hash: "#return/abc123", fetch: f, household: store });
    const app = new App(d);
    await app.boot();
    for (let i = 0; i < 4; i++) await tick();
    run(0);
    expect(store.now()).toBe("tok");
    expect(app.els.firstRun.hidden).toBe(true);
  });

  it("About makes a single-use link to open this household on another phone, and copies it", async () => {
    const { f, seen } = door({ "/household/return-link": [201, { url: "https://fil.example/#return/xyz", expires_at: "2026-10-27T15:15:00Z" }] });
    const copied: string[] = [];
    const { d, run } = deps({ search: "", fetch: f, household: tokens("tok"), copy: async (t) => void copied.push(t) });
    const app = new App(d);
    await app.boot();
    run(0);
    const box = app.els.root.querySelector("#another") as HTMLElement;
    expect(box.hidden).toBe(false);
    (box.querySelector('[data-act="another-make"]') as HTMLButtonElement).click();
    for (let i = 0; i < 4; i++) await tick();
    run(16);
    expect(seen.at(-1)).toMatchObject({ url: "/api/v1/household/return-link", auth: "Bearer tok" });
    expect(box.querySelector("#another-url")?.textContent).toBe("https://fil.example/#return/xyz");
    expect(box.querySelector("#another-note")?.textContent).toMatch(/^Works once, until \d\d:\d\d\.$/);
    (box.querySelector('[data-act="another-copy"]') as HTMLButtonElement).click();
    for (let i = 0; i < 2; i++) await tick();
    run(32);
    expect(copied).toEqual(["https://fil.example/#return/xyz"]);
    expect(box.querySelector("#another-note")?.textContent).toBe("Copied.");
  });

  it("a capped session (close 4429) says so kindly in the session and on home", async () => {
    const sock = new Sock();
    const { d, run } = deps({ search: "", household: tokens("tok"), openSocket: () => sock });
    const app = new App(d);
    await app.boot();
    await app.startLive();
    sock.onopen?.({});
    sock.onclose?.({ code: 4429, reason: JSON.stringify({ error: "home_cap", reopens_at: null }) });
    run(0);
    const text = "Your home has used today's guided help. It opens again tomorrow. Your reminders, your appliances and the demo replay still work.";
    expect(app.els.notice.textContent).toBe(text);
    expect((app.els.root.querySelector("#cap-line") as HTMLElement).textContent).toBe(text);
  });

  it("a deleted household (close 4410) is final: no reconnect, the token forgotten, back to the start saying why", async () => {
    let opened = 0;
    const sock = new Sock();
    const store = tokens("tok");
    const went: string[] = [];
    const { d, run } = deps({ search: "", household: store, openSocket: () => (opened++, sock), navigate: (x) => void went.push(x) });
    const app = new App(d);
    await app.boot();
    await app.startLive();
    sock.onopen?.({});
    sock.onclose?.({ code: 4410, reason: JSON.stringify({ error: "household_deleted" }) });
    run(60_000);
    expect(opened).toBe(1);
    expect(store.now()).toBeNull();
    expect(went).toEqual(["?start&deleted"]);

    const back = deps({ search: "?start&deleted", household: tokens(null) });
    const app2 = new App(back.d);
    await app2.boot();
    back.run(0);
    expect(app2.els.firstRun.hidden).toBe(false);
    expect(app2.els.joinError.textContent).toBe(JOIN_TEXT.deleted);
  });

  it("Delete my household asks twice, sends DELETE with the bearer, and forgets the token", async () => {
    const calls: { url: string; method?: string; auth: string | null }[] = [];
    const f = (async (u: string, init?: RequestInit) => {
      calls.push({ url: String(u), method: init?.method, auth: new Headers(init?.headers).get("authorization") });
      return new Response(null, { status: 204 });
    }) as typeof fetch;
    const store = tokens("tok-9");
    const { d, run } = deps({ search: "", fetch: f, household: store, disclaimers: { version: "t", first_run: { en: "FIRST" }, spoken: {}, step_citation: {}, terms: {} } });
    const app = new App(d);
    await app.boot();
    run(0);
    expect(app.els.deleteHh.textContent).toBe("Delete my household");
    calls.length = 0; // the home screen's own fetches at boot
    app.els.deleteHh.click();
    run(16);
    expect(calls).toEqual([]);
    expect(app.els.deleteHh.textContent).toBe("Tap again to delete everything");
    app.els.deleteHh.click();
    await new Promise((r) => setTimeout(r, 0));
    run(32);
    expect(calls).toEqual([{ url: "/api/v1/household", method: "DELETE", auth: "Bearer tok-9" }]);
    expect(store.now()).toBeNull();
    expect(app.els.deleteHh.hidden).toBe(true);
    expect(app.els.firstRun.hidden).toBe(false);
    expect(app.els.joinError.textContent).toContain("deleted");
  });
});

describe("adding an appliance (contracts 1.6.0)", () => {
  const AP = { id: "a1", alias: "washer", kind: "washer", brand: "acme", model: "WM-0000", plate_confirmed: true };
  function api(replies: Record<string, () => Response>) {
    const seen: { url: string; method: string; body: unknown; auth: string | null }[] = [];
    const f = (async (u: string, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${u}`;
      // The home screen's list, due and people fetches at boot are not part of the add flow.
      if (key === "GET /api/v1/appliances" || key.startsWith("GET /api/v1/due")) return new Response("[]", { status: 200 });
      if (key === "GET /api/v1/household/people") return new Response("{}", { status: 404 });
      seen.push({ url: String(u), method: init?.method ?? "GET", body: init?.body ? JSON.parse(String(init.body)) : null, auth: new Headers(init?.headers).get("authorization") });
      return (replies[key] ?? (() => new Response("{}", { status: 404 })))();
    }) as typeof fetch;
    return { f, seen };
  }
  const json = (b: unknown, status = 200) => () => new Response(JSON.stringify(b), { status });
  const tick = () => new Promise((r) => setTimeout(r, 0));

  it("a settled plate read offers Add; it posts the photo id, polls every 2 s while visible, and a job button starts it", async () => {
    let ingest = { ...AP, card_status: "pending", ingest: { stage: "lookup", notice: null } };
    const { f, seen } = api({
      "POST /api/v1/appliances": json(ingest, 201),
      "GET /api/v1/appliances/a1": () => new Response(JSON.stringify(ingest), { status: 200 }),
      "GET /api/v1/appliances/a1/card": json({ jobs: [{ id: "clean_filters", title: { en: "Clean the filters", nl: "Zeven reinigen" } }] }),
    });
    let now = 0;
    const went: string[] = [];
    const { d, run } = deps({ search: "", fetch: f, household: tokens("tok"), now: () => now, navigate: (s) => void went.push(s) });
    const app = new App(d);
    await app.boot();
    app.onMessage({ type: "plate_read", seq: 1, t_ms: 1, photo_id: "p1", brand: "acme", model: "WM-0000", fields: [{ name: "model", value: "WM-0000", uncertain: [] }] });
    run(0);
    expect(app.els.plate.hidden).toBe(true);
    expect(app.els.add.hidden).toBe(false);
    expect(app.els.addLabel.textContent).toBe("acme WM-0000");
    expect(app.els.addModelRow.hidden).toBe(true);
    app.els.addName.value = "washer";
    app.els.addGo.click();
    await tick();
    run(16);
    expect(seen[0]).toMatchObject({ method: "POST", url: "/api/v1/appliances", body: { alias: "washer", plate_photo_id: "p1" }, auth: "Bearer tok" });
    expect(app.els.addStages.querySelector(".now")?.textContent).toBe("Finding the manual…");
    // Not before 2 s; not while the page is hidden.
    now = 1000;
    run(32);
    expect(seen.length).toBe(1);
    now = 2500;
    const vis = vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    run(48);
    expect(seen.length).toBe(1);
    vis.mockReturnValue("visible");
    ingest = { ...AP, card_status: "checked" } as typeof ingest;
    run(64);
    await tick();
    run(80);
    expect(seen[1]).toMatchObject({ method: "GET", url: "/api/v1/appliances/a1" });
    expect(app.els.addStatus.textContent).toBe("Ready: washer.");
    // Final: no more polling; the checked card's jobs are fetched once and each starts the session on that job.
    now = 9000;
    run(96);
    await tick();
    run(112);
    expect(seen.length).toBe(3);
    expect(seen[2]).toMatchObject({ method: "GET", url: "/api/v1/appliances/a1/card" });
    expect(app.els.addStart.hidden).toBe(true);
    const job = app.els.addJobs.querySelector<HTMLElement>("button")!;
    expect(job.textContent).toBe("Clean the filters");
    job.click();
    expect(went).toEqual(["?appliance=a1&job=clean_filters"]);
    vis.mockRestore();
  });

  it("a failed plate read (no fields, no model) falls back to typing the model", async () => {
    const { d, run } = deps({ search: "" });
    const app = new App(d);
    await app.boot();
    app.onMessage({ type: "plate_read", seq: 1, t_ms: 1, photo_id: "p2", brand: null, model: null, fields: [] });
    run(0);
    expect(app.els.add.hidden).toBe(false);
    expect(app.els.addModelRow.hidden).toBe(false);
    expect(app.l.add!.photoId).toBeNull();
  });

  it("a typed model posts {model}; a taken name says so in the phone's words", async () => {
    const { f, seen } = api({ "POST /api/v1/appliances": json({ error: "alias_taken", detail: "x" }, 409) });
    const { d, run } = deps({ search: "", fetch: f, household: tokens("tok") });
    const app = new App(d);
    await app.boot();
    (app.els.controls.querySelector('[data-act="type-model"]') as HTMLButtonElement).click();
    run(0);
    expect(app.els.addModelRow.hidden).toBe(false);
    expect(app.els.typeModel.hidden).toBe(true);
    app.els.addModel.value = " WM-1234 ";
    app.els.addGo.click();
    await tick();
    run(16);
    expect(seen[0]!.body).toEqual({ model: "WM-1234" });
    expect(app.els.addNotice.textContent).toBe("That name is already used. Pick another.");
    // The same button now retries, and says so.
    expect(app.els.addGo.textContent).toBe("Try again");
  });

  it("the keyboard's Go in the model field adds the appliance, as the Add button does", async () => {
    const { f, seen } = api({ "POST /api/v1/appliances": json({ error: "alias_taken", detail: "x" }, 409) });
    const { d, run } = deps({ search: "", fetch: f, household: tokens("tok") });
    const app = new App(d);
    await app.boot();
    (app.els.controls.querySelector('[data-act="type-model"]') as HTMLButtonElement).click();
    run(0);
    expect(app.els.addModel.getAttribute("enterkeyhint")).toBe("go");
    app.els.addModel.value = "WM-1234";
    app.els.addModel.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await tick();
    run(16);
    expect(seen[0]!.body).toEqual({ model: "WM-1234" });
  });

  it("a lookup miss shows the fixed notice, links the maker's page only over https, and re-posts with the manual link", async () => {
    const miss = {
      ...AP,
      card_status: "failed",
      ingest: { stage: "lookup", notice: { kind: "lookup_failed", maker_page: "https://acme.example/manuals", text: { en: "No manual found.", nl: "Geen handleiding gevonden." } } },
    };
    const { f, seen } = api({ "POST /api/v1/appliances": json(miss, 201) });
    const { d, run } = deps({ search: "", fetch: f, lang: "nl", household: tokens("tok") });
    const app = new App(d);
    await app.boot();
    (app.els.controls.querySelector('[data-act="type-model"]') as HTMLButtonElement).click();
    app.els.addModel.value = "WM-1";
    app.els.addGo.click();
    await tick();
    run(0);
    expect(app.els.addNotice.textContent).toBe("Geen handleiding gevonden.");
    expect(app.els.addMaker.hidden).toBe(false);
    expect(app.els.addMaker.getAttribute("href")).toBe("https://acme.example/manuals");
    expect(app.els.addStart.hidden).toBe(true);
    expect(app.els.addPdfRow.hidden).toBe(false);
    app.els.addPdf.value = "https://acme.example/m/wm-1.pdf";
    (app.els.addPdfRow.querySelector('[data-act="add-pdf"]') as HTMLButtonElement).click();
    await tick();
    expect(seen[1]!.body).toEqual({ alias: "washer", model: "WM-0000", pdf_url: "https://acme.example/m/wm-1.pdf" });

    // A non-https maker page is never made a link.
    app.l.add!.appliance = { ...miss, ingest: { ...miss.ingest, notice: { ...miss.ingest.notice, maker_page: "javascript:alert(1)" } } } as never;
    run(16);
    expect(app.els.addMaker.hidden).toBe(true);
  });
});

describe("the manual's PDF and the wait (contracts 1.15)", () => {
  const AP = { id: "a1", alias: "washer", kind: "washer", brand: "acme", model: "WM-0000", plate_confirmed: true };
  const MISS = {
    ...AP,
    card_status: "failed",
    ingest: {
      stage: "lookup",
      notice: { kind: "lookup_failed", reason: "no_manual", maker_page: "https://acme.example/manuals", text: { en: "No manual found." } },
    },
  };
  const tick = () => new Promise((r) => setTimeout(r, 0));
  const pdf = (head = "%PDF-1.7\n", size?: number) => {
    const f = new File([head], "manual.pdf", { type: "application/pdf" });
    if (size !== undefined) Object.defineProperty(f, "size", { value: size });
    return f;
  };
  async function missed(extra: Partial<Deps> = {}) {
    const fetched: { url: string; auth: string | null }[] = [];
    const f = (async (u: string, init?: RequestInit) => {
      fetched.push({ url: String(u), auth: new Headers(init?.headers).get("authorization") });
      if (String(u).endsWith(".png")) return new Response(new Blob(["png"]), { status: 200 });
      return new Response("[]", { status: 200 });
    }) as typeof fetch;
    const t = deps({ search: "", fetch: f, household: tokens("tok"), ...extra });
    const app = new App(t.d);
    await app.boot();
    app.l.add = { photoId: null, label: null, appliance: MISS as never, error: null, busy: false, nextPollAt: 0, jobs: null, upload: null, thumb: null };
    t.run(0);
    return { app, run: t.run, fetched };
  }
  async function pick(app: App, file: File) {
    Object.defineProperty(app.els.addFile, "files", { value: [file], configurable: true });
    app.els.addFile.dispatchEvent(new Event("change", { bubbles: true }));
    for (let i = 0; i < 5; i++) await tick(); // the first bytes are read asynchronously
  }

  it("a lookup miss puts the Manual link field right under the notice, then Add the PDF and the maker's page", async () => {
    const { app, run } = await missed();
    expect(app.els.addNotice.textContent).toBe("No manual found.");
    expect(app.els.addNotice.nextElementSibling).toBe(app.els.addPdfRow);
    expect(app.els.addPdfRow.nextElementSibling).toBe(app.els.addManualRow);
    expect(app.els.addPdfRow.hidden).toBe(false);
    expect(app.els.addManualRow.hidden).toBe(false);
    expect(app.els.addManualRow.textContent).toContain("Add the PDF");
    expect(app.els.addMaker.hidden).toBe(false);
    expect(app.els.addFile.accept).toContain("application/pdf");
    // A notice that a new manual can't fix (today's budget is used up) offers neither.
    app.l.add!.appliance = { ...MISS, ingest: { stage: "ingest", notice: { kind: "ingest_failed", reason: "cap_reached", text: { en: "Budget used." } } } } as never;
    run(16);
    expect(app.els.addPdfRow.hidden).toBe(true);
    expect(app.els.addManualRow.hidden).toBe(true);
  });

  it("a file over 50 MB, or one that isn't a PDF, is refused on the phone and nothing is sent", async () => {
    const sent: string[] = [];
    const { app, run } = await missed({ upload: async (u) => (sent.push(u), new Response("{}", { status: 201 })) });
    await pick(app, pdf("%PDF-1.7", 52_428_801));
    run(16);
    expect(app.els.addNotice.textContent).toBe(PDF_TEXT.too_large);
    await pick(app, pdf("<html>"));
    run(32);
    expect(app.els.addNotice.textContent).toBe(PDF_TEXT.not_a_pdf);
    expect(sent).toEqual([]);
    // The way out stays on screen.
    expect(app.els.addPdfRow.hidden).toBe(false);
    expect(app.els.addManualRow.hidden).toBe(false);
  });

  it("a PDF goes raw with the bearer, its progress on screen; the 201 restarts the wait, which shows what the server knows", async () => {
    let done!: (r: Response) => void;
    let progress!: (f: number) => void;
    const calls: { url: string; headers: Record<string, string>; body: Blob }[] = [];
    const made: string[] = [];
    const dropped: string[] = [];
    const { app, run, fetched } = await missed({
      upload: (url, body, headers, sent) => {
        calls.push({ url, headers, body });
        progress = sent;
        return new Promise((r) => (done = r));
      },
      objectUrl: { make: () => (made.push("blob:1"), "blob:1"), drop: (u) => void dropped.push(u) },
    });
    const file = pdf();
    await pick(app, file);
    expect(calls).toHaveLength(1);
    expect(calls[0]!.url).toBe("/api/v1/appliances/a1/manual");
    expect(calls[0]!.headers).toEqual({ "content-type": "application/pdf", authorization: "Bearer tok" });
    expect(calls[0]!.body).toBe(file);
    progress(0.4);
    run(16);
    expect(app.els.addStatus.textContent).toBe("Sending the PDF…");
    expect(app.els.addMeter.hidden).toBe(false);
    expect(app.els.addMeter.getAttribute("aria-valuenow")).toBe("40");
    expect(app.els.addEta.textContent).toBe("40%");
    // While it goes, the miss's notice and its controls step aside.
    expect(app.els.addNotice.textContent).toBe("");
    expect(app.els.addManualRow.hidden).toBe(true);

    done(
      new Response(
        JSON.stringify({
          ...AP,
          card_status: "pending",
          ingest: {
            stage: "ingest",
            progress: 0.55,
            eta_s: 140,
            manual: { pages: 48, lang: "de", user_lang: false, title: "Gebrauchsanleitung WM-0000", thumb_url: "/api/v1/appliances/a1/manual/pages/1.png" },
            quotes: { checked: 12, passed: 11 },
            jobs_ready: [
              { id: "clean_filter", title: { en: "Clean the filter", nl: "Filter reinigen" }, steps: 4, page: 31, duration_min: 15, kind: "upkeep", startable: false },
              { id: "door_seal", title: { nl: "Deurrubber reinigen" }, steps: 1, page: 33, duration_min: null, kind: "upkeep", startable: false },
            ],
            jobs_refused: [{ id: "x1", reason: "mains" }, { id: "x2", reason: "mains" }, { id: "x3", reason: "gas" }],
            notice: null,
          },
        }),
        { status: 201 },
      ),
    );
    await tick();
    run(32);
    await tick();
    run(48);
    expect(app.els.addStages.querySelector(".now")?.textContent).toBe("Reading the manual…");
    expect(app.els.addMeter.getAttribute("aria-valuenow")).toBe("55");
    expect(app.els.addEta.textContent).toBe("About 2 min left");
    expect(app.els.addTitle.textContent).toBe("Gebrauchsanleitung WM-0000");
    expect(app.els.addPages.textContent).toBe("Manual found: 48 pages, in German.");
    expect(app.els.addQuotes.textContent).toBe("Quotes checked against the pages: 11 of 12 found.");
    expect(app.els.addReadyHead.hidden).toBe(false);
    const ready = [...app.els.addReady.querySelectorAll("li")].map((li) => li.textContent);
    expect(ready).toEqual(["Clean the filter4 steps · page 31 · about 15 min", "Deurrubber reinigen1 step · page 33"]);
    // Not startable yet: plain rows, no buttons.
    expect(app.els.addReady.querySelector("button")).toBeNull();
    expect([...app.els.addRefused.querySelectorAll("li")].map((li) => li.textContent)).toEqual([
      "2 jobs left out: it means opening the mains wiring.",
      "1 job left out: it works on the gas supply.",
    ]);
    // The thumbnail is fetched with the bearer, once, and freed on close.
    expect(fetched.filter((f) => f.url.endsWith(".png"))).toEqual([{ url: "/api/v1/appliances/a1/manual/pages/1.png", auth: "Bearer tok" }]);
    expect(app.els.addThumb.hidden).toBe(false);
    expect(app.els.addThumb.getAttribute("src")).toBe("blob:1");
    expect(app.els.addManualRow.hidden).toBe(true);
    (app.els.add.querySelector('[data-act="add-close"]') as HTMLButtonElement).click();
    expect(dropped).toEqual(["blob:1"]);
  });

  it("a shared PDF waits on home for its appliance, then goes to the one picked; Not now drops it", async () => {
    const calls: string[] = [];
    const f = (async (u: string) => {
      if (String(u) === "/api/v1/appliances") return new Response(JSON.stringify([{ ...AP, card_status: "checked" }]), { status: 200 });
      return new Response("[]", { status: 200 });
    }) as typeof fetch;
    const shared = new File(["%PDF-1.7"], "WM-0000 manual.pdf", { type: "application/pdf" });
    const t = deps({
      search: "?share=pdf",
      fetch: f,
      household: tokens("tok"),
      sharedPdf: async () => shared,
      upload: async (url, body) => (calls.push(`${url} ${(body as File).name}`), new Response(JSON.stringify({ ...AP, card_status: "pending", ingest: { stage: "ingest" } }), { status: 201 })),
    });
    const app = new App(t.d);
    await app.boot();
    await tick();
    t.run(0);
    expect(app.els.shared.hidden).toBe(false);
    expect(app.els.sharedName.textContent).toBe("WM-0000 manual.pdf");
    expect(app.els.sharedAsk.textContent).toBe("Which appliance is this manual for?");
    const pickBtn = app.els.sharedList.querySelector<HTMLButtonElement>('[data-act="shared-pick"]')!;
    expect(pickBtn.textContent).toBe("Washer");
    expect(calls).toEqual([]); // nothing goes until a pick
    pickBtn.click();
    for (let i = 0; i < 5; i++) await tick();
    t.run(16);
    expect(calls).toEqual(["/api/v1/appliances/a1/manual WM-0000 manual.pdf"]);
    expect(app.els.shared.hidden).toBe(true);
    expect(app.els.home.hidden).toBe(true);
    expect(app.els.add.hidden).toBe(false);
    expect(app.els.addHead.textContent).toBe("The washer's manual");

    const t2 = deps({ search: "?share=pdf", fetch: f, household: tokens("tok"), sharedPdf: async () => shared });
    const app2 = new App(t2.d);
    await app2.boot();
    await tick();
    t2.run(0);
    (app2.els.shared.querySelector('[data-act="shared-drop"]') as HTMLButtonElement).click();
    t2.run(16);
    expect(app2.els.shared.hidden).toBe(true);
  });

  it("a shared PDF for a new appliance goes as soon as the appliance exists", async () => {
    const calls: string[] = [];
    const f = (async (u: string, init?: RequestInit) => {
      if (init?.method === "POST" && String(u) === "/api/v1/appliances") return new Response(JSON.stringify(MISS), { status: 201 });
      return new Response("[]", { status: 200 });
    }) as typeof fetch;
    const t = deps({
      search: "?share=pdf",
      fetch: f,
      household: tokens("tok"),
      sharedPdf: async () => new File(["%PDF-1.7"], "m.pdf"),
      upload: async (url) => (calls.push(url), new Response(JSON.stringify({ ...AP, card_status: "pending", ingest: { stage: "ingest" } }), { status: 201 })),
    });
    const app = new App(t.d);
    await app.boot();
    await tick();
    t.run(0);
    expect(app.els.sharedAsk.textContent).toBe("Add the appliance it is for, and I'll read it.");
    (app.els.shared.querySelector('[data-act="shared-new"]') as HTMLButtonElement).click();
    t.run(16);
    expect(app.els.addModelRow.hidden).toBe(false);
    // Closing before it exists goes back to the choice, the file still held.
    (app.els.add.querySelector('[data-act="add-close"]') as HTMLButtonElement).click();
    t.run(32);
    expect(app.els.shared.hidden).toBe(false);
    (app.els.shared.querySelector('[data-act="shared-new"]') as HTMLButtonElement).click();
    app.els.addModel.value = "WM-0000";
    app.els.addGo.click();
    for (let i = 0; i < 6; i++) await tick();
    expect(calls).toEqual(["/api/v1/appliances/a1/manual"]);
  });

  it("the server's 413 and 415 come back in the phone's words, with the way out still there", async () => {
    let status = 413;
    const { app, run } = await missed({ upload: async () => new Response(JSON.stringify({ error: status === 413 ? "too_large" : "not_a_pdf", detail: "x" }), { status }) });
    await pick(app, pdf());
    run(16);
    expect(app.els.addNotice.textContent).toBe(PDF_TEXT.too_large);
    status = 415;
    await pick(app, pdf());
    run(32);
    expect(app.els.addNotice.textContent).toBe(PDF_TEXT.not_a_pdf);
    expect(app.els.addManualRow.hidden).toBe(false);
  });
});

describe("a refused hello (WS close 1008)", () => {
  it("shows the server's notice when it sent one, and its own line when not; a gone token goes back to join", async () => {
    // With a notice: it stays, and no /household check is made.
    const sock = new Sock();
    const fetched: string[] = [];
    const f = (async (u: string) => {
      fetched.push(String(u));
      return new Response("{}", { status: 404 });
    }) as typeof fetch;
    const a = deps({ search: "", openSocket: () => sock, fetch: f });
    const app = new App(a.d);
    await app.boot();
    await app.startLive();
    sock.onopen?.({});
    sock.onmessage?.({ data: JSON.stringify({ type: "notice", seq: 0, t_ms: 0, kind: "no_rules_for_country", text: null }) });
    sock.onclose?.({ code: 1008 });
    await new Promise((r) => setTimeout(r, 0));
    a.run(0);
    expect(app.els.state.querySelector(".state-line")?.textContent).toBe("No safety rules for your country yet, so I won't guide repairs.");
    expect(fetched).toEqual([]);

    // A refused job (contracts 1.7.0): its own line, not the ingest one.
    const sj = new Sock();
    const j = deps({ search: "?appliance=a1&job=descale", openSocket: () => sj, fetch: f });
    const appJ = new App(j.d);
    await appJ.boot();
    await appJ.startLive();
    sj.onopen?.({});
    sj.onmessage?.({ data: JSON.stringify({ type: "notice", seq: 0, t_ms: 0, kind: "job_refused", text: null }) });
    sj.onclose?.({ code: 1008 });
    await new Promise((r) => setTimeout(r, 0));
    j.run(0);
    expect(appJ.els.state.querySelector(".state-line")?.textContent).toBe("The safety rules don't allow this job yet. The other jobs on this appliance are fine.");

    // Without one, and the token still good: the phone's own line.
    const s2 = new Sock();
    const b = deps({ search: "", openSocket: () => s2, fetch: f, household: tokens("tok") });
    const app2 = new App(b.d);
    await app2.boot();
    await app2.startLive();
    s2.onopen?.({});
    s2.onclose?.({ code: 1008 });
    await new Promise((r) => setTimeout(r, 0));
    b.run(0);
    expect(app2.els.notice.textContent).toContain("couldn't start");

    // Without one, and the token gone (401): back to the join screen.
    const s3 = new Sock();
    const store = tokens("gone");
    const c = deps({
      search: "",
      openSocket: () => s3,
      household: store,
      fetch: (async () => new Response("{}", { status: 401 })) as typeof fetch,
    });
    const app3 = new App(c.d);
    await app3.boot();
    await app3.startLive();
    s3.onopen?.({});
    s3.onclose?.({ code: 1008 });
    await new Promise((r) => setTimeout(r, 0));
    c.run(0);
    expect(store.now()).toBeNull();
    expect(app3.els.firstRun.hidden).toBe(false);
  });
});

describe("hold to talk (pointer events only)", () => {
  /** A pointer event as a phone's touch sends it, with no touch or mouse events at all. */
  function press(el: Element, type: "pointerdown" | "pointerup" | "pointercancel"): Event {
    const ev = new Event(type, { bubbles: true, cancelable: true });
    Object.assign(ev, { pointerId: 7, pointerType: "touch" });
    el.dispatchEvent(ev);
    return ev;
  }
  /** A recorder whose start waits until we answer the permission prompt. */
  function fakeRecorder(wav: string | null = "UklGRg==") {
    const log: string[] = [];
    let answer!: (ok: boolean) => void;
    const prompt = new Promise<boolean>((r) => (answer = r));
    const make = () => {
      log.push("made");
      return {
        start: async () => {
          log.push("start");
          if (!(await prompt)) throw Object.assign(new Error("denied"), { name: "NotAllowedError" });
          log.push("open");
        },
        stop: async () => {
          log.push("stop");
          return wav;
        },
      };
    };
    return { make, log, answer };
  }
  const tick = () => new Promise((r) => setTimeout(r, 0));

  it("hands-free: off until turned on; each heard turn is an utterance; nothing is heard while the phone speaks; it closes at a stop", async () => {
    let onTurn: ((wav: string) => void) | null = null;
    let quiet: (() => boolean) | null = null;
    let stops = 0;
    let speaking = false;
    const listener = () => ({
      start: async (f: (wav: string) => void, q: () => boolean) => {
        onTurn = f;
        quiet = q;
      },
      stop: () => void stops++,
    });
    const synth = { speak: () => true, cancel: () => undefined, speaking: () => speaking };
    const { app, sock, run } = await live(undefined, { listener, synth });
    run(0);
    expect(app.els.listen.hidden).toBe(false);
    expect(app.els.listen.textContent).toBe("Hands-free");
    expect(onTurn).toBeNull();
    app.els.listen.click();
    await tick();
    run(16);
    expect(app.els.listen.textContent).toBe("Listening — tap to stop");
    expect(app.els.listen.getAttribute("aria-pressed")).toBe("true");
    expect(app.els.talk.hidden).toBe(true);
    onTurn!("UklGRg==");
    expect(sock.sent.at(-1)).toMatchObject({ type: "utterance", text: null, audio_wav_b64: "UklGRg==", lang: "en" });
    expect(quiet!()).toBe(false);
    speaking = true;
    expect(quiet!()).toBe(true);
    app.onMessage({ type: "safety", seq: 1, t_ms: 1, state: { state: "STOP", rule_id: "nl.gas.smell", evidence: "e", script: "Stop. Get everyone outside." } } as never);
    run(32);
    expect(stops).toBe(1);
    expect(app.l.listening).toBe(false);
  });

  it("records from press to release and sends one utterance; the press claims the touch", async () => {
    const rec = fakeRecorder();
    const { app, sock, run } = await live(undefined, { recorder: rec.make });
    run(0);
    const down = press(app.els.talk, "pointerdown");
    // Made and started inside the press itself (iOS needs the audio context from the gesture).
    expect(rec.log).toEqual(["made", "start"]);
    expect(down.defaultPrevented).toBe(true);
    run(16);
    expect(app.els.talk.textContent).toContain("Listening 0:00");
    expect(app.els.talk.getAttribute("aria-pressed")).toBe("true");
    rec.answer(true);
    await tick();
    // The finger ends off the button: the release still counts.
    press(app.els.controls, "pointerup");
    await tick();
    run(32);
    expect(rec.log).toEqual(["made", "start", "open", "stop"]);
    expect(sock.sent.at(-1)).toMatchObject({ type: "utterance", audio_wav_b64: "UklGRg==", lang: "en" });
    expect(app.els.talk.textContent).toBe("Hold to talk");
  });

  it("a release while the permission prompt is up still stops the recorder; nothing leaks", async () => {
    const rec = fakeRecorder(null);
    const { app, sock, run } = await live(undefined, { recorder: rec.make });
    run(0);
    press(app.els.talk, "pointerdown");
    press(app.els.talk, "pointercancel"); // the prompt took the touch
    await tick();
    expect(rec.log).toEqual(["made", "start", "stop"]);
    rec.answer(true);
    await tick();
    expect(sock.sent.some((m) => m.type === "utterance")).toBe(false);
    // The next hold makes a fresh recorder.
    press(app.els.talk, "pointerdown");
    expect(rec.log.filter((x) => x === "made").length).toBe(2);
  });

  it("a blocked microphone says so instead of failing silently", async () => {
    const rec = fakeRecorder();
    const { app, run } = await live(undefined, { recorder: rec.make });
    run(0);
    press(app.els.talk, "pointerdown");
    rec.answer(false);
    await tick();
    run(16);
    expect(app.els.notice.textContent).toBe("The microphone is blocked. Allow it in the browser's site settings.");
    expect(app.els.talk.textContent).toBe("Hold to talk");
  });

  it("a long press opens no context menu on the button", async () => {
    const { app } = await live();
    const ev = new Event("contextmenu", { bubbles: true, cancelable: true });
    app.els.talk.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true);
  });
});

describe("the job sheet", () => {
  const FAR = new Date(Date.now() + 40 * 86_400_000).toISOString().slice(0, 10);
  const SNOOZE = new Date(Date.now() + 60 * 86_400_000).toISOString().slice(0, 10);
  const ST = { last_done: "2026-07-01", last_done_source: "user", schedule: null, snoozed_until: null };

  /** A household with one dishwasher job, answering the schedule routes as the server does. */
  function server() {
    const sent: { method: string; url: string; body: string | null; auth: string | null }[] = [];
    const replies: Record<string, number> = {};
    let due: Record<string, unknown> = {
      appliance_id: "a1",
      job_id: "clean_filters",
      title: "Clean the filters",
      due_on: FAR,
      reason: "scheduled",
      status: "upcoming",
      last_done: { date: "2026-07-01", source: "user" },
      manual: { every: 3, unit: "month", page: 43, quote: "Reinig de filters elke 3 maanden." },
      schedule: null,
      window_end: null,
      snoozed_until: null,
    };
    let changes: unknown[] = [
      { id: 1, at: "2026-10-20T09:00:00Z", kind: "snooze", by: { kind: "agent" }, before: ST, after: { ...ST, snoozed_until: "2026-11-01" }, undone: true },
      { id: 0, at: "2026-07-01T09:00:00Z", kind: "done", by: { kind: "ha" }, before: ST, after: ST },
    ];
    const f = (async (u: string, init?: RequestInit) => {
      const url = String(u);
      const method = init?.method ?? "GET";
      if (method !== "GET") sent.push({ method, url, body: init?.body ? String(init.body) : null, auth: new Headers(init?.headers).get("authorization") });
      const tail = url.split("/").pop()!;
      if (method !== "GET" && replies[tail]) return new Response("{}", { status: replies[tail] });
      if (url.endsWith("/changes")) return new Response(JSON.stringify(changes), { status: 200 });
      if (method !== "GET") {
        const body = init?.body ? JSON.parse(String(init.body)) : {};
        if (tail === "schedule") due = { ...due, schedule: body.kind === "manual" ? null : body };
        if (tail === "snooze") due = { ...due, snoozed_until: body.until, due_on: body.until ?? FAR };
        if (tail === "done") due = { ...due, last_done: { date: body.date ?? "today", source: "user" } };
        changes = [{ id: changes.length + 1, at: "2026-10-27T12:00:00Z", kind: tail === "undo" ? "undo" : tail, by: { kind: "app" }, before: ST, after: ST }, ...changes];
        return new Response(JSON.stringify(due), { status: 200 });
      }
      if (url.includes("/due")) return new Response(JSON.stringify([due]), { status: 200 });
      if (url.endsWith("/card"))
        return new Response(
          JSON.stringify({
            jobs: [{ id: "clean_filters", interval: { quote_ref: "q_interval" } }],
            quotes: [{ id: "q_interval", lang: "nl", en: "Clean the filters every 3 months." }],
          }),
          { status: 200 },
        );
      return new Response(JSON.stringify([{ id: "a1", alias: "dishwasher", kind: "dishwasher", brand: "bosch", model: "X", plate_confirmed: true, card_status: "checked" }]), { status: 200 });
    }) as typeof fetch;
    return { f, sent, replies, set: (over: Record<string, unknown>) => void (due = { ...due, ...over }) };
  }

  const tick = () => new Promise((r) => setTimeout(r, 0));
  async function settle(run: (t: number) => void, t = 0): Promise<void> {
    for (let i = 0; i < 4; i++) await tick();
    run(t);
  }

  async function open(over: Partial<Deps> = {}, due: Record<string, unknown> = {}) {
    const srv = server();
    srv.set(due);
    const pushed: string[] = [];
    const { d, run } = deps({ search: "", fetch: srv.f, household: tokens("tok"), pushHash: (h) => void pushed.push(h), ...over });
    const app = new App(d);
    await app.boot();
    await settle(run);
    return { app, run, srv, pushed };
  }
  const q = <T extends HTMLElement>(app: App, sel: string) => app.els.sheet.querySelector(sel) as T;
  const tap = (app: App, act: string) => q<HTMLButtonElement>(app, `[data-act="${act}"]`).click();
  const type = (input: HTMLInputElement, v: string) => {
    input.value = v;
    input.dispatchEvent(new Event("input", { bubbles: true }));
  };

  it("opens in one tap from the home list: the manual's interval with its page beside yours, and who changed what", async () => {
    const { app, run, pushed } = await open();
    (app.els.homeList.querySelector('[data-act="open-sheet"]') as HTMLButtonElement).click();
    await settle(run, 16);
    expect(pushed).toEqual(["job/a1/clean_filters"]);
    expect(app.els.home.hidden).toBe(true);
    expect(app.els.sheet.hidden).toBe(false);
    expect(q(app, "h1").textContent).toBe("Clean the filters");
    const far = fromNow(FAR, today());
    expect(q(app, ".when").textContent).toMatch(new RegExp(`^${far.charAt(0).toUpperCase()}${far.slice(1)} · \\d+ \\w{3}`));
    expect(q(app, ".when").classList.contains("due-now")).toBe(false);
    // The manual's line stays in view; the home's own schedule, who and the history fold under Job settings.
    expect([...app.els.sheet.querySelectorAll(".sched-line")].map((x) => x.textContent)).toEqual(["Every 3 monthsFrom the manual · p. 43"]);
    expect(q(app, '[data-act="sheet-more"]').getAttribute("aria-expanded")).toBe("false");
    expect(app.els.sheet.querySelector(".changes")).toBeNull();
    // Undo stays in view with the buttons that make a change.
    expect(q(app, '[data-act="sheet-undo"]').closest(".sheet-acts")).not.toBeNull();
    tap(app, "sheet-more");
    run(12);
    expect(q(app, '[data-act="sheet-more"]').getAttribute("aria-expanded")).toBe("true");
    expect([...app.els.sheet.querySelectorAll(".sched-line")].map((x) => x.textContent)).toEqual(["Every 3 monthsFrom the manual · p. 43", "Following the manual"]);
    // Yours only once the household has its own schedule.
    expect(app.els.sheet.querySelector(".job-settings .tag.yours")).toBeNull();
    // The citation verbatim in the manual's language, and the card's English line under it, labelled.
    expect(q(app, ".sched-quote p").textContent).toBe("“Reinig de filters elke 3 maanden.”");
    expect(q(app, ".sched-quote p").lang).toBe("nl");
    expect(q(app, ".sched-quote .translation").textContent).toBe("In EnglishClean the filters every 3 months.");
    const lines = [...app.els.sheet.querySelectorAll(".changes li")].map((x) => x.textContent);
    // An undone change is labelled so, not struck through.
    expect(lines).toEqual(["20 OctSnoozed until 1 Nov, by an agentundone", "1 JulMarked done on 1 Jul, from Home Assistant"]);
    // The snooze was undone already, so Undo offers the change under it.
    expect(q(app, '[data-act="sheet-undo"]').textContent).toBe("Undo: Marked done on 1 Jul");

    tap(app, "sheet-close");
    run(32);
    expect(pushed).toEqual(["job/a1/clean_filters", ""]);
    expect(app.els.sheet.hidden).toBe(true);
    expect(app.els.home.hidden).toBe(false);
  });

  it("home's next job offers Mark done and Later under Start, each on its sheet", async () => {
    const { app, run, srv, pushed } = await open({}, { due_on: today(), status: "due" });
    const acts = [...app.els.homeList.querySelectorAll<HTMLButtonElement>(".next-acts button")];
    expect(acts.map((b) => [b.dataset.act, b.textContent])).toEqual([["home-done", "Mark done"], ["home-later", "Later…"]]);
    acts[1]!.click();
    run(16);
    expect(pushed).toEqual(["job/a1/clean_filters"]);
    expect(q(app, '[data-act="sheet-later"]').getAttribute("aria-expanded")).toBe("true");
    expect(srv.sent).toEqual([]);
    tap(app, "sheet-close");
    run(32);
    (app.els.homeList.querySelector('[data-act="home-done"]') as HTMLButtonElement).click();
    await settle(run, 48);
    expect(app.els.sheet.hidden).toBe(false);
    expect(srv.sent.map((x) => [x.method, x.url, x.body])).toEqual([["POST", "/api/v1/appliances/a1/jobs/clean_filters/done", "{}"]]);
    expect(q(app, '[data-act="sheet-undo"]').closest(".sheet-acts")).not.toBeNull();
  });

  it("opens from its address (#job/<appliance>/<job>), and says so when the home has no such job", async () => {
    const { app, run } = await open({ hash: "#job/a1/clean_filters" });
    expect(app.els.sheet.hidden).toBe(false);
    expect(q(app, "h1").textContent).toBe("Clean the filters");
    // Back: the address no longer names the job.
    app.route("");
    run(16);
    expect(app.els.sheet.hidden).toBe(true);
    const other = await open({ hash: "#job/a1/nope" });
    expect(other.app.els.sheet.textContent).toContain("This job has no schedule in your home.");
  });

  it("leads with Start the job, the one primary, due or not", async () => {
    const { app, run, srv } = await open({ hash: "#job/a1/clean_filters" });
    const start = () => q<HTMLButtonElement>(app, '[data-act="start-job"]');
    const check = () => {
      expect(start().textContent).toBe("Start the job");
      expect(start().classList.contains("ok")).toBe(true);
      expect(start().closest(".sheet-acts")).not.toBeNull();
      expect(app.els.sheet.querySelectorAll("button.ok").length).toBe(1);
      expect(q(app, '[data-act="sheet-done-ask"]').textContent).toBe("Mark done");
    };
    check();
    // The server's next answer has it due (another phone changed its schedule).
    srv.set({ due_on: today(), status: "due" });
    tap(app, "sheet-undo");
    await settle(run, 16);
    check();
  });

  it("says what it is fetching while the job's dates load", async () => {
    const srv = { f: (() => new Promise<Response>(() => undefined)) as unknown as typeof fetch };
    const { d, run } = deps({ search: "", fetch: srv.f, household: tokens("tok"), hash: "#job/a1/clean_filters" });
    const app = new App(d);
    void app.boot();
    await settle(run);
    expect(app.els.sheet.hidden).toBe(false);
    expect(q(app, '[role="status"]').textContent).toBe("Fetching this job's dates and history…");
  });

  it("marks done today or on a date, and a future date is refused in words", async () => {
    const { app, run, srv } = await open({ hash: "#job/a1/clean_filters" });
    // Mark done asks which day: Today, Yesterday, or another day (picking it is the answer). No split button.
    expect(app.els.sheet.querySelector('[data-act="sheet-done"]')).toBeNull();
    expect(app.els.sheet.querySelector(".done-split")).toBeNull();
    tap(app, "sheet-done-ask");
    run(4);
    expect(q(app, '[data-act="sheet-done-ask"]').getAttribute("aria-expanded")).toBe("true");
    const yesterday = new Date(Date.parse(`${today()}T12:00:00Z`) - 86_400_000).toISOString().slice(0, 10);
    expect([...app.els.sheet.querySelectorAll<HTMLElement>(".done-when button")].map((b) => [b.dataset.act, b.textContent, b.dataset.date ?? null])).toEqual([
      ["sheet-done", "Today", null],
      ["sheet-done-date", "Yesterday", yesterday],
    ]);
    tap(app, "sheet-done");
    await settle(run, 16);
    // Done: the choices fold away.
    expect(app.els.sheet.querySelector(".done-when")).toBeNull();
    srv.replies.done = 422;
    tap(app, "sheet-done-ask");
    run(4);
    const pick = q<HTMLInputElement>(app, '[data-field="doneDate"]');
    pick.value = "2026-09-03";
    pick.dispatchEvent(new Event("change", { bubbles: true }));
    await settle(run, 32);
    expect(q(app, '[role="alert"]').textContent).toBe("That date is still to come. Pick today or earlier.");
    expect(q<HTMLInputElement>(app, '[data-field="doneDate"]').value).toBe("2026-09-03");
    expect(srv.sent.map((x) => [x.method, x.url, x.body, x.auth])).toEqual([
      ["POST", "/api/v1/appliances/a1/jobs/clean_filters/done", "{}", "Bearer tok"],
      ["POST", "/api/v1/appliances/a1/jobs/clean_filters/done", '{"date":"2026-09-03"}', "Bearer tok"],
    ]);
    // The first write's own change is in the history now, newest first.
    tap(app, "sheet-more");
    run(40);
    expect(q(app, ".changes li").textContent).toContain("in the app");
  });

  it("snoozes only to a date after the due date, shows it, and clears it", async () => {
    const { app, run, srv } = await open({ hash: "#job/a1/clean_filters" });
    const pick = () => q<HTMLInputElement>(app, '[data-field="snoozeDate"]');
    const after = new Date(Date.parse(`${FAR}T12:00:00Z`) + 86_400_000).toISOString().slice(0, 10);
    // Later… opens one row: two quick picks and Another day…, all after the due date. Picking a day is the snooze.
    const pickDay = (v: string) => {
      pick().value = v;
      pick().dispatchEvent(new Event("change", { bubbles: true }));
    };
    expect(pick()).toBeNull();
    tap(app, "sheet-later");
    run(4);
    expect(q(app, '[data-act="sheet-later"]').getAttribute("aria-expanded")).toBe("true");
    expect([...app.els.sheet.querySelectorAll<HTMLElement>('[data-act="sheet-snooze-to"]')].map((b) => [b.dataset.date, b.firstChild?.textContent])).toEqual([
      [after, "A day later"],
      [new Date(Date.parse(`${FAR}T12:00:00Z`) + 7 * 86_400_000).toISOString().slice(0, 10), "A week later"],
    ]);
    expect(pick().min).toBe(after);
    expect(pick().closest(".choices")?.textContent).toContain("Another day…");
    expect(app.els.sheet.querySelector('[data-act="sheet-snooze"]')).toBeNull();
    // Only typing a day doesn't snooze; the picker's own change does.
    type(pick(), SNOOZE);
    expect(srv.sent).toEqual([]);
    // The due date itself, typed past the picker's bound: refused here, naming the day the picker starts after.
    pickDay(FAR);
    run(8);
    expect(q(app, '[role="alert"]').textContent).toBe(`A snooze only moves it later. Pick a day after ${shortDate(FAR, today())}.`);
    expect(srv.sent).toEqual([]);
    pickDay(SNOOZE);
    await settle(run, 16);
    expect(q(app, ".sheet-acts").textContent).toContain("Snoozed until");
    tap(app, "sheet-unsnooze");
    await settle(run, 32);
    expect(srv.sent.map((x) => x.body)).toEqual([`{"until":"${SNOOZE}"}`, '{"until":null}']);
    expect(q(app, ".sheet-acts").textContent).not.toContain("Snoozed until");
    srv.replies.snooze = 422;
    pickDay(SNOOZE);
    await settle(run, 48);
    expect(q(app, '[role="alert"]').textContent).toMatch(/^A snooze only moves it later\. Pick a day after /);
    // A quick pick snoozes in one tap.
    srv.replies.snooze = 0;
    tap(app, "sheet-snooze-to");
    await settle(run, 64);
    expect(srv.sent.at(-1)?.body).toBe(`{"until":"${after}"}`);
  });

  it("sets the home's own window, keeps what is typed across redraws, and won't save a wrong one", async () => {
    const { app, run, srv } = await open({ hash: "#job/a1/clean_filters" });
    tap(app, "sheet-more");
    run(12);
    tap(app, "sched-edit");
    run(16);
    const window = q<HTMLInputElement>(app, '[data-field="kind"][value="window"]');
    window.click();
    run(32);
    type(q(app, '[data-field="min"]'), "6");
    type(q(app, '[data-field="max"]'), "3");
    run(48);
    expect(q(app, ".sched-edit .error").textContent).toBe("The first number must be the smaller one.");
    tap(app, "sched-save");
    expect(srv.sent).toEqual([]);
    type(q(app, '[data-field="min"]'), "3");
    type(q(app, '[data-field="max"]'), "6");
    expect(q(app, ".sched-edit .error").hidden).toBe(true);
    tap(app, "sched-save");
    await settle(run, 64);
    expect(srv.sent.map((x) => [x.method, x.url, x.body])).toEqual([
      ["PUT", "/api/v1/appliances/a1/jobs/clean_filters/schedule", '{"kind":"window","min":3,"max":6,"unit":"month"}'],
    ]);
    expect(app.els.sheet.querySelector(".sched-edit")).toBeNull();
    expect([...app.els.sheet.querySelectorAll(".sched-line")].map((x) => x.textContent)).toEqual(["Every 3 monthsFrom the manual · p. 43", "Yoursevery 3 to 6 months"]);
    // Back to the manual's.
    tap(app, "sched-edit");
    run(80);
    q<HTMLInputElement>(app, '[data-field="kind"][value="manual"]').click();
    tap(app, "sched-save");
    await settle(run, 96);
    expect(srv.sent.at(-1)?.body).toBe('{"kind":"manual"}');
    expect(q(app, ".job-settings .sched-line").textContent).toBe("Following the manual");
  });

  it("undoes the last change, and says why when it can't", async () => {
    const { app, run, srv } = await open({ hash: "#job/a1/clean_filters" });
    srv.replies.undo = 409;
    tap(app, "sheet-undo");
    await settle(run, 16);
    expect(q(app, '[role="alert"]').textContent).toBe("Nothing to undo.");
    srv.replies.undo = 403;
    tap(app, "sheet-undo");
    await settle(run, 32);
    expect(q(app, '[role="alert"]').textContent).toBe("This phone can only view this home's jobs.");
    srv.replies.undo = 0;
    tap(app, "sheet-undo");
    await settle(run, 48);
    expect(srv.sent.map((x) => [x.method, x.url, x.body])).toContainEqual(["POST", "/api/v1/appliances/a1/jobs/clean_filters/undo", null]);
    tap(app, "sheet-more");
    run(60);
    expect(q(app, ".changes li").textContent).toContain("Undid a change");
  });

  it("opens straight on the sheet from a calendar entry, still counting the reminder's open", async () => {
    const { app, srv } = await open({ search: "?appliance=a1&job=clean_filters&reminder=k1&ch=ics&title=Clean%20the%20filters", hash: "#job/a1/clean_filters" });
    expect(srv.sent.map((x) => [x.url, x.body])).toEqual([["/api/v1/reminders/k1/opened", '{"channel":"ics"}']]);
    expect(app.els.landing.hidden).toBe(true);
    expect(app.els.sheet.hidden).toBe(false);
    expect(q(app, "h1").textContent).toBe("Clean the filters");
  });

  it("is one tap from a reminder", async () => {
    const { app, run, pushed } = await open({ search: "?reminder=a1:clean_filters:2026-11-01&title=Clean%20the%20filters" });
    expect(app.els.landing.hidden).toBe(false);
    (app.els.root.querySelector('[data-act="landing-sheet"]') as HTMLButtonElement).click();
    await settle(run, 16);
    expect(pushed).toEqual(["job/a1/clean_filters"]);
    expect(app.els.landing.hidden).toBe(true);
    expect(q(app, "h1").textContent).toBe("Clean the filters");
  });
});

describe("the language switch", () => {
  it("shows the language in use and hands a new pick to setLang", async () => {
    const picked: string[] = [];
    const { d, run } = deps({ search: "", lang: "nl", setLang: (v) => void picked.push(v) });
    const app = new App(d);
    await app.boot();
    run(0);
    const sel = app.els.lang;
    expect([...sel.options].map((o) => o.textContent)).toEqual(["English", "Nederlands"]);
    expect(sel.value).toBe("nl");
    sel.value = "nl";
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    expect(picked).toEqual([]);
    sel.value = "en";
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    expect(picked).toEqual(["en"]);
  });
});

describe("the language switch after a pick", () => {
  it("shows the picked language after the reload, and the browser can't bring the old one back", async () => {
    const kept = new Map<LangSlot, string>([["home", "en"]]);
    const store: LangStore = { get: (k) => kept.get(k) ?? null, set: (k, v) => void kept.set(k, v) };
    let reloads = 0;
    pickLang(store, "nl", { search: "", replaceSearch: () => undefined, reload: () => void reloads++ });
    const first = deps({ search: "", lang: chooseLang("", store, "en-GB") });
    const before = new App(first.d);
    await before.boot();
    first.run(0);
    expect(before.els.lang.value).toBe("nl");
    pickLang(store, "en", { search: "", replaceSearch: () => undefined, reload: () => void reloads++ });
    expect(reloads).toBe(2);
    const { d, run } = deps({ search: "", lang: chooseLang("", store, "nl-NL") });
    const app = new App(d);
    await app.boot();
    run(0);
    expect(app.els.lang.value).toBe("en");
    expect(app.els.lang.getAttribute("autocomplete")).toBe("off");
  });
});

describe("keeping the app on the home screen", () => {
  function host(kind: InstallKind | null, accepted = true) {
    const h: InstallHost & { prompted: number; answered: number } = {
      prompted: 0,
      answered: 0,
      kind: () => kind,
      prompt: async () => (h.prompted++, accepted),
      done: () => void h.answered++,
    };
    return h;
  }
  // A one-step job, done.
  async function finish(over: Partial<Deps>) {
    const r = await live(() => [], { search: "?appliance=a1&job=j", ...over });
    const v = { check_id: "c", attempt: 1, judge: "done", clauses: [], result: "done" } as const;
    r.app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "a", index: 0, of: 1, text: "One.", page: 43, view: "front", power: "any" });
    r.app.onMessage({ type: "verdict", seq: 2, t_ms: 2, verdict: { ...v, step_id: "a" } } as never);
    r.run(16);
    return r;
  }
  const visible = (app: App) => [app.els.installPrompt, app.els.installIos, app.els.installOther, app.els.installAdded].map((e) => !e.hidden);

  it("is not there before a job is done", async () => {
    const { app, run } = await live(() => [], { install: host("prompt") });
    run(0);
    expect(app.els.install.hidden).toBe(true);
  });

  it("offers Android's own prompt on Job done, and says so once added", async () => {
    const h = host("prompt");
    const { app, run } = await finish({ install: h });
    expect(app.els.complete.hidden).toBe(false);
    expect(app.els.install.hidden).toBe(false);
    expect(visible(app)).toEqual([true, false, false, false]);
    app.els.installPrompt.click();
    await new Promise((r) => setTimeout(r, 0));
    run(32);
    expect(h.prompted).toBe(1);
    expect(h.answered).toBe(1);
    expect(visible(app)).toEqual([false, false, false, true]);
    expect(app.els.installLater.hidden).toBe(true);
    expect(app.els.completeText.textContent).toBe("Checked against your manual.");
  });

  it("closes, and won't come back, when the prompt is turned down or Not now is tapped", async () => {
    const h = host("prompt", false);
    const { app, run } = await finish({ install: h });
    app.els.installPrompt.click();
    await new Promise((r) => setTimeout(r, 0));
    run(32);
    expect(app.els.install.hidden).toBe(true);
    expect(h.answered).toBe(1);
    const h2 = host("ios-safari");
    const b = await finish({ install: h2 });
    expect(visible(b.app)).toEqual([false, true, false, false]);
    expect(b.app.els.installIos.textContent).toContain("Add to Home Screen");
    // Safari sends web notifications only to the home-screen app, and the offer says so.
    expect(b.app.els.installWhy.textContent).toMatch(/^On an iPhone, reminders reach you only when Fix-It Lens is on your home screen\./);
    b.app.els.installLater.click();
    b.run(32);
    expect(b.app.els.install.hidden).toBe(true);
    expect(h2.answered).toBe(1);
  });

  it("in another iPhone browser, says to open it in Safari and copies the page without its query", async () => {
    const copied: string[] = [];
    const { app, run } = await finish({ install: host("ios-other"), origin: "https://fix.example", copy: async (t) => void copied.push(t) });
    expect(visible(app)).toEqual([false, false, true, false]);
    expect(app.els.installUrl.textContent).toBe(`https://fix.example${location.pathname}`);
    app.els.installCopy.click();
    await new Promise((r) => setTimeout(r, 0));
    run(32);
    expect(copied).toEqual([`https://fix.example${location.pathname}`]);
    expect(app.els.installCopy.textContent).toBe("Link copied");
  });

  it("is never offered in a replay, or when the browser has nothing to offer", async () => {
    const h = host("prompt");
    const r = await live(() => [], { search: "?appliance=a1&job=j", install: h });
    (r.app.s as { mode: string }).mode = "replay";
    const v = { check_id: "c", attempt: 1, judge: "done", clauses: [], result: "done" } as const;
    r.app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "a", index: 0, of: 1, text: "One.", page: 43, view: "front", power: "any" });
    r.app.onMessage({ type: "verdict", seq: 2, t_ms: 2, verdict: { ...v, step_id: "a" } } as never);
    r.run(16);
    expect(r.app.els.complete.hidden).toBe(false);
    expect(r.app.els.install.hidden).toBe(true);
    const n = await finish({ install: host(null) });
    expect(n.app.els.install.hidden).toBe(true);
  });
});

describe("your appliances and the card library (contracts 1.25)", () => {
  const CARD = {
    manual: { source: "maker_site", source_url: "https://acme.example/docs/wm-0000.pdf", title: "WM-0000 user manual", lang: "en", page_count: 48 },
    jobs: [
      { id: "clean_filter", title: { en: "Clean the filter" }, kind: "upkeep", steps: [{ id: "open_flap", text: { en: "Open the flap." }, page: 31 }] },
      { id: "drain_pump", title: { en: "Clear the drain pump" }, kind: "fix", steps: [{ id: "lift", text: { en: "Lift the cover." }, page: 40 }] },
    ],
    refused: [{ id: "heater", title: { en: "Replace the heater" }, reason: "mains", page: 44 }],
    checks: { citation: { status: "pass", failures: [] }, rules: { status: "pass", failures: [] } },
  };
  const UPLOAD = { ...CARD, manual: { source: "household_upload", source_url: `urn:fixit:upload:${"b".repeat(64)}`, title: null, lang: "en", page_count: 12 }, refused: [] };
  const RECORD = {
    card_sha256: "a".repeat(64), version: 1, built_at: "2026-10-20T09:00:00Z", shared: true, private_reason: null,
    reviewed: { by_person: false, at: null }, homes: 3, quotes: { checked: 17, passed: 16 }, reports_open: { total: 1, safety: 1 },
  };
  const WASHER = { id: "a1", alias: "washing machine", kind: "washer", brand: "Acme", model: "WM-0000", plate_confirmed: true, card_status: "checked" };
  const DRYER = { id: "a2", alias: "dryer", kind: "dryer", brand: "Acme", model: "DR-1", plate_confirmed: true, card_status: "checked" };
  const DUE = {
    appliance_id: "a1", job_id: "clean_filter", title: "Clean the filter", due_on: new Date(Date.now() + 40 * 86_400_000).toISOString().slice(0, 10),
    reason: "scheduled", status: "upcoming", last_done: { date: "2026-07-01", source: "user" },
    manual: { every: 3, unit: "month", page: 31, quote: "Clean the filter every three months." }, schedule: null, window_end: null, snoozed_until: null,
  };

  function server() {
    const sent: { method: string; url: string; body: string | null; auth: string | null }[] = [];
    const replies: Record<string, number> = {};
    let washer: Record<string, unknown> = WASHER;
    let dryerGone = false;
    const f = (async (u: string, init?: RequestInit) => {
      const url = String(u);
      const method = init?.method ?? "GET";
      const auth = new Headers(init?.headers).get("authorization");
      sent.push({ method, url, body: init?.body ? String(init.body) : null, auth });
      const tail = url.split("/").pop()!;
      if (method === "DELETE") {
        if (replies.delete) return new Response("{}", { status: replies.delete });
        dryerGone = url.endsWith("/appliances/a2");
        return new Response(null, { status: dryerGone ? 204 : 404 });
      }
      if (method === "POST" && replies[tail]) return new Response(JSON.stringify({ error: "too_many_reports" }), { status: replies[tail] });
      if (method === "POST" && tail === "reports") return new Response(JSON.stringify({ id: "r1", status: "open" }), { status: 201 });
      if (method === "POST" && tail === "not-mine") {
        washer = { ...WASHER, card_status: "failed", ingest: { stage: "lookup", notice: { kind: "lookup_failed", reason: "not_mine", text: { en: "Add the PDF of your own manual, or paste its link under Manual link." } } } };
        return new Response(JSON.stringify(washer), { status: 200 });
      }
      if (url.endsWith("/a1/card/record")) return new Response(JSON.stringify(RECORD), { status: 200 });
      if (url.endsWith("/card/record")) return new Response(JSON.stringify({ error: "not_found" }), { status: 404 });
      if (url.endsWith("/a1/card")) return washer.card_status === "checked" ? new Response(JSON.stringify(CARD), { status: 200 }) : new Response("{}", { status: 404 });
      if (url.endsWith("/a2/card")) return new Response(JSON.stringify(UPLOAD), { status: 200 });
      if (url.endsWith(".png")) return new Response(new Blob(["png"]), { status: 200 });
      if (url.includes("/due")) return new Response(JSON.stringify(washer.card_status === "checked" ? [DUE] : []), { status: 200 });
      if (url.endsWith("/changes")) return new Response("[]", { status: 200 });
      if (url.endsWith("/appliances/a1")) return new Response(JSON.stringify(washer), { status: 200 });
      // As the server does: the list leaves out ingest, and only GET /appliances/{id} carries the notice.
      const { ingest: _, ...row } = washer;
      return new Response(JSON.stringify(dryerGone ? [row] : [row, DRYER]), { status: 200 });
    }) as typeof fetch;
    return { f, sent, replies };
  }

  const tick = () => new Promise((r) => setTimeout(r, 0));
  async function settle(run: (t: number) => void, t = 0): Promise<void> {
    for (let i = 0; i < 6; i++) await tick();
    run(t);
  }
  async function open(over: Partial<Deps> = {}) {
    const srv = server();
    const pushed: string[] = [];
    const urls: string[] = [];
    const dropped: string[] = [];
    const { d, run } = deps({
      search: "",
      fetch: srv.f,
      household: tokens("tok"),
      pushHash: (h) => void pushed.push(h),
      objectUrl: { make: () => (urls.push(`blob:${urls.length}`), `blob:${urls.length - 1}`), drop: (u) => void dropped.push(u) },
      ...over,
    });
    const app = new App(d);
    await app.boot();
    await settle(run);
    return { app, run, srv, pushed, urls, dropped };
  }
  const dv = (app: App) => app.els.devices;
  const tap = (root: HTMLElement, sel: string) => (root.querySelector(sel) as HTMLElement).click();

  it("home opens every appliance; one appliance shows its jobs, its manual at the maker's page, and how it was checked", async () => {
    const { app, run, srv, pushed } = await open();
    expect(app.els.devicesLink.hidden).toBe(false);
    app.els.devicesLink.click();
    await settle(run, 16);
    expect(pushed).toEqual(["devices"]);
    expect(app.els.home.hidden).toBe(true);
    expect(dv(app).hidden).toBe(false);
    expect([...dv(app).querySelectorAll(".item-open strong")].map((e) => e.textContent)).toEqual(["Washing machine", "Dryer"]);
    expect(dv(app).textContent).toContain("Next: Clean the filter");

    tap(dv(app), '[data-act="devices-open"][data-appliance="a1"]');
    await settle(run, 32);
    expect(pushed.at(-1)).toBe("devices/a1");
    expect(srv.sent.some((s) => s.url === "/api/v1/appliances/a1/card/record" && s.auth === "Bearer tok")).toBe(true);
    const text = dv(app).textContent ?? "";
    // The open safety report leads the page.
    expect(dv(app).querySelector(".safety-alert")?.textContent).toMatch(/reported something unsafe/);
    expect(text).toContain("Clean the filter");
    expect(text).toContain("Something's wrong");
    expect(text).toContain("Keep it runningUpkeepClean the filter");
    expect(text).toContain("Fixes in the manualFixClear the drain pump");
    expect(text).toContain("Clear the drain pump");
    expect(text).toContain("16 of 17 matched");
    expect(text).toContain("3 homes use it");
    // Every citation opens the maker's own PDF at its page, in a new tab; nothing is served from us.
    const open1 = [...dv(app).querySelectorAll("a")].find((a) => a.textContent === "Open the manual") as HTMLAnchorElement;
    expect(open1.getAttribute("href")).toBe("https://acme.example/docs/wm-0000.pdf#page=1");
    expect(open1.target).toBe("_blank");
    expect(open1.rel).toBe("noopener noreferrer");
    const left = dv(app).querySelector(".ap-refused a") as HTMLAnchorElement;
    expect(left.getAttribute("href")).toBe("https://acme.example/docs/wm-0000.pdf#page=44");
    expect(srv.sent.some((s) => s.url.endsWith(".png"))).toBe(false);

    // The job's sheet: its manual line's page opens the same PDF there; Close comes back to the appliance.
    tap(dv(app), '[data-act="open-sheet"]');
    await settle(run, 48);
    expect(app.els.sheet.hidden).toBe(false);
    expect(dv(app).hidden).toBe(true);
    expect((app.els.sheet.querySelector(".sched-line a") as HTMLAnchorElement).getAttribute("href")).toBe("https://acme.example/docs/wm-0000.pdf#page=31");
    tap(app.els.sheet, '[data-act="sheet-close"]');
    await settle(run, 64);
    expect(pushed.at(-1)).toBe("devices/a1");
    expect(dv(app).hidden).toBe(false);
  });

  it("takes an appliance out of the home after asking once, and says so when that fails", async () => {
    const { app, run, srv, pushed } = await open({ hash: "#devices/a2" });
    expect(dv(app).querySelector('[data-act="remove-go"]')).toBeNull();
    tap(dv(app), '[data-act="remove-ask"]');
    run(16);
    expect(dv(app).querySelector(".ap-remove p")?.textContent).toBe("Remove dryer from this home? Its jobs and reminders go; what you did with it stays in the history.");
    tap(dv(app), '[data-act="remove-cancel"]');
    run(32);
    expect(dv(app).querySelector('[data-act="remove-go"]')).toBeNull();
    srv.replies.delete = 500;
    tap(dv(app), '[data-act="remove-ask"]');
    run(48);
    tap(dv(app), '[data-act="remove-go"]');
    await settle(run, 64);
    expect(dv(app).querySelector(".ap-remove .notice-line")?.textContent).toBe("Couldn't remove it. Check the connection and try again.");
    expect(dv(app).querySelector('[data-act="remove-go"]')).not.toBeNull();
    delete srv.replies.delete;
    tap(dv(app), '[data-act="remove-go"]');
    await settle(run, 80);
    expect(srv.sent.filter((s) => s.method === "DELETE").map((s) => [s.url, s.auth])).toEqual([["/api/v1/appliances/a2", "Bearer tok"], ["/api/v1/appliances/a2", "Bearer tok"]]);
    expect(pushed.at(-1)).toBe("devices");
    expect([...dv(app).querySelectorAll(".item-open strong")].map((e) => e.textContent)).toEqual(["Washing machine"]);
  });

  it("files a report without touching the card, and words a refusal", async () => {
    const { app, run, srv } = await open({ hash: "#devices/a1" });
    tap(dv(app), '[data-act="report-open"]');
    run(16);
    tap(dv(app), '[data-act="report-send"]');
    await settle(run, 32);
    expect(dv(app).querySelector(".report .error")?.textContent).toBe("Pick what's wrong.");
    const pick = (v: string) => {
      const r = dv(app).querySelector(`input[value="${v}"]`) as HTMLInputElement;
      r.checked = true;
      r.dispatchEvent(new Event("change", { bubbles: true }));
    };
    pick("wrong_step");
    run(48);
    const sel = dv(app).querySelector('select[data-report="step"]') as HTMLSelectElement;
    sel.value = "drain_pump/lift";
    sel.dispatchEvent(new Event("change", { bubbles: true }));
    const note = dv(app).querySelector('textarea[data-report="note"]') as HTMLTextAreaElement;
    note.value = "It's the other cover.";
    note.dispatchEvent(new Event("input", { bubbles: true }));
    srv.replies.reports = 429;
    tap(dv(app), '[data-act="report-send"]');
    await settle(run, 64);
    expect(dv(app).querySelector(".report .error")?.textContent).toMatch(/20 reports waiting/);
    // What was typed survives the redraw.
    expect((dv(app).querySelector('textarea[data-report="note"]') as HTMLTextAreaElement).value).toBe("It's the other cover.");
    delete srv.replies.reports;
    tap(dv(app), '[data-act="report-send"]');
    await settle(run, 80);
    const post = srv.sent.filter((s) => s.method === "POST").at(-1)!;
    expect(post.url).toBe("/api/v1/appliances/a1/reports");
    expect(JSON.parse(post.body!)).toEqual({ kind: "wrong_step", job_id: "drain_pump", step_id: "lift", note: "It's the other cover." });
    expect(dv(app).querySelector(".report")).toBeNull();
    expect(dv(app).querySelector(".done-line")?.textContent).toMatch(/^Sent\. Thank you\./);
    expect(srv.sent.every((s) => !(s.method !== "GET" && s.url.endsWith("/card")))).toBe(true);
  });

  it("\"This isn't my appliance\" unlinks it for this home, and the page asks for the manual", async () => {
    const { app, run, srv } = await open({ hash: "#devices/a1" });
    tap(dv(app), '[data-act="report-open"]');
    run(16);
    const r = dv(app).querySelector('input[value="not_mine"]') as HTMLInputElement;
    r.checked = true;
    r.dispatchEvent(new Event("change", { bubbles: true }));
    run(32);
    expect(dv(app).textContent).toContain("Other homes keep it.");
    expect(dv(app).querySelector('[data-act="report-send"]')?.textContent).toBe("Stop using this card");
    tap(dv(app), '[data-act="report-send"]');
    await settle(run, 48);
    const post = srv.sent.filter((s) => s.method === "POST").at(-1)!;
    expect(post.url).toBe("/api/v1/appliances/a1/not-mine");
    expect(post.body).toBe("{}");
    const text = dv(app).textContent ?? "";
    expect(text).toContain("Your home no longer uses that card");
    expect(text).toContain("Add the PDF of your own manual");
    expect(text).not.toContain("How this card was checked");
    tap(dv(app), '[data-act="devices-manual"]');
    run(64);
    expect(app.els.add.hidden).toBe(false);
    expect(app.els.addHead.textContent).toBe("The washing machine's manual");
  });

  it("a home's own upload opens in the app, page by page, with the bearer; no maker link is made", async () => {
    const { app, run, srv, urls, dropped } = await open({ hash: "#devices/a2" });
    expect(dv(app).querySelector("a")).toBeNull();
    expect(dv(app).textContent).toContain("Only your home can open it.");
    expect(dv(app).textContent).toContain("The rest of the check record isn't available");
    tap(dv(app), '[data-act="view-page"]');
    await settle(run, 16);
    expect(srv.sent.at(-1)).toMatchObject({ url: "/api/v1/appliances/a2/manual/pages/1.png", auth: "Bearer tok" });
    expect(app.els.pageView.hidden).toBe(false);
    expect(app.els.pageImg.getAttribute("src")).toBe("blob:0");
    expect(app.els.pageCap.textContent).toBe("Page 1 of 12");
    expect(app.els.pagePrev.disabled).toBe(true);
    app.els.pageNext.click();
    await settle(run, 32);
    expect(srv.sent.at(-1)!.url).toBe("/api/v1/appliances/a2/manual/pages/2.png");
    expect(app.els.pageImg.getAttribute("src")).toBe("blob:1");
    expect(dropped).toEqual(["blob:0"]);
    (app.els.pageView.querySelector('[data-act="close-page"]') as HTMLButtonElement).click();
    run(48);
    expect(app.els.pageView.hidden).toBe(true);
    expect(dropped).toEqual(["blob:0", "blob:1"]);
    expect(urls).toHaveLength(2);
  });

  it("in a live session for a maker's card, the step's citation opens the maker's PDF at that page", async () => {
    const { app, run } = await open({ search: "?appliance=a1&job=clean_filter", openSocket: () => new Sock() });
    await app.startLive();
    await settle(run, 16);
    app.onMessage({ type: "hello_ok", seq: 0, t_ms: 0, session_id: "s9", resumed: false, mode: "api" });
    app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "clean_filter", step_id: "open_flap", index: 0, of: 1, text: "Open the flap.", page: 31, view: "front", power: "any" });
    run(32);
    expect(app.els.cardMaker.hidden).toBe(false);
    expect(app.els.cardMaker.getAttribute("href")).toBe("https://acme.example/docs/wm-0000.pdf#page=31");
    expect(app.els.showPage.hidden).toBe(true);
  });
});

describe("a typed model that needs its brand (422 brand_needed)", () => {
  it("asks for the brand, then sends it with the model", async () => {
    const bodies: Record<string, unknown>[] = [];
    const f = (async (u: string, init?: RequestInit) => {
      if (init?.method === "POST") {
        const b = JSON.parse(String(init.body)) as Record<string, unknown>;
        bodies.push(b);
        return b.brand
          ? new Response(JSON.stringify({ id: "a1", alias: "fridge", kind: "fridge", brand: "Acme", model: "RB-1", plate_confirmed: false, card_status: "pending", ingest: { stage: "lookup" } }), { status: 201 })
          : new Response(JSON.stringify({ error: "brand_needed" }), { status: 422 });
      }
      return new Response("[]", { status: 200 });
    }) as typeof fetch;
    const { d, run } = deps({ search: "", fetch: f, household: tokens("tok") });
    const app = new App(d);
    await app.boot();
    (app.els.root.querySelector('[data-act="type-model"]') as HTMLButtonElement).click();
    run(0);
    expect(app.els.addBrandRow.hidden).toBe(true);
    app.els.addModel.value = "RB-1";
    app.els.addGo.click();
    for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
    run(16);
    expect(app.els.addBrandRow.hidden).toBe(false);
    expect(app.els.addNotice.textContent).toBe("Which brand is it? Type the brand from the model label, then tap Add.");
    app.els.addBrand.value = "Acme";
    app.els.addGo.click();
    for (let i = 0; i < 4; i++) await new Promise((r) => setTimeout(r, 0));
    run(32);
    expect(bodies).toEqual([{ model: "RB-1" }, { model: "RB-1", brand: "Acme" }]);
    expect(app.els.addBrandRow.hidden).toBe(true);
  });
});

describe("a job the manual gives no interval (contracts 1.20)", () => {
  const ST = { last_done: null, last_done_source: null, schedule: null, snoozed_until: null };
  const NEXT = new Date(Date.now() + 365 * 86_400_000).toISOString().slice(0, 10);

  /** A boiler whose card has one upkeep job with no interval: off /due until a person sets a schedule. */
  function server() {
    const sent: { method: string; url: string; body: string | null }[] = [];
    let schedule: Record<string, unknown> | null = null;
    let changes: Record<string, unknown>[] = [];
    const row = () => ({
      appliance_id: "a3",
      job_id: "bleed_radiators",
      title: "Bleed the radiators",
      due_on: NEXT,
      reason: "scheduled",
      status: "upcoming",
      last_done: { date: today(), source: "user" },
      manual: null,
      schedule,
      window_end: null,
      snoozed_until: null,
    });
    const f = (async (u: string, init?: RequestInit) => {
      const url = String(u);
      const method = init?.method ?? "GET";
      if (method !== "GET") sent.push({ method, url, body: init?.body ? String(init.body) : null });
      if (url.endsWith("/changes")) return new Response(JSON.stringify(changes), { status: 200 });
      if (method === "PUT" && url.endsWith("/schedule")) {
        const body = JSON.parse(String(init!.body));
        const before = schedule;
        schedule = body.kind === "manual" ? null : body;
        changes = [{ id: changes.length + 1, at: "2026-10-27T12:00:00Z", kind: "schedule", by: { kind: "app" }, before: { ...ST, schedule: before }, after: { ...ST, schedule }, undone: false }, ...changes];
        return schedule ? new Response(JSON.stringify(row()), { status: 200 }) : new Response(null, { status: 204 });
      }
      if (method === "POST" && url.endsWith("/undo")) {
        const last = changes.find((c) => !c.undone)!;
        last.undone = true;
        schedule = (last.before as { schedule: Record<string, unknown> | null }).schedule;
        return schedule ? new Response(JSON.stringify(row()), { status: 200 }) : new Response(null, { status: 204 });
      }
      if (url.includes("/due")) return new Response(JSON.stringify(schedule ? [row()] : []), { status: 200 });
      if (url.endsWith("/card"))
        return new Response(
          JSON.stringify({
            manual: { source: "maker_site", source_url: "https://acme.example/cv-24.pdf", title: "CV-24 manual", lang: "en", page_count: 20 },
            jobs: [
              { id: "bleed_radiators", title: { en: "Bleed the radiators" }, kind: "upkeep", interval: null, steps: [{ id: "key", text: { en: "Fit the key." }, page: 9 }] },
              { id: "pressure", title: { en: "Top up the pressure" }, kind: "fix", interval: null, steps: [{ id: "tap", text: { en: "Open the tap." }, page: 11 }] },
            ],
          }),
          { status: 200 },
        );
      return new Response(JSON.stringify([{ id: "a3", alias: "boiler", kind: "boiler", brand: "Acme", model: "CV-24", plate_confirmed: true, card_status: "checked" }]), { status: 200 });
    }) as typeof fetch;
    return { f, sent };
  }

  const tick = () => new Promise((r) => setTimeout(r, 0));
  async function settle(run: (t: number) => void, t = 0): Promise<void> {
    for (let i = 0; i < 6; i++) await tick();
    run(t);
  }
  async function open() {
    const srv = server();
    const { d, run } = deps({ search: "", fetch: srv.f, household: tokens("tok"), pushHash: () => {} });
    const app = new App(d);
    await app.boot();
    await settle(run);
    return { app, run, srv };
  }
  const q = <T extends HTMLElement>(app: App, sel: string) => app.els.sheet.querySelector(sel) as T;
  const tap = (app: App, act: string) => q<HTMLButtonElement>(app, `[data-act="${act}"]`).click();
  const type = (input: HTMLElement, v: string) => {
    (input as HTMLInputElement).value = v;
    input.dispatchEvent(new Event(input.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
  };

  it("lists it under No schedule yet, sets the home's own, words it as yours, stops it, and undo brings it back", async () => {
    const { app, run, srv } = await open();
    const home = app.els.homeList;
    expect([...home.querySelectorAll("h2")].map((h) => h.textContent)).toEqual(["Keep it running", "No schedule yet", "Something's wrong"]);
    expect(home.textContent).toContain("Boiler · the manual gives no interval");
    // A repair is never scheduled, and the appliance isn't listed twice.
    expect(home.textContent).not.toContain("Top up the pressure");
    expect(home.textContent).not.toContain("Your appliances");

    (home.querySelector('[data-act="open-sheet"]') as HTMLButtonElement).click();
    await settle(run, 16);
    const sheet = app.els.sheet;
    expect(sheet.hidden).toBe(false);
    expect(sheet.textContent).toContain("No schedule yet");
    expect(sheet.textContent).toContain("Manualgives no interval");
    expect(sheet.textContent).not.toContain("This job has no schedule in your home.");
    expect(sheet.querySelector('[data-act="sheet-done-ask"]')).toBeNull();
    expect(sheet.textContent).not.toMatch(/p\. null|null|undefined/);

    tap(app, "sched-edit");
    run(32);
    // Empty: no "As the manual says", no number, no unit.
    expect(sheet.querySelector('input[value="manual"]')).toBeNull();
    expect(q<HTMLInputElement>(app, '[data-field="every"]').value).toBe("");
    expect(q<HTMLSelectElement>(app, '[data-field="unit"]').value).toBe("");
    expect(q<HTMLElement>(app, ".sched-edit .error").hidden).toBe(true);
    tap(app, "sched-save");
    expect(q<HTMLElement>(app, ".sched-edit .error").hidden).toBe(false);
    expect(srv.sent).toEqual([]);
    type(q(app, '[data-field="every"]'), "1");
    expect(q<HTMLElement>(app, ".sched-edit .error").textContent).toBe("Pick days, weeks, months or years.");
    type(q(app, '[data-field="unit"]'), "year");
    tap(app, "sched-save");
    await settle(run, 48);
    expect(JSON.parse(srv.sent[0]!.body!)).toEqual({ kind: "every", every: 1, unit: "year" });
    expect(sheet.textContent).toContain("Manualgives no interval");
    expect(sheet.textContent).toContain("Yoursevery year");
    expect(sheet.querySelector('[data-act="sheet-done-ask"]')).not.toBeNull();
    expect(home.textContent).toContain("your schedule");
    expect(home.textContent).not.toContain("No schedule yet");

    // Stop: a 204 takes it off the list, and the sheet offers a schedule again.
    tap(app, "sched-stop");
    await settle(run, 64);
    expect(JSON.parse(srv.sent[1]!.body!)).toEqual({ kind: "manual" });
    expect(home.textContent).toContain("No schedule yet");
    expect(sheet.textContent).toContain("Schedule stopped");
    expect(sheet.querySelector('[data-act="sched-edit"]')!.textContent).toBe("Set a schedule");
    expect(sheet.textContent).not.toContain("This job has no schedule in your home.");

    // Undo answers 200 with the job back on the list.
    tap(app, "sheet-undo");
    await settle(run, 80);
    expect(srv.sent[2]!.url).toMatch(/\/undo$/);
    expect(sheet.textContent).toContain("Yoursevery year");
    expect(home.textContent).not.toContain("No schedule yet");
  });

  it("shows the job on its appliance's page, opening the same sheet", async () => {
    const { app, run } = await open();
    (app.els.devicesLink as HTMLButtonElement).click();
    await settle(run, 16);
    (app.els.devices.querySelector('[data-act="devices-open"][data-appliance="a3"]') as HTMLButtonElement).click();
    await settle(run, 32);
    expect(app.els.devices.querySelector(".ap-jobs")!.textContent).toContain("Bleed the radiatorsNo schedule yet · the manual gives no interval");
  });
});

describe("Something's wrong: what a code on the display means (F1a, GET …/meaning)", () => {
  const CARD = {
    manual: { source: "maker_site", source_url: "https://acme.example/docs/wm-0000.pdf", title: "WM-0000 user manual", lang: "de", page_count: 48 },
    jobs: [
      { id: "clean_filter", title: { en: "Clean the filter" }, kind: "upkeep", interval: { every: 3, unit: "month" }, steps: [{ id: "open_flap", text: { en: "Open the flap." }, page: 31 }] },
      { id: "drain_pump", title: { en: "Clear the drain pump" }, kind: "fix", interval: null, steps: [{ id: "lift", text: { en: "Lift the cover." }, page: 40 }] },
    ],
    refused: [],
  };
  const E24 = {
    code: "E24", job_id: "drain_pump", title: { en: "Clear the drain pump" }, page: 40, quote: "E24: Pumpe verstopft. Flusensieb reinigen.", quote_en: "E24: pump blocked. Clean the lint filter.",
    row_id: "e24", meaning: { en: "The water isn't draining" }, cause: { en: "The pump or its filter is blocked" }, remedy: { en: "Clean the drain filter" }, remedy_kind: "user", escalate_to: null, symbol: null,
  };
  const none = { matches: [], candidates: [], refused: [], table_page: null, notice: null, landlord: false };
  function server(landlord = false) {
    const asked: string[] = [];
    const f = (async (u: string) => {
      const url = String(u);
      if (url.includes("/meaning?")) {
        const code = new URL(url, "http://x").searchParams.get("code")!;
        asked.push(`${url.includes("/a1/") ? "a1" : "a2"}:${code}`);
        if (url.includes("/a2/")) return new Response(JSON.stringify({ error: "not_found" }), { status: 404 });
        if (code === "E24") return new Response(JSON.stringify({ ...none, code, matches: [E24], landlord }), { status: 200 });
        if (code === "E2") return new Response(JSON.stringify({ ...none, code, candidates: ["E20", "E24"], notice: "Which of these does the display show?" }), { status: 200 });
        if (code === "F07") return new Response(JSON.stringify({ ...none, code, refused: ["F07"], table_page: 40, notice: "The manual leaves this code to a technician." }), { status: 200 });
        return new Response(JSON.stringify({ ...none, code, table_page: 40, notice: `${code} isn't in this manual's list of codes.` }), { status: 200 });
      }
      if (url.endsWith("/a1/card")) return new Response(JSON.stringify(CARD), { status: 200 });
      if (url.endsWith("/a2/card")) return new Response(JSON.stringify(CARD), { status: 200 });
      if (url.endsWith("/card/record")) return new Response("{}", { status: 404 });
      if (url.includes("/due")) return new Response("[]", { status: 200 });
      return new Response(
        JSON.stringify([
          { id: "a1", alias: "washing machine", kind: "washer", brand: "Acme", model: "WM-0000", plate_confirmed: true, card_status: "checked" },
          { id: "a2", alias: "dryer", kind: "dryer", brand: "Acme", model: "DR-1", plate_confirmed: true, card_status: "checked" },
        ]),
        { status: 200 },
      );
    }) as typeof fetch;
    return { f, asked };
  }
  const tick = () => new Promise((r) => setTimeout(r, 0));
  async function settle(run: (t: number) => void, t = 0): Promise<void> {
    for (let i = 0; i < 6; i++) await tick();
    run(t);
  }
  async function open(landlord = false) {
    const srv = server(landlord);
    const { d, run } = deps({ search: "", fetch: srv.f, household: tokens("tok"), pushHash: () => {} });
    const app = new App(d);
    await app.boot();
    await settle(run);
    return { app, run, srv };
  }
  const dv = (app: App) => app.els.devices;
  const tap = (app: App, sel: string) => (dv(app).querySelector(sel) as HTMLButtonElement).click();
  const typeCode = (app: App, v: string) => {
    const i = dv(app).querySelector('input[data-fault="code"]') as HTMLInputElement;
    i.value = v;
    i.dispatchEvent(new Event("input", { bubbles: true }));
  };

  it("goes from home to the appliance, and shows the manual's own row with its page and the fix to start", async () => {
    const { app, run, srv } = await open();
    (app.els.homeList.querySelector('[data-act="wrong"]') as HTMLButtonElement).click();
    run(16);
    expect(dv(app).querySelector("h1")!.textContent).toBe("Something's wrong");
    expect(dv(app).textContent).toContain("Which appliance?");
    tap(app, '[data-act="devices-open"][data-appliance="a1"]');
    await settle(run, 32);
    expect(dv(app).querySelector("#something-wrong h2")!.textContent).toBe("Something's wrong");

    // Nothing typed: no request.
    tap(app, '[data-act="fault-look"]');
    run(40);
    expect(dv(app).querySelector(".fault .error")!.textContent).toBe("Type the code first.");
    expect(srv.asked).toEqual([]);

    typeCode(app, " E24 ");
    tap(app, '[data-act="fault-look"]');
    await settle(run, 48);
    expect(srv.asked).toEqual(["a1:E24"]);
    const ans = dv(app).querySelector(".fault-answer")!;
    expect(ans.querySelector(".fault-head")!.textContent).toBe("E24The water isn't draining");
    expect(ans.querySelector("blockquote p")!.getAttribute("lang")).toBe("de");
    expect(ans.textContent).toContain("In EnglishE24: pump blocked. Clean the lint filter.");
    expect((ans.querySelector("a.cite-link") as HTMLAnchorElement).href).toBe("https://acme.example/docs/wm-0000.pdf#page=40");
    expect(ans.textContent).toContain("Cause: The pump or its filter is blocked");
    const start = ans.querySelector('[data-act="start-job"]') as HTMLButtonElement;
    expect(start.textContent).toBe("Start the fix: Clear the drain pump");
    expect(start.dataset.job).toBe("drain_pump");
    // The typed code stays in the box.
    expect((dv(app).querySelector('input[data-fault="code"]') as HTMLInputElement).value).toBe(" E24 ");
  });

  it("offers the card's close codes to pick, says a code isn't listed with the fault table's page, and a card it can't read", async () => {
    const { app, run, srv } = await open();
    (app.els.homeList.querySelector('[data-act="wrong"]') as HTMLButtonElement).click();
    run(16);
    tap(app, '[data-act="devices-open"][data-appliance="a1"]');
    await settle(run, 32);
    typeCode(app, "E2");
    tap(app, '[data-act="fault-look"]');
    await settle(run, 48);
    expect(dv(app).querySelector(".notice-line")!.textContent).toBe("Which of these does the display show?");
    expect([...dv(app).querySelectorAll('[data-act="fault-pick"]')].map((b) => b.textContent)).toEqual(["E20", "E24"]);
    tap(app, '[data-act="fault-pick"][data-code="E24"]');
    await settle(run, 64);
    expect(srv.asked).toEqual(["a1:E2", "a1:E24"]);
    expect((dv(app).querySelector('input[data-fault="code"]') as HTMLInputElement).value).toBe("E24");
    expect(dv(app).querySelector(".fault-match")).not.toBeNull();

    typeCode(app, "Z9");
    tap(app, '[data-act="fault-look"]');
    await settle(run, 80);
    const ans = dv(app).querySelector(".fault-answer")!;
    expect(ans.textContent).toContain("Z9 isn't in this manual's list of codes.");
    expect(ans.textContent).toContain("The manual's fault table: p. 40");
    expect(ans.querySelector('[data-act="start-job"]')).toBeNull();

    // A refused fix names no job to start: the server's notice says who does it.
    typeCode(app, "F07");
    tap(app, '[data-act="fault-look"]');
    await settle(run, 96);
    expect(dv(app).querySelector(".fault-answer")!.textContent).toContain("The manual leaves this code to a technician.");
    expect(dv(app).querySelector('.fault-answer [data-act="start-job"]')).toBeNull();

    tap(app, '[data-act="devices-back"]');
    run(100);
    tap(app, '[data-act="devices-open"][data-appliance="a2"]');
    await settle(run, 112);
    typeCode(app, "E1");
    tap(app, '[data-act="fault-look"]');
    await settle(run, 128);
    expect(dv(app).querySelector(".fault .error")!.textContent).toBe("This appliance has no checked card yet, so I can't look codes up.");
  });

  it("tells a renter to ask the landlord first, and the fix is no longer the one primary button", async () => {
    const { app, run } = await open(true);
    (app.els.homeList.querySelector('[data-act="wrong"]') as HTMLButtonElement).click();
    run(16);
    tap(app, '[data-act="devices-open"][data-appliance="a1"]');
    await settle(run, 32);
    typeCode(app, "E24");
    tap(app, '[data-act="fault-look"]');
    await settle(run, 48);
    expect(dv(app).querySelector(".fault-match .notice-line")!.textContent).toMatch(/^You rent: /);
    expect(dv(app).querySelector('.fault-match [data-act="start-job"]')!.classList.contains("ok")).toBe(false);
  });
});

describe("people in a home (E6)", () => {
  const FAR = new Date(Date.now() + 40 * 86_400_000).toISOString().slice(0, 10);
  const ANA = { id: "p1", name: "Ana", role: "admin", view_only: false, me: true };
  const SAM = { id: "p2", name: "Sam", role: "member", view_only: false, me: false };
  const ALERTS = { push: true, calendar: true, hub: false, items: "all", follows: [], quiet: null };

  /** One home with a dishwasher job, the people routes, and the caller's alerts; people: null is a server without E6. */
  function server(given: Record<string, unknown>[] | null = [ANA, SAM]) {
    // The server's own copy: a rename stays, as it does on the server, and the shared fixtures stay as they are.
    const people = given?.map((x) => ({ ...x })) ?? null;
    const sent: { method: string; url: string; body: unknown; auth: string | null }[] = [];
    const replies: Record<string, [number, unknown]> = {};
    let due: Record<string, unknown> = {
      appliance_id: "a1",
      job_id: "clean_filters",
      title: "Clean the filters",
      due_on: FAR,
      reason: "scheduled",
      status: "upcoming",
      last_done: { date: "2026-07-01", source: "user" },
      manual: { every: 3, unit: "month", page: 43, quote: "Clean the filters every 3 months." },
      schedule: null,
      window_end: null,
      snoozed_until: null,
      assignee: null,
    };
    let alerts: unknown = ALERTS;
    const f = (async (u: string, init?: RequestInit) => {
      const url = String(u).replace(/^\/api\/v1/, "");
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      if (method !== "GET") sent.push({ method, url, body, auth: new Headers(init?.headers).get("authorization") });
      const hit = replies[`${method} ${url}`];
      if (hit) return new Response(JSON.stringify(hit[1]), { status: hit[0] });
      const ok = (x: unknown, status = 200) => new Response(JSON.stringify(x), { status });
      if (url === "/household/people") return people ? ok(people) : ok({ detail: "Not Found" }, 404);
      if (url === "/me/alerts") {
        if (method === "PUT") alerts = body;
        return people ? ok(alerts) : ok({}, 404);
      }
      if (url === "/household/invites") return ok({ code: "c", join_url: "https://fil.example:8443/#join/c0de", role: body.role, view_only: body.view_only, email: body.email, expires_at: "2026-11-03T00:00:00Z" }, 201);
      if (url === "/household/join") return ok({ token: "tok-sam", person: { ...SAM, me: true }, household: { id: "h1" } }, 201);
      if (method === "PATCH" && url.startsWith("/household/people/") && typeof body?.name === "string") {
        const p = people?.find((x) => url === `/household/people/${x.id}`);
        if (p) p.name = body.name;
      }
      if (url.startsWith("/household/people/")) return method === "DELETE" ? new Response(null, { status: 204 }) : ok({ ...SAM, ...body });
      if (url.endsWith("/assignee")) {
        due = { ...due, assignee: body.person_id };
        return ok(due);
      }
      if (url.endsWith("/changes")) return ok([]);
      if (url === "/due") return ok([due]);
      if (url.endsWith("/card")) return ok({ jobs: [] });
      if (url === "/appliances") return ok([{ id: "a1", alias: "dishwasher", kind: "dishwasher", brand: "bosch", model: "X", plate_confirmed: true, card_status: "checked" }]);
      return ok({}, 404);
    }) as typeof fetch;
    return { f, sent, replies, people, assign: (id: string | null) => void (due = { ...due, assignee: id }) };
  }

  const tick = () => new Promise((r) => setTimeout(r, 0));
  async function settle(run: (t: number) => void, t = 0): Promise<void> {
    for (let i = 0; i < 6; i++) await tick();
    run(t);
  }
  async function open(people: Record<string, unknown>[] | null = [ANA, SAM], over: Partial<Deps> = {}) {
    const srv = server(people);
    const pushed: string[] = [];
    const went: string[] = [];
    const store = tokens("tok");
    const { d, run } = deps({ search: "", fetch: srv.f, household: store, pushHash: (h) => void pushed.push(h), navigate: (x) => void went.push(x), ...over });
    const app = new App(d);
    await app.boot();
    await settle(run);
    return { app, run, srv, pushed, went, store };
  }
  const pp = (app: App) => app.els.people;
  const tapIn = (root: HTMLElement, act: string, person?: string) =>
    (root.querySelector(`[data-act="${act}"]${person ? `[data-person="${person}"]` : ""}`) as HTMLButtonElement).click();
  const change = (el: HTMLInputElement | HTMLSelectElement, v: string | boolean) => {
    if (typeof v === "boolean") (el as HTMLInputElement).checked = v;
    else el.value = v;
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  };

  it("a server without people keeps the app as it was: no People link, no Who does it, delete offered", async () => {
    const { app, run } = await open(null);
    expect((document.getElementById("people-link") as HTMLElement).hidden).toBe(true);
    (app.els.homeList.querySelector('[data-act="open-sheet"]') as HTMLButtonElement).click();
    await settle(run, 16);
    expect(app.els.sheet.querySelector(".who")).toBeNull();
    expect((document.getElementById("delete-household") as HTMLElement).hidden).toBe(false);
  });

  it("the first invite asks the inviter's name while the server still calls them Admin, and sets it before the link", async () => {
    const { app, run, srv } = await open([{ ...ANA, name: "Admin" }, SAM]);
    (document.getElementById("people-link") as HTMLButtonElement).click();
    await settle(run, 16);
    tapIn(pp(app), "invite-open");
    run(32);
    const mine = pp(app).querySelector('[data-pf="invite-myname"]') as HTMLInputElement;
    expect(mine).not.toBeNull();
    // No name, no invite: the form says why and sends nothing.
    tapIn(pp(app), "invite-make");
    await settle(run, 48);
    expect(pp(app).querySelector(".error")?.textContent).toBe(PEOPLE_TEXT.name);
    expect(srv.sent).toEqual([]);
    const field = pp(app).querySelector('[data-pf="invite-myname"]') as HTMLInputElement;
    field.value = " Ana ";
    field.dispatchEvent(new Event("input", { bubbles: true }));
    tapIn(pp(app), "invite-make");
    await settle(run, 64);
    await settle(run, 80);
    expect(srv.sent.map((x) => `${x.method} ${x.url}`)).toEqual(["PATCH /household/people/p1", "POST /household/invites"]);
    expect(srv.sent[0]!.body).toEqual({ name: "Ana" });
    expect(pp(app).querySelector(".invite-url")?.textContent).toBe("https://fil.example:8443/#join/c0de");
    // The next invite doesn't ask again.
    tapIn(pp(app), "invite-again");
    run(96);
    expect(pp(app).querySelector('[data-pf="invite-myname"]')).toBeNull();
  });

  it("another screen opens at its top, not where the home list was scrolled to", async () => {
    const { app, run } = await open();
    Object.defineProperty(app.els.root, "scrollTop", { value: 300, writable: true });
    (document.getElementById("people-link") as HTMLButtonElement).click();
    await settle(run, 16);
    expect(pp(app).hidden).toBe(false);
    expect(app.els.root.scrollTop).toBe(0);
  });

  it("an admin opens People and alerts, makes an invite link to share or copy, changes a role and removes someone", async () => {
    const copied: string[] = [];
    const { app, run, srv, pushed } = await open(undefined, { copy: async (t) => void copied.push(t) });
    const link = document.getElementById("people-link") as HTMLButtonElement;
    expect(link.hidden).toBe(false);
    link.click();
    await settle(run, 16);
    expect(pushed).toEqual(["people"]);
    expect(pp(app).hidden).toBe(false);
    expect(app.els.home.hidden).toBe(true);
    expect(pp(app).querySelector("h1")?.textContent).toBe("Household");
    expect([...pp(app).querySelectorAll(".person strong")].map((x) => x.textContent)).toEqual(["Ana", "Sam"]);

    tapIn(pp(app), "invite-open");
    run(32);
    change(pp(app).querySelector('[data-pf="invite-role"]') as HTMLSelectElement, "viewer");
    run(48);
    expect(pp(app).querySelector(".role-help")?.textContent).toMatch(/Changes nothing\.$/);
    const mail = pp(app).querySelector('[data-pf="invite-email"]') as HTMLInputElement;
    mail.value = "sam@example.org";
    mail.dispatchEvent(new Event("input", { bubbles: true }));
    tapIn(pp(app), "invite-make");
    await settle(run, 64);
    expect(srv.sent.at(-1)).toMatchObject({ method: "POST", url: "/household/invites", body: { role: "member", view_only: true, email: "sam@example.org", expires_in_days: 7 } });
    expect(pp(app).querySelector(".invite-url")?.textContent).toBe("https://fil.example:8443/#join/c0de");
    expect(pp(app).querySelector('[data-act="invite-mail"]')?.textContent).toBe("Email it to sam@example.org");
    tapIn(pp(app), "invite-copy");
    await settle(run, 80);
    expect(copied).toEqual(["https://fil.example:8443/#join/c0de"]);
    expect(pp(app).querySelector('[data-act="invite-copy"]')?.textContent).toBe("Link copied");

    change(pp(app).querySelector('select[data-pf="role"][data-person="p2"]') as HTMLSelectElement, "admin");
    await settle(run, 96);
    expect(srv.sent.at(-1)).toMatchObject({ method: "PATCH", url: "/household/people/p2", body: { role: "admin", view_only: false } });

    // The last admin can't be demoted: the server's 409 says why, and the screen keeps the role it has.
    srv.replies["PATCH /household/people/p2"] = [409, { error: "last_admin" }];
    change(pp(app).querySelector('select[data-pf="role"][data-person="p2"]') as HTMLSelectElement, "viewer");
    await settle(run, 112);
    expect(pp(app).querySelector(".error")?.textContent).toBe(PEOPLE_TEXT.lastAdmin);
    expect((pp(app).querySelector('select[data-pf="role"][data-person="p2"]') as HTMLSelectElement).value).toBe("member");

    tapIn(pp(app), "people-remove", "p2");
    run(128);
    expect(pp(app).textContent).toContain("Remove Sam?");
    tapIn(pp(app), "people-remove-yes", "p2");
    await settle(run, 144);
    expect(srv.sent.at(-1)).toMatchObject({ method: "DELETE", url: "/household/people/p2" });

    // Household is a tab: Home is the way back.
    (document.getElementById("home-tab") as HTMLButtonElement).click();
    run(160);
    expect(pp(app).hidden).toBe(true);
    expect(app.els.home.hidden).toBe(false);
  });

  it("the People list is read again when its tab is tapped or the app comes back, so a new joiner shows", async () => {
    const { app, run, srv } = await open();
    const folk = srv.people!;
    const link = document.getElementById("people-link") as HTMLButtonElement;
    link.click();
    await settle(run, 16);
    const names = () => [...pp(app).querySelectorAll(".person strong")].map((x) => x.textContent);
    expect(names()).toEqual(["Ana", "Sam"]);

    folk.push({ ...SAM, id: "p3", name: "Kim", me: false });
    document.dispatchEvent(new Event("visibilitychange"));
    await settle(run, 32);
    expect(names()).toEqual(["Ana", "Kim", "Sam"]);

    folk.push({ ...SAM, id: "p4", name: "Lou", me: false });
    link.click();
    await settle(run, 48);
    expect(names()).toEqual(["Ana", "Kim", "Lou", "Sam"]);
  });

  it("is quiet for a new build's reload only while nothing typed is waiting to be sent", async () => {
    const { app, run } = await open();
    expect(app.quiet()).toBe(true);
    (document.getElementById("people-link") as HTMLButtonElement).click();
    await settle(run, 16);
    tapIn(pp(app), "invite-open");
    run(32);
    const mail = pp(app).querySelector('[data-pf="invite-email"]') as HTMLInputElement;
    mail.value = "sam@example.org";
    mail.dispatchEvent(new Event("input", { bubbles: true }));
    expect(app.quiet()).toBe(false);
  });

  it("three tabs and Settings get round the home, and say where you are", async () => {
    const { app, run } = await open();
    const tab = (id: string) => document.getElementById(id) as HTMLButtonElement;
    expect(app.els.tabs.hidden).toBe(false);
    expect(tab("home-tab").getAttribute("aria-current")).toBe("page");
    tab("devices-link").click();
    await settle(run, 16);
    expect(app.els.devices.hidden).toBe(false);
    expect(tab("devices-link").getAttribute("aria-current")).toBe("page");
    expect(tab("home-tab").hasAttribute("aria-current")).toBe(false);
    tab("people-link").click();
    await settle(run, 32);
    expect(pp(app).hidden).toBe(false);
    expect(tab("people-link").getAttribute("aria-current")).toBe("page");
    // Settings, from the header: the language, another phone, about and delete, under the same tabs.
    app.els.gear.click();
    run(48);
    expect(app.els.settings.hidden).toBe(false);
    expect(pp(app).hidden).toBe(true);
    expect(app.els.gear.getAttribute("aria-current")).toBe("page");
    expect(app.els.settings.textContent).toContain("Not affiliated with Bosch, Samsung, the boiler makers or Meta.");
    expect(document.getElementById("delete-household")?.closest("#settings")).not.toBeNull();
    tab("home-tab").click();
    run(64);
    expect(app.els.settings.hidden).toBe(true);
    expect(app.els.home.hidden).toBe(false);
  });

  it("your own alerts: chosen appliances and quiet hours go up as the contract has them; a bad pair is refused here", async () => {
    const { app, run, srv } = await open();
    (document.getElementById("people-link") as HTMLButtonElement).click();
    await settle(run, 16);
    change(pp(app).querySelector('[data-pf="items"][value="chosen"]') as HTMLInputElement, true);
    run(32);
    tapIn(pp(app), "alerts-save");
    run(48);
    expect(pp(app).querySelector(".alerts .error")?.textContent).toBe(PEOPLE_TEXT.follows);
    change(pp(app).querySelector('[data-pf="follow"]') as HTMLInputElement, true);
    change(pp(app).querySelector('[data-pf="quiet"]') as HTMLInputElement, true);
    run(64);
    change(pp(app).querySelector('[data-pf="quiet-from"]') as HTMLInputElement, "21:30");
    change(pp(app).querySelector('[data-pf="hub"]') as HTMLInputElement, true);
    tapIn(pp(app), "alerts-save");
    await settle(run, 80);
    expect(srv.sent.at(-1)).toEqual({
      method: "PUT",
      url: "/me/alerts",
      body: { push: true, calendar: true, hub: true, items: "chosen", follows: ["a1"], quiet: { from: "21:30", to: "07:00" } },
      auth: "Bearer tok",
    });
    expect(pp(app).querySelector(".alerts .note")?.textContent).toBe(PEOPLE_TEXT.saved);
  });

  it("a job is given to one person from its sheet, and home says who it is for", async () => {
    const { app, run, srv } = await open();
    (app.els.homeList.querySelector('[data-act="open-sheet"]') as HTMLButtonElement).click();
    await settle(run, 16);
    (app.els.sheet.querySelector('[data-act="sheet-more"]') as HTMLButtonElement).click();
    run(20);
    expect(app.els.sheet.querySelector(".who > p.sub")?.textContent).toBe("Everyone who follows this appliance is reminded.");
    change(app.els.sheet.querySelector('[data-field="assignee"]') as HTMLSelectElement, "p2");
    await settle(run, 32);
    // One PUT on change, none for the input event before it.
    expect(srv.sent.filter((x) => x.url.endsWith("/assignee"))).toEqual([{ method: "PUT", url: "/appliances/a1/jobs/clean_filters/assignee", body: { person_id: "p2" }, auth: "Bearer tok" }]);
    expect(app.els.sheet.querySelector(".who > p.sub")?.textContent).toBe("Only Sam is reminded.");
    (app.els.sheet.querySelector('[data-act="sheet-close"]') as HTMLButtonElement).click();
    run(48);
    expect(app.els.homeList.textContent).toContain("for Sam");
  });

  it("a view-only person reads everything but is offered no Start, no writes and no delete; a refused hello says why", async () => {
    const sock = new Sock();
    const { app, run, srv, went } = await open([{ ...ANA, me: false }, { ...SAM, view_only: true, me: true }], { openSocket: () => sock });
    expect(app.els.homeList.querySelector('[data-act="start-job"]')).toBeNull();
    expect((document.getElementById("delete-household") as HTMLElement).hidden).toBe(true);
    (app.els.homeList.querySelector('[data-act="open-sheet"]') as HTMLButtonElement).click();
    await settle(run, 16);
    expect(app.els.sheet.querySelector('[data-field="assignee"]')).toBeNull();
    expect(app.els.sheet.querySelector('[data-act="sheet-done-ask"]')).toBeNull();
    expect(app.els.sheet.querySelector('[data-act="start-job"]')).toBeNull();
    expect(srv.sent).toEqual([]);
    // A start-job link elsewhere does nothing for a viewer.
    const b = document.createElement("button");
    b.dataset.act = "start-job";
    b.dataset.appliance = "a1";
    b.dataset.job = "clean_filters";
    app.els.root.append(b);
    b.click();
    expect(went).toEqual([]);

    await app.startLive();
    sock.onopen?.({});
    sock.onclose?.({ code: 1008 });
    await settle(run, 32);
    expect(app.els.root.textContent).toContain(PEOPLE_TEXT.viewerSession);
  });

  it("leaving the home forgets it on this phone and goes back to the start", async () => {
    const { app, run, srv, went, store } = await open([{ ...ANA, me: false }, { ...SAM, me: true }]);
    (document.getElementById("people-link") as HTMLButtonElement).click();
    await settle(run, 16);
    expect(pp(app).querySelector('[data-act="invite-open"]')).toBeNull();
    tapIn(pp(app), "people-leave", "p2");
    run(32);
    tapIn(pp(app), "people-remove-yes", "p2");
    await settle(run, 48);
    expect(srv.sent.at(-1)).toMatchObject({ method: "DELETE", url: "/household/people/p2" });
    expect(store.now()).toBeNull();
    expect(went).toEqual(["?start"]);
  });

  describe("a join link (#join/<code>)", () => {
    async function join(store = tokens(null)) {
      const srv = server();
      let cleared = 0;
      const { d, run } = deps({ search: "", hash: "#join/c0de", fetch: srv.f, household: store, firstRunSeen: () => false, clearDoor: () => void cleared++ });
      const app = new App(d);
      await app.boot();
      run(0);
      return { app, run, srv, store, cleared: () => cleared };
    }

    it("takes the code out of the address, asks consent, then a name, and keeps the token it gets", async () => {
      const { app, run, srv, store, cleared } = await join();
      expect(cleared()).toBe(1);
      expect(app.els.firstRun.hidden).toBe(false);
      expect(app.els.join.hidden).toBe(true);
      // No tick, no name asked.
      expect((app.els.firstRun.querySelector('[data-act="accept-first-run"]') as HTMLButtonElement).disabled).toBe(true);
      app.els.consentBox.click();
      run(8);
      (app.els.firstRun.querySelector('[data-act="accept-first-run"]') as HTMLButtonElement).click();
      run(12);
      expect(app.els.firstRun.hidden).toBe(true);
      expect(app.els.join.hidden).toBe(false);
      expect(app.els.join.querySelector("h1")?.textContent).toBe("Join a home");

      // An empty name is refused here; the server's 409 name_taken says pick another.
      tapIn(app.els.join, "join-go");
      run(16);
      expect(app.els.join.querySelector(".error")).not.toBeNull();
      expect(srv.sent).toEqual([]);
      const name = app.els.join.querySelector('[data-pf="join-name"]') as HTMLInputElement;
      name.value = " Sam ";
      name.dispatchEvent(new Event("input", { bubbles: true }));
      srv.replies["POST /household/join"] = [409, { error: "name_taken" }];
      tapIn(app.els.join, "join-go");
      await settle(run, 20);
      expect(app.els.join.querySelector(".error")?.textContent).toBe(PEOPLE_TEXT.nameTaken);

      delete srv.replies["POST /household/join"];
      // Enter on the keyboard joins, as the button does.
      (app.els.join.querySelector('[data-pf="join-name"]') as HTMLInputElement).dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
      await settle(run, 24);
      expect(srv.sent.at(-1)).toEqual({ method: "POST", url: "/household/join", body: { code: "c0de", name: "Sam" }, auth: null });
      expect(store.now()).toBe("tok-sam");
      expect(app.els.join.hidden).toBe(true);
      expect(app.els.home.hidden).toBe(false);
    });

    it("a join link tapped while the app is open is taken out of the address too", async () => {
      const srv = server();
      let cleared = 0;
      const { d, run } = deps({ search: "", fetch: srv.f, household: tokens("tok"), clearDoor: () => void cleared++ });
      const app = new App(d);
      await app.boot();
      run(0);
      expect(cleared).toBe(0);
      app.route("#join/c0de");
      run(4);
      expect(cleared).toBe(1);
      expect(app.els.join.hidden).toBe(false);
      expect(app.els.home.hidden).toBe(true);
    });

    it("a used or expired invite says to ask for a new one", async () => {
      const { app, run, srv } = await join(tokens("tok-old"));
      expect(app.els.firstRun.hidden).toBe(true);
      // This phone already opens a home: joining moves it, and it says so first.
      expect(app.els.join.textContent).toContain(PEOPLE_JOIN_TEXT.switching);
      const name = app.els.join.querySelector('[data-pf="join-name"]') as HTMLInputElement;
      name.value = "Sam";
      name.dispatchEvent(new Event("input", { bubbles: true }));
      srv.replies["POST /household/join"] = [403, { error: "forbidden" }];
      tapIn(app.els.join, "join-go");
      await settle(run, 8);
      expect(app.els.join.querySelector(".error")?.textContent).toBe(PEOPLE_JOIN_TEXT.bad);
    });
  });
});

describe("connections", () => {
  const ANA = { id: "p1", name: "Ana", role: "admin", view_only: false, me: true };
  const HA = { id: "t1", by_kind: "ha", scope: "write", label: "Home Assistant", created_at: "2026-10-20T09:00:00Z", last_used_at: "2026-10-26T08:00:00Z" };

  /** A home whose admin may see its keys; tokens: null answers 403 (anyone but an admin). */
  function server(tokens: Record<string, unknown>[] | null = [HA], people: Record<string, unknown>[] = [ANA]) {
    const sent: { method: string; url: string; body: unknown }[] = [];
    let keys = tokens ? [...tokens] : null;
    const f = (async (u: string, init?: RequestInit) => {
      const url = String(u).replace(/^\/api\/v1/, "");
      const method = init?.method ?? "GET";
      const body = init?.body ? JSON.parse(String(init.body)) : null;
      if (method !== "GET") sent.push({ method, url, body });
      const ok = (x: unknown, status = 200) => new Response(JSON.stringify(x), { status });
      if (url === "/household/people") return ok(people);
      if (url === "/household/feeds") return ok({ ics_url: "https://fil.example:8443/api/v1/feeds/h1.p1.sig.ics" });
      if (url === "/household/tokens" && method === "GET") return keys ? ok(keys) : ok({ error: "forbidden" }, 403);
      if (url === "/household/tokens" && method === "POST") {
        keys = [...(keys ?? []), { id: "t2", by_kind: body.by_kind, scope: body.scope, label: body.label, created_at: "2026-10-27T09:00:00Z", last_used_at: null }];
        return ok({ id: "t2", token: "sekrit-key-1" }, 201);
      }
      if (url.startsWith("/household/tokens/") && method === "DELETE") {
        keys = (keys ?? []).filter((k) => `/household/tokens/${k.id}` !== url);
        return new Response(null, { status: 204 });
      }
      if (url === "/due" || url === "/appliances") return ok([]);
      return ok({}, 404);
    }) as typeof fetch;
    return { f, sent };
  }

  const tick = () => new Promise((r) => setTimeout(r, 0));
  async function settle(run: (t: number) => void, t = 0): Promise<void> {
    for (let i = 0; i < 6; i++) await tick();
    run(t);
  }
  async function open(tokens?: Record<string, unknown>[] | null, people?: Record<string, unknown>[]) {
    const srv = server(tokens, people);
    const pushed: string[] = [];
    const copied: string[] = [];
    const { d, run } = deps({ search: "", fetch: srv.f, household: tokens_("tok"), pushHash: (h) => void pushed.push(h), copy: async (t) => void copied.push(t) });
    const app = new App(d);
    await app.boot();
    await settle(run);
    (document.getElementById("conn-link") as HTMLButtonElement).click();
    await settle(run, 16);
    return { app, run, srv, pushed, copied };
  }
  const tokens_ = tokens;
  const cn = (app: App) => app.els.conn;
  const tap = (app: App, act: string) => (cn(app).querySelector(`[data-act="${act}"]`) as HTMLButtonElement).click();
  const pick = (app: App, field: string, value: string) => {
    const i = cn(app).querySelector(`[data-cf="${field}"][value="${value}"]`) as HTMLInputElement;
    i.checked = true;
    i.dispatchEvent(new Event("change", { bubbles: true }));
  };

  it("an admin sees the keys without their secrets, adds one for an agent, sees its key once, and removes one", async () => {
    const { app, run, srv, pushed, copied } = await open();
    expect(pushed).toEqual(["connections"]);
    expect(cn(app).hidden).toBe(false);
    expect(app.els.home.hidden).toBe(true);
    expect(cn(app).querySelector(".conn strong")?.textContent).toBe("Home Assistant");
    expect(cn(app).querySelector(".conn .sub")?.textContent).toBe("Can look and change the schedule · added 20 Oct · last used 26 Oct");

    tap(app, "conn-new");
    run(32);
    // Home Assistant first, with a key that can write (its Done button needs one); an agent starts with one that only looks.
    expect((cn(app).querySelector('[data-cf="scope"][value="write"]') as HTMLInputElement).checked).toBe(true);
    pick(app, "kind", "agent");
    run(48);
    expect((cn(app).querySelector('[data-cf="scope"][value="read"]') as HTMLInputElement).checked).toBe(true);
    expect((cn(app).querySelector('[data-cf="label"]') as HTMLInputElement).value).toBe("AI agent");
    const label = cn(app).querySelector('[data-cf="label"]') as HTMLInputElement;
    label.value = "  ";
    label.dispatchEvent(new Event("input", { bubbles: true }));
    tap(app, "conn-make");
    run(64);
    expect(cn(app).querySelector(".error")?.textContent).toBe("Give it a name of 1 to 60 characters.");
    expect(srv.sent).toEqual([]);
    const label2 = cn(app).querySelector('[data-cf="label"]') as HTMLInputElement;
    label2.value = "My agent";
    label2.dispatchEvent(new Event("input", { bubbles: true }));
    label2.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    await settle(run, 80);
    expect(srv.sent).toEqual([{ method: "POST", url: "/household/tokens", body: { by_kind: "agent", scope: "read", label: "My agent" } }]);
    // Shown once, with where it goes.
    const made = cn(app).querySelector(".conn-made") as HTMLElement;
    expect(made.textContent).toContain("sekrit-key-1");
    expect(made.textContent).toContain("https://fil.example:8443/mcp");
    tap(app, "conn-copy-key");
    await settle(run, 96);
    expect(copied).toEqual(["sekrit-key-1"]);
    expect(cn(app).querySelector('[data-act="conn-copy-key"]')?.textContent).toBe("Copied.");
    expect([...cn(app).querySelectorAll(".conn strong")].map((x) => x.textContent)).toEqual(["Home Assistant", "My agent"]);
    tap(app, "conn-done");
    run(112);
    expect(cn(app).textContent).not.toContain("sekrit-key-1");

    (cn(app).querySelector('[data-act="conn-remove"][data-conn="t1"]') as HTMLButtonElement).click();
    run(128);
    expect(cn(app).textContent).toContain("Remove Home Assistant? It stops working at once.");
    tap(app, "conn-remove-yes");
    await settle(run, 144);
    expect(srv.sent.at(-1)).toEqual({ method: "DELETE", url: "/household/tokens/t1", body: null });
    expect([...cn(app).querySelectorAll(".conn strong")].map((x) => x.textContent)).toEqual(["My agent"]);

    // Connections sits under Household, and goes back to it.
    tap(app, "conn-close");
    run(160);
    expect(cn(app).hidden).toBe(true);
    expect(pushed.at(-1)).toBe("people");
    expect((document.getElementById("people") as HTMLElement).hidden).toBe(false);
  });

  it("a key made and left on screen is gone once the screen closes", async () => {
    const { app, run } = await open();
    tap(app, "conn-new");
    run(32);
    tap(app, "conn-make");
    await settle(run, 48);
    expect(cn(app).textContent).toContain("sekrit-key-1");
    expect(cn(app).textContent).toContain("Settings, Devices & services");
    app.route("");
    run(64);
    app.route("#connections");
    await settle(run, 80);
    expect(cn(app).textContent).not.toContain("sekrit-key-1");
  });

  it("anyone else gets their own calendar address, hidden until asked, and no keys", async () => {
    const { app, run, copied } = await open(null, [{ ...ANA, role: "member" }]);
    expect(cn(app).querySelector(".conn-keys")).toBeNull();
    expect(cn(app).querySelector(".error")).toBeNull();
    expect(cn(app).textContent).not.toContain("feeds/");
    tap(app, "conn-feed-show");
    run(32);
    expect(cn(app).textContent).toContain("https://fil.example:8443/api/v1/feeds/h1.p1.sig.ics");
    tap(app, "conn-feed-copy");
    await settle(run, 48);
    expect(copied).toEqual(["https://fil.example:8443/api/v1/feeds/h1.p1.sig.ics"]);
  });
});

describe("saved timings (?debug=1)", () => {
  it("saves this run's timings as a file: frame ids and milliseconds, never the token", async () => {
    const blobs: Blob[] = [];
    const { d } = deps({ search: "?debug=1", household: tokens("tok-secret"), objectUrl: { make: (b) => (blobs.push(b), "blob:x"), drop: () => undefined } });
    const app = new App(d);
    await app.boot();
    app.metrics.pendingStop = { frameId: "fA", captureMs: 10 };
    app.metrics.painted(70, true);
    app.saveTimings();
    expect(blobs).toHaveLength(1);
    const text = await new Promise<string>((ok) => {
      const r = new FileReader();
      r.onload = () => ok(String(r.result));
      r.readAsText(blobs[0]!);
    });
    const run = JSON.parse(text);
    expect(run.stop_frames).toEqual([{ frame_id: "fA", ms: 60 }]);
    expect(run).toHaveProperty("at");
    expect(text).not.toContain("tok-secret");
  });
});

describe("the feedback form", () => {
  /** A home's server: pilot says whether GET /household counts it in the pilot; posted forms are kept. */
  function server(pilot: boolean | undefined, status = 201) {
    const sent: unknown[] = [];
    const f = (async (u: string, init?: RequestInit) => {
      const url = String(u).replace(/^\/api\/v1/, "");
      const ok = (x: unknown, st = 200) => new Response(JSON.stringify(x), { status: st });
      if (url === "/household") return ok({ id: "h1", country: "NL", lang: "nl", cap_usd_day: 1, name: null, renter: null, ...(pilot === undefined ? {} : { pilot }) });
      if (url === "/household/feedback" && init?.method === "POST") {
        sent.push(JSON.parse(String(init.body)));
        return status === 201 ? ok({ id: "fb-0123456789ab" }, 201) : ok({ error: "x" }, status);
      }
      if (url === "/due" || url === "/appliances") return ok([]);
      return ok({}, 404);
    }) as typeof fetch;
    return { f, sent };
  }
  const tick = () => new Promise((r) => setTimeout(r, 0));
  async function settle(run: (t: number) => void, t = 0): Promise<void> {
    for (let i = 0; i < 6; i++) await tick();
    run(t);
  }
  async function finish(pilot: boolean | undefined, replay = false) {
    const srv = server(pilot);
    const went: string[] = [];
    const r = await live(() => [], { search: "?appliance=a1&job=j", fetch: srv.f, household: tokens("tok"), navigate: (s) => void went.push(s) });
    if (replay) (r.app.s as { mode: string }).mode = "replay";
    const v = { check_id: "c", attempt: 1, judge: "done", clauses: [], result: "done" } as const;
    r.app.onMessage({ type: "step", seq: 1, t_ms: 1, job_id: "j", step_id: "a", index: 0, of: 1, text: "One.", page: 43, view: "front", power: "any" });
    r.app.onMessage({ type: "verdict", seq: 2, t_ms: 2, verdict: { ...v, step_id: "a" } } as never);
    await settle(r.run, 16);
    return { ...r, went };
  }
  async function form(pilot: boolean | undefined, lang = "en", status = 201) {
    const srv = server(pilot, status);
    const pushed: string[] = [];
    const { d, run } = deps({ search: "?feedback", lang, fetch: srv.f, household: tokens("tok"), pushHash: (h) => void pushed.push(h) });
    const app = new App(d);
    await app.boot();
    await settle(run, 16);
    return { app, run, srv, pushed };
  }
  const fb = (app: App) => app.els.fb;
  const tap = (app: App, act: string, extra = "") => (fb(app).querySelector(`[data-act="${act}"]${extra}`) as HTMLButtonElement).click();
  const type = (app: App, key: string, text: string) => {
    const t = fb(app).querySelector(`[data-fb="${key}"]`) as HTMLInputElement;
    t.value = text;
    t.dispatchEvent(new Event("input", { bubbles: true }));
  };

  it("Job done asks a pilot home what it thought, and opens the form on a fresh page", async () => {
    const r = await finish(true);
    expect(r.app.els.fbOffer.hidden).toBe(false);
    expect(r.app.els.fbOffer.textContent).toContain("What did you think?");
    (r.app.els.fbOffer.querySelector('[data-act="fb-open"]') as HTMLButtonElement).click();
    r.run(32);
    expect(r.went).toEqual(["?feedback"]);
    expect(r.app.els.fbOffer.hidden).toBe(true);
  });

  it("never asks a home outside the pilot, one the server says nothing about, or in a replay; Not now puts it away", async () => {
    expect((await finish(false)).app.els.fbOffer.hidden).toBe(true);
    expect((await finish(undefined)).app.els.fbOffer.hidden).toBe(true);
    expect((await finish(true, true)).app.els.fbOffer.hidden).toBe(true);
    const r = await finish(true);
    (r.app.els.fbOffer.querySelector('[data-act="fb-later"]') as HTMLButtonElement).click();
    r.run(32);
    expect(r.app.els.fbOffer.hidden).toBe(true);
  });

  it("a home outside the pilot that opens ?feedback lands home, with no form", async () => {
    const { app, pushed } = await form(false);
    expect(fb(app).hidden).toBe(true);
    expect(pushed.at(-1)).toBe("");
  });

  it("every question may be skipped: nothing answered sends nulls and no quote", async () => {
    const { app, run, srv } = await form(true);
    expect(fb(app).hidden).toBe(false);
    expect(fb(app).querySelector("h1")?.textContent).toBe("What did you think?");
    tap(app, "fb-send");
    await settle(run, 32);
    expect(srv.sent).toEqual([
      {
        lang: "en",
        answers: { would_use: null, would_use_why: null, practical: null, hard: null, unsafe: null, unsafe_which: null, monthly: null, friend: null },
        quote: "none",
        quote_name: null,
      },
    ]);
    expect(fb(app).querySelector("h1")?.textContent).toBe("Thank you");
    tap(app, "fb-close");
    run(48);
    expect(fb(app).hidden).toBe(true);
  });

  it("one answer per tick-box question, taken back by tapping it again; written answers go as typed", async () => {
    const { app, run, srv } = await form(true, "nl");
    expect(fb(app).querySelector("h1")?.textContent).toBe("Wat vond je ervan?");
    tap(app, "fb-pick", '[data-q="would_use"][data-v="yes"]');
    run(32);
    tap(app, "fb-pick", '[data-q="would_use"][data-v="maybe"]');
    run(48);
    tap(app, "fb-pick", '[data-q="practical"][data-v="partly"]');
    run(64);
    tap(app, "fb-pick", '[data-q="practical"][data-v="partly"]');
    run(80);
    const pressed = [...fb(app).querySelectorAll('[aria-pressed="true"]')].map((b) => (b as HTMLElement).dataset.v);
    expect(pressed).toEqual(["maybe"]);
    type(app, "hard", "  De rode lijn \n");
    type(app, "friend", "   ");
    tap(app, "fb-send");
    await settle(run, 96);
    const sent = srv.sent[0] as { lang: string; answers: Record<string, unknown> };
    expect(sent.lang).toBe("nl");
    expect(sent.answers).toMatchObject({ would_use: "maybe", practical: null, hard: "  De rode lijn \n", friend: null });
    expect(fb(app).textContent).toContain("Dank je wel");
  });

  it("quoting by name needs the name; the name goes only with that choice", async () => {
    const { app, run, srv } = await form(true);
    const pick = (v: string) => {
      const i = fb(app).querySelector(`[data-fb="quote"][value="${v}"]`) as HTMLInputElement;
      i.checked = true;
      i.dispatchEvent(new Event("change", { bubbles: true }));
      run(32);
    };
    expect((fb(app).querySelector('[data-fb="quote"][value="none"]') as HTMLInputElement).checked).toBe(true);
    pick("named");
    tap(app, "fb-send");
    await settle(run, 48);
    expect(srv.sent).toEqual([]);
    expect(fb(app).querySelector(".error")?.textContent).toBe("Write the name to quote you by, or choose another option.");
    type(app, "quote_name", "Ana");
    tap(app, "fb-send");
    await settle(run, 64);
    expect(srv.sent.at(-1)).toMatchObject({ quote: "named", quote_name: "Ana" });
    expect(fb(app).textContent).toContain("pilot@craftycast.com");
  });

  it("a refusal keeps the answers and says why", async () => {
    const { app, run } = await form(true, "en", 503);
    type(app, "monthly", "A reminder");
    tap(app, "fb-send");
    await settle(run, 32);
    expect(fb(app).querySelector(".error")?.textContent).toBe("Couldn't send just now. Your answers are still here; try again.");
    expect((fb(app).querySelector('[data-fb="monthly"]') as HTMLTextAreaElement).value).toBe("A reminder");
  });
});

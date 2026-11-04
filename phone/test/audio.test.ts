import { describe, expect, it } from "vitest";
import { TURN, Turns, micErrorText } from "../src/audio.ts";

describe("micErrorText", () => {
  it("names the cause in the phone's words", () => {
    expect(micErrorText({ name: "NotAllowedError" })).toContain("blocked");
    expect(micErrorText({ name: "NoSecureContext" })).toContain("https");
    expect(micErrorText(new Error("x"))).toContain("didn't start");
  });
});

describe("Turns", () => {
  const rate = 16000;
  const chunk = (amp: number, ms = 100) => new Float32Array((rate * ms) / 1000).fill(amp);
  const run = (t: Turns, parts: [number, number][]) => {
    const out: Float32Array[] = [];
    for (const [amp, ms] of parts) for (let i = 0; i < ms / 100; i++) {
      const r = t.feed(chunk(i % 2 ? amp : -amp));
      if (r) out.push(r);
    }
    return out;
  };

  it("a spoken turn starts after a moment of speech and ends after a pause, with its lead-in", () => {
    const out = run(new Turns(rate), [[0.001, 1000], [0.2, 1500], [0.001, 1000]]);
    expect(out).toHaveLength(1);
    // the speech, the silence that ended it, and a little before the start
    expect(out[0]!.length / rate).toBeGreaterThan(1.5 + TURN.endMs / 1000 - 0.1);
  });

  it("a clatter under the floor's start time is no turn; a turn is cut at the cap", () => {
    expect(run(new Turns(rate), [[0.001, 1000], [0.3, 200], [0.001, 2000]])).toHaveLength(0);
    const long = run(new Turns(rate), [[0.001, 500], [0.2, TURN.maxMs + 2000]]);
    expect(long.length).toBeGreaterThanOrEqual(1);
    expect(long[0]!.length / rate).toBeLessThanOrEqual(TURN.maxMs / 1000 + 0.5);
  });

  it("reset drops a turn that was building (the phone itself spoke)", () => {
    const t = new Turns(rate);
    run(t, [[0.001, 500], [0.2, 600]]);
    t.reset();
    expect(run(t, [[0.001, 1000]])).toHaveLength(0);
  });
});

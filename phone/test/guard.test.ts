import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { Guard, type FrameIn, type ZoneNow } from "../src/guard.ts";

const C = JSON.parse(readFileSync(fileURLToPath(new URL("../../contracts/examples/g1/example.json", import.meta.url)), "utf8")).constants;
const ZONE: ZoneNow = {
  zoneId: "z1",
  label: "gas tap",
  polygon: [
    { x: 500, y: 300 },
    { x: 540, y: 300 },
    { x: 540, y: 340 },
    { x: 500, y: 340 },
  ],
  ringCm: 3,
  ringStep: false,
  scaleCmPerPx: 0.1,
  toolLenCm: 0,
  testZone: false,
};
const frame = (t: number, x: number | null, extra: Partial<FrameIn> = {}): FrameIn => ({
  frameId: `f${t}`,
  tMs: t,
  ageMs: 10,
  points: x === null ? [] : [{ x, y: 320 }],
  trackerConfident: true,
  zones: [ZONE],
  ...extra,
});

describe("Guard", () => {
  it("latches a stop, sends one g1 message, and only code clears it", () => {
    const sent: unknown[] = [];
    const g = new Guard(C, (m) => sent.push(m));
    expect(g.frame(frame(0, 100), true).kind).toBe("clear");
    expect(g.frame(frame(33, 400), true).kind).toBe("stop"); // 7 cm out
    expect(g.frame(frame(66, 100), true).kind).toBe("stop"); // hand back: still latched
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ type: "g1", kind: "stop", zone_id: "z1", frame_id: "f33" });
    g.serverCleared();
    expect(g.frame(frame(99, 100), true).kind).toBe("clear");
  });

  it("offline, a stop clears only after the hand stays clear for 2 s", () => {
    const g = new Guard(C, () => undefined);
    g.frame(frame(0, 400), false);
    expect(g.frame(frame(1000, 0), false).kind).toBe("stop");
    expect(g.frame(frame(2500, 0), false).kind).toBe("stop");
    expect(g.frame(frame(3100, 0), false).kind).toBe("clear");
  });

  it("losing the zone fails closed", () => {
    const sent: { kind: string }[] = [];
    const g = new Guard(C, (m) => sent.push(m));
    const s = g.frame(frame(0, 100, { zones: [{ ...ZONE, polygon: null }] }), true);
    expect(s).toMatchObject({ kind: "pause", why: "zone_lost", label: "gas tap" });
    expect(g.frame(frame(33, 100, { trackerConfident: false }), true).kind).toBe("pause");
    expect(sent).toHaveLength(1);
    expect(g.frame(frame(66, 100), true).kind).toBe("clear");
  });

  it("a lost hand pauses until the hand is back", () => {
    const g = new Guard(C, () => undefined);
    g.frame(frame(0, 200), true); // 27 cm out
    expect(g.frame(frame(33, null), true)).toMatchObject({ kind: "pause", why: "hand_lost" });
    expect(g.frame(frame(66, null), true)).toMatchObject({ kind: "pause", why: "hand_lost" });
    expect(g.frame(frame(99, 100), true).kind).toBe("clear");
  });

  it("a stale frame pauses", () => {
    const g = new Guard(C, () => undefined);
    expect(g.frame(frame(0, 100, { ageMs: 250 }), true)).toMatchObject({ kind: "pause", why: "stale_frame" });
  });
});

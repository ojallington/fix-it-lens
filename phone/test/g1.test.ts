import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import type { G1File, G1Frame, Zone } from "../../contracts/ts/src/g1.ts";
import { G1, runFrames, type Constants } from "../src/g1.ts";

// The shared vectors from rules/ when they are there; the contract's example file until then.
const RULES = fileURLToPath(new URL("../../rules/g1.json", import.meta.url));
const EXAMPLE = fileURLToPath(new URL("../../contracts/examples/g1/example.json", import.meta.url));
const file: G1File = JSON.parse(readFileSync(existsSync(RULES) ? RULES : EXAMPLE, "utf8"));
const FRAME_MS = 34;

describe(`shared G1 vectors (${existsSync(RULES) ? "rules/g1.json" : "contracts example"})`, () => {
  for (const v of file.vectors) {
    it(v.id, () => {
      const e = runFrames(file.constants, v.frames);
      expect(e?.kind ?? null).toBe(v.expect.kind);
      if (v.expect.stop_at_t_ms === null) expect(e).toBeNull();
      else expect(Math.abs(e!.t_ms - v.expect.stop_at_t_ms)).toBeLessThanOrEqual(FRAME_MS);
    });
  }
});

// Local vectors for the acceptance list, on a 10 cm square zone at 0.1 cm/px.
const C: Constants = file.constants;
const ZONE: Zone = {
  polygon: [
    { x: 1000, y: 300 },
    { x: 1100, y: 300 },
    { x: 1100, y: 400 },
    { x: 1000, y: 400 },
  ],
  ring_cm: 3,
  scale_cm_per_px: 0.1,
};

/** A fingertip moving along y=350 towards the zone's left edge (x=1000) at speed m/s, sampled at 30 fps. */
function reach(startCm: number, speedMs: number, zone: Zone = ZONE, tool = 0, n = 120): G1Frame[] {
  const frames: G1Frame[] = [];
  for (let i = 0; i < n; i++) {
    const t = i * (1000 / 30);
    const cm = startCm - speedMs * 100 * (t / 1000);
    frames.push({ t_ms: t, fingertips: [{ x: 1000 - cm / zone.scale_cm_per_px, y: 350 }], zone, tool_len_cm: tool });
  }
  return frames;
}

/** Where the fingertip is (cm from the zone's outline) when the screen turns red, given the stop path's latency. */
function cmAtRed(frames: G1Frame[], speedMs: number, pathMs: number): number {
  const e = runFrames(C, frames)!;
  const x = frames.find((f) => f.t_ms === e.t_ms)!.fingertips[0]!.x;
  return (1000 - x) * 0.1 - speedMs * 100 * (pathMs / 1000);
}

describe("G1 acceptance", () => {
  it("a 1 m/s reach is red at least 12 cm out with the 150 ms path", () => {
    const at = cmAtRed(reach(100, 1), 1, C.stop_p95_ms);
    expect(at).toBeGreaterThanOrEqual(12);
  });

  it("a fast reach stops further out than a slow one", () => {
    const fast = runFrames(C, reach(100, 1))!;
    const slow = runFrames(C, reach(60, 0.2, ZONE, 0, 300))!;
    expect(fast.reason).toBe("ttc");
    expect(slow.reason).toBe("distance");
    expect(fast.dist_cm!).toBeGreaterThan(slow.dist_cm!);
  });

  it("a ring step stops on time to contact alone, inside 20 cm", () => {
    const zone = { ...ZONE, ring_step: true };
    const e = runFrames(C, reach(30, 0.3, zone, 0, 200))!;
    expect(e.reason).toBe("ttc");
    expect(e.dist_cm!).toBeLessThan(C.dist_cm);
  });

  it("a slow hand on a ring step can work beside the ring without a stop", () => {
    const zone = { ...ZONE, ring_step: true };
    const still: G1Frame[] = [0, 33, 66, 100].map((t) => ({
      t_ms: t,
      fingertips: [{ x: 1000 - 60, y: 350 }],
      zone,
      tool_len_cm: 0,
    }));
    expect(runFrames(C, still)).toBeNull();
  });

  it("a tool in the hand grows the ring by its length", () => {
    const bare = runFrames(C, reach(100, 0.2, ZONE, 0, 400))!;
    const tool = runFrames(C, reach(100, 0.2, ZONE, 15, 400))!;
    expect(tool.t_ms).toBeLessThan(bare.t_ms);
  });

  it("covering the outline counts as touching", () => {
    const e = runFrames(C, [{ t_ms: 0, fingertips: [{ x: 1050, y: 350 }], zone: ZONE, tool_len_cm: 0 }])!;
    expect(e).toMatchObject({ kind: "stop", reason: "covered" });
  });

  it("losing the hand near the zone pauses; a hand never seen does not", () => {
    const near: G1Frame[] = [
      { t_ms: 0, fingertips: [{ x: 1000 - 300, y: 350 }], zone: ZONE, tool_len_cm: 0 },
      { t_ms: 33, fingertips: [], zone: ZONE, tool_len_cm: 0 },
    ];
    expect(runFrames(C, near)).toMatchObject({ kind: "pause", reason: "hand_lost", t_ms: 33 });
    expect(runFrames(C, [{ t_ms: 0, fingertips: [], zone: ZONE, tool_len_cm: 0 }])).toBeNull();
  });

  it("a frame older than the limit pauses, whatever the hand does", () => {
    const e = runFrames(C, [
      { t_ms: 0, frame_age_ms: C.frame_max_age_ms + 1, fingertips: [{ x: 1050, y: 350 }], zone: ZONE, tool_len_cm: 0 },
    ]);
    expect(e).toMatchObject({ kind: "pause", reason: "stale_frame" });
  });

  it("no zone ring is ever below the 3 cm floor", () => {
    const g = new G1(C);
    expect(g.ringCm(0.5, 0)).toBe(C.ring_floor_cm);
  });
});

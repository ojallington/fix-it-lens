import { describe, expect, it } from "vitest";
import type { Affine } from "../src/geom.ts";
import { Tracker } from "../src/track.ts";

const shift = (dx: number, dy: number): Affine => [1, 0, dx, 0, 1, dy];

describe("Tracker", () => {
  it("warps a late mask forward from its own frame, not from now", () => {
    const t = new Tracker();
    t.push({ frameId: "f0", delta: shift(0, 0), points: 50, inliers: 45 });
    // SAM computes on f1; the phone then moves 5 px right per frame for 18 frames (600 ms) before the answer lands.
    t.push({ frameId: "f1", delta: shift(5, 0), points: 50, inliers: 45 });
    for (let i = 2; i < 20; i++) t.push({ frameId: `f${i}`, delta: shift(5, 0), points: 50, inliers: 45 });
    const mask = [{ x: 100, y: 100 }, { x: 120, y: 100 }, { x: 110, y: 120 }];
    const now = t.warpPoly("f1", mask)!;
    expect(now[0]!.x).toBeCloseTo(100 + 18 * 5);
    expect(now[0]!.y).toBeCloseTo(100);
  });

  it("asks for a fresh anchor after the view drifts a quarter of the outline", () => {
    const t = new Tracker();
    t.push({ frameId: "a", delta: null, points: 50, inliers: 50 });
    t.anchored("a", 100);
    t.push({ frameId: "b", delta: shift(20, 0), points: 50, inliers: 50 });
    expect(t.wantsAnchor()).toBe(false);
    t.push({ frameId: "c", delta: shift(10, 0), points: 50, inliers: 50 });
    expect(t.wantsAnchor()).toBe(true);
  });

  it("is not confident with few points or few inliers", () => {
    const t = new Tracker();
    expect(t.push({ frameId: "a", delta: shift(0, 0), points: 8, inliers: 8 })).toBe(false);
    expect(t.push({ frameId: "b", delta: shift(0, 0), points: 40, inliers: 10 })).toBe(false);
    expect(t.push({ frameId: "c", delta: null, points: 40, inliers: 40 })).toBe(false);
    expect(t.push({ frameId: "d", delta: shift(0, 0), points: 40, inliers: 30 })).toBe(true);
  });

  it("scales cm per pixel as the view zooms", () => {
    const t = new Tracker();
    t.push({ frameId: "a", delta: null, points: 50, inliers: 50 });
    t.push({ frameId: "b", delta: [2, 0, 0, 0, 2, 0], points: 50, inliers: 50 });
    expect(t.scaleNow("a", 0.1)).toBeCloseTo(0.05);
  });
});

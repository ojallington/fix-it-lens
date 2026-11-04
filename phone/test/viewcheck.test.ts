import { describe, expect, it } from "vitest";
import { checkView, headingDelta, mayStart, ringWarning, type ViewInput } from "../src/viewcheck.ts";

const sq = (x: number, y: number, s: number) => [
  { x, y },
  { x: x + s, y },
  { x: x + s, y: y + s },
  { x, y: y + s },
];

const SIDE: ViewInput = {
  imgW: 1024,
  imgH: 576,
  frontHeadingDeg: 10,
  headingDeg: 100,
  zone: sq(300, 200, 40), // a gas tap 4 cm wide over 40 px
  target: sq(600, 220, 60),
  knownWidthCm: 4,
};

describe("view check", () => {
  it("a front view refuses to start a red-zone step", () => {
    const r = checkView({ ...SIDE, headingDeg: 15 });
    expect(r).toMatchObject({ ok: false, kind: "front", why: "front_view" });
    expect(mayStart({ view: "side" }, true, r)).toBe(false);
  });

  it("a side view with scale starts it", () => {
    const r = checkView(SIDE);
    expect(r.ok).toBe(true);
    expect(r.scaleCmPerPx).toBeCloseTo(0.1);
    expect(mayStart({ view: "side" }, true, r)).toBe(true);
  });

  it("refuses without both outlines whole and apart", () => {
    expect(checkView({ ...SIDE, zone: sq(1000, 200, 40) })).toMatchObject({ ok: false, why: "zone_not_seen" });
    expect(checkView({ ...SIDE, target: null })).toMatchObject({ ok: false, why: "target_not_seen" });
    expect(checkView({ ...SIDE, target: sq(310, 210, 40) })).toMatchObject({ ok: false, why: "outlines_overlap" });
    expect(checkView({ ...SIDE, knownWidthCm: null })).toMatchObject({ ok: false, why: "no_scale" });
  });

  it("refuses with no front heading on record", () => {
    expect(checkView({ ...SIDE, frontHeadingDeg: null })).toMatchObject({ ok: false, why: "no_heading" });
    expect(mayStart({ view: "side" }, true, null)).toBe(false);
  });

  it("wraps headings", () => {
    expect(headingDelta(350, 80)).toBe(90);
    expect(headingDelta(10, 350)).toBe(20);
  });

  it("a step away from any red zone needs no view check", () => {
    expect(mayStart({ view: "front" }, false, null)).toBe(true);
  });

  it("warns before a ring step", () => {
    expect(ringWarning(3, "gas tap")).toContain("3 cm around the gas tap");
  });
});

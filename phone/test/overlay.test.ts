// The frame's pan: an outline is never left under the step card or past the screen's edge.
import { describe, expect, it } from "vitest";
import { coverMap, focusPan } from "../src/overlay.ts";

// The demo replay's target on its 1024x768 frame, on the folded Fold (360x900) with the sheet from y 480 down.
const IMG = { w: 1024, h: 768 };
const FLAP = { x0: 552, y0: 520, x1: 704, y1: 584 };
const FREE = { top: 56, bottom: 480 };

describe("focusPan", () => {
  it("without a pan the demo's outline sits under the sheet and past the right edge", () => {
    const map = coverMap(IMG.w, IMG.h, 360, 900);
    expect(map({ x: FLAP.x0, y: FLAP.y0 }).y).toBeGreaterThan(FREE.bottom);
    expect(map({ x: FLAP.x1, y: FLAP.y1 }).x).toBeGreaterThan(360);
  });

  it("moves the frame so the whole outline is inside the free band, with the margin", () => {
    const pan = focusPan(IMG.w, IMG.h, 360, 900, FLAP, FREE, 16);
    const map = coverMap(IMG.w, IMG.h, 360, 900, pan);
    const a = map({ x: FLAP.x0, y: FLAP.y0 });
    const b = map({ x: FLAP.x1, y: FLAP.y1 });
    expect(a.x).toBeGreaterThanOrEqual(0);
    expect(b.x).toBeLessThanOrEqual(360);
    expect(a.y).toBeGreaterThanOrEqual(FREE.top + 16);
    expect(b.y).toBeLessThanOrEqual(FREE.bottom - 16);
    // Sideways the frame still covers the screen; upwards it never uncovers anything above the sheet.
    const tl = map({ x: 0, y: 0 });
    const br = map({ x: IMG.w, y: IMG.h });
    expect(tl.x).toBeLessThanOrEqual(0);
    expect(br.x).toBeGreaterThanOrEqual(360);
    expect(tl.y).toBeLessThanOrEqual(0);
    expect(br.y).toBeGreaterThanOrEqual(FREE.bottom);
  });

  it("leaves a frame alone when the outline is already clear, or there is none", () => {
    expect(focusPan(IMG.w, IMG.h, 360, 900, { x0: 450, y0: 300, x1: 560, y1: 380 }, FREE, 16)).toEqual({ dx: 0, dy: 0 });
    expect(focusPan(IMG.w, IMG.h, 360, 900, null, FREE, 16)).toEqual({ dx: 0, dy: 0 });
  });

  it("an outline near the top moves down only as far as the frame's own top edge", () => {
    const pan = focusPan(IMG.w, IMG.h, 360, 900, { x0: 450, y0: 0, x1: 560, y1: 20 }, FREE, 16);
    expect(pan.dy).toBe(0);
  });
});

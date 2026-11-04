import { describe, expect, it } from "vitest";
import { FrameGate, hamming, phash } from "../src/phash.ts";

const W = 160;
const H = 120;

/** A textured scene, shifted by dx pixels, with optional noise. */
function scene(dx: number, noise = 0, seed = 1): Uint8Array {
  let s = seed;
  const rnd = (): number => ((s = (s * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff) - 0.5;
  const g = new Uint8Array(W * H);
  for (let y = 0; y < H; y++)
    for (let x = 0; x < W; x++) {
      const X = x + dx;
      const v = 128 + 60 * Math.sin(X / 7) * Math.cos(y / 11) + (X > 60 && X < 100 && y > 40 && y < 80 ? 50 : 0);
      g[y * W + x] = Math.max(0, Math.min(255, v + noise * rnd()));
    }
  return g;
}

describe("phash", () => {
  it("is 16 hex digits and stable under sensor noise", () => {
    const a = phash(scene(0), W, H);
    expect(a).toMatch(/^[0-9a-f]{16}$/);
    expect(hamming(a, phash(scene(0, 6, 7), W, H))).toBeLessThan(6);
  });

  it("moves when the view moves", () => {
    expect(hamming(phash(scene(0), W, H), phash(scene(40), W, H))).toBeGreaterThanOrEqual(8);
  });
});

describe("FrameGate (the SAM saver)", () => {
  it("sends no frame while the view has not moved", () => {
    const g = new FrameGate();
    const still = phash(scene(0), W, H);
    expect(g.offer(still, 0)).toBe("first");
    for (let t = 33; t < 10_000; t += 33) expect(g.offer(phash(scene(0, 4, t), W, H), t)).toBeNull();
    expect(g.sent).toBe(1);
  });

  it("sends a moved view, at most once a second", () => {
    const g = new FrameGate();
    g.offer(phash(scene(0), W, H), 0);
    const moved = phash(scene(40), W, H);
    expect(g.offer(moved, 500)).toBeNull();
    expect(g.offer(moved, 1000)).toBe("moved");
  });

  it("sends fresh frames while a step check runs", () => {
    const g = new FrameGate();
    const still = phash(scene(0), W, H);
    g.offer(still, 0);
    g.checking = true;
    expect(g.offer(still, 500)).toBe("checking");
  });

  it("a still view still sends while the session holds for a fresh frame, and stops when it lets go", () => {
    const g = new FrameGate();
    const still = phash(scene(0), W, H);
    g.offer(still, 0);
    expect(g.offer(still, 3000)).toBeNull();
    g.wantsFresh = true;
    expect(g.offer(still, 3100)).toBe("fresh");
    expect(g.offer(still, 3300)).toBeNull();
    expect(g.offer(still, 3600)).toBe("fresh");
    g.wantsFresh = false;
    expect(g.offer(still, 9000)).toBeNull();
  });
});

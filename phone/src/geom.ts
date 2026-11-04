// Plane geometry for G1 and the tracker: points, polygons, 2x3 affine transforms. Pure, no DOM.

export interface Pt {
  x: number;
  y: number;
}

/** Row-major 2x3 affine [a, b, tx, c, d, ty]: x' = a x + b y + tx, y' = c x + d y + ty. */
export type Affine = readonly [number, number, number, number, number, number];

export const IDENTITY: Affine = [1, 0, 0, 0, 1, 0];

export function apply(m: Affine, p: Pt): Pt {
  return { x: m[0] * p.x + m[1] * p.y + m[2], y: m[3] * p.x + m[4] * p.y + m[5] };
}

/** a ∘ b: apply b first, then a. */
export function compose(a: Affine, b: Affine): Affine {
  return [
    a[0] * b[0] + a[1] * b[3],
    a[0] * b[1] + a[1] * b[4],
    a[0] * b[2] + a[1] * b[5] + a[2],
    a[3] * b[0] + a[4] * b[3],
    a[3] * b[1] + a[4] * b[4],
    a[3] * b[2] + a[4] * b[5] + a[5],
  ];
}

export function invert(m: Affine): Affine {
  const det = m[0] * m[4] - m[1] * m[3];
  if (Math.abs(det) < 1e-12) throw new Error("singular affine");
  const a = m[4] / det;
  const b = -m[1] / det;
  const c = -m[3] / det;
  const d = m[0] / det;
  return [a, b, -(a * m[2] + b * m[5]), c, d, -(c * m[2] + d * m[5])];
}

export function applyAll(m: Affine, poly: readonly Pt[]): Pt[] {
  return poly.map((p) => apply(m, p));
}

/** Even-odd rule. A point on an edge counts as inside (fail-safe for G1). */
export function inside(p: Pt, poly: readonly Pt[]): boolean {
  if (distToSegments(p, poly) < 1e-9) return true;
  let hit = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const a = poly[i]!;
    const b = poly[j]!;
    if (a.y > p.y !== b.y > p.y && p.x < ((b.x - a.x) * (p.y - a.y)) / (b.y - a.y) + a.x) hit = !hit;
  }
  return hit;
}

function distToSegment(p: Pt, a: Pt, b: Pt): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2));
  return Math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy));
}

function distToSegments(p: Pt, poly: readonly Pt[]): number {
  let best = Infinity;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) best = Math.min(best, distToSegment(p, poly[j]!, poly[i]!));
  return best;
}

/** Distance in pixels from p to the polygon's outline; 0 when p is inside. */
export function distToPolygon(p: Pt, poly: readonly Pt[]): number {
  if (poly.length === 0) return Infinity;
  if (inside(p, poly)) return 0;
  return distToSegments(p, poly);
}

export function centroid(poly: readonly Pt[]): Pt {
  let x = 0;
  let y = 0;
  for (const p of poly) {
    x += p.x;
    y += p.y;
  }
  return { x: x / poly.length, y: y / poly.length };
}

export function bbox(poly: readonly Pt[]): { x0: number; y0: number; x1: number; y1: number } {
  let x0 = Infinity;
  let y0 = Infinity;
  let x1 = -Infinity;
  let y1 = -Infinity;
  for (const p of poly) {
    x0 = Math.min(x0, p.x);
    y0 = Math.min(y0, p.y);
    x1 = Math.max(x1, p.x);
    y1 = Math.max(y1, p.y);
  }
  return { x0, y0, x1, y1 };
}

/** Parses the polygon(s) out of an SVG path made of M/L/Z (absolute) commands, as the server sends mask_svg. */
export function parseSvgPolygons(svg: string): Pt[][] {
  const d = /\bd="([^"]*)"/.exec(svg)?.[1] ?? svg;
  const polys: Pt[][] = [];
  let cur: Pt[] = [];
  const re = /([MLZmlz])|(-?\d*\.?\d+(?:e-?\d+)?)/g;
  const nums: number[] = [];
  const flush = (): void => {
    for (let i = 0; i + 1 < nums.length; i += 2) cur.push({ x: nums[i]!, y: nums[i + 1]! });
    nums.length = 0;
  };
  for (let m = re.exec(d); m; m = re.exec(d)) {
    if (m[1]) {
      flush();
      const c = m[1].toUpperCase();
      if (c === "M" && cur.length) {
        polys.push(cur);
        cur = [];
      }
      if (c === "Z") {
        if (cur.length) polys.push(cur);
        cur = [];
      }
    } else nums.push(Number(m[2]));
  }
  flush();
  if (cur.length) polys.push(cur);
  return polys.filter((p) => p.length >= 3);
}

export function polygonToSvgPath(poly: readonly Pt[]): string {
  return poly.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)} ${p.y.toFixed(1)}`).join(" ") + " Z";
}

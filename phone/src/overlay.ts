// The canvas over the camera: outlines drawn on in 300 ms and pulsing once on a pass, red zones with their ring, and
// a second ring at the hand's distance that tightens onto the zone as the hand approaches. Drawn by the app itself.
// The part is the feature, drawn as the brand's mark: two crop corners close on it, then a smoothed outline in the ink
// blue on a white halo draws on, with a light fill and a numbered tag pinned to the top corner. Parts not to touch
// get the same corners in red.

import { bbox, centroid, type Pt } from "./geom.ts";

export const DRAW_ON_MS = 300;
export const PULSE_MS = 600;
/** The corners' close-in, before the outline draws on. */
export const LOCK_MS = 280;

/** The overlay's colours. The app reads them from the page's tokens (readLook), so the camera can't drift from them;
 * these are the dark theme's values, for when there is no page to read. The part keeps the paper ink blue on a white
 * halo: over a camera that reads better than the light blue. */
export interface Look {
  target: string;
  red: string;
  green: string;
  context: string;
  fg: string;
  muted: string;
  sheet: string;
  ink: string;
  /** Reduced motion: the corners and lines are drawn in place. */
  still: boolean;
}
export const LOOK: Look = {
  target: "#2a49d8",
  red: "#ff6f52",
  green: "#5fcf98",
  context: "#958e7e",
  fg: "#f1ece0",
  muted: "#c8c1b1",
  sheet: "rgba(21, 19, 15, 0.86)",
  ink: "#15130f",
  still: false,
};
const HALO = "rgba(255, 255, 255, 0.92)";
const DARK_HALO = "rgba(21, 19, 15, 0.4)";

/** The look from the page's own tokens (--red, --green, --faint, --fg, --muted, --sheet, --bg); a token that isn't set
 * keeps the dark default. */
export function readLook(root: Element | null | undefined, still: boolean): Look {
  const cs = root ? root.ownerDocument.defaultView?.getComputedStyle(root) : undefined;
  const v = (name: string, d: string): string => cs?.getPropertyValue(name).trim() || d;
  return {
    target: LOOK.target,
    red: v("--red", LOOK.red),
    green: v("--green", LOOK.green),
    context: v("--faint", LOOK.context),
    fg: v("--fg", LOOK.fg),
    muted: v("--muted", LOOK.muted),
    sheet: v("--sheet", LOOK.sheet),
    ink: v("--bg", LOOK.ink),
    still,
  };
}

/** A colour at an opacity, for #rrggbb; anything else is returned as it is. */
function alpha(c: string, a: number): string {
  const m = /^#([0-9a-f]{6})$/i.exec(c);
  if (!m) return c;
  const n = parseInt(m[1]!, 16);
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

/** The app's --ease, cubic-bezier(.2,.7,.1,1), near enough: fast in, a long soft landing. */
function ease(t: number): number {
  return 1 - Math.pow(1 - Math.max(0, Math.min(1, t)), 3.2);
}

/** Maps image space onto the screen the way `object-fit: cover` maps the video. Recompute on every resize (the
 * Fold changes aspect when it unfolds). */
export function coverMap(imgW: number, imgH: number, viewW: number, viewH: number, pan: Pan = NO_PAN): (p: Pt) => Pt {
  const s = Math.max(viewW / imgW, viewH / imgH);
  const ox = (viewW - imgW * s) / 2 + pan.dx;
  const oy = (viewH - imgH * s) / 2 + pan.dy;
  return (p) => ({ x: p.x * s + ox, y: p.y * s + oy });
}

/** How far the frame (and the overlay with it) is moved from its centred cover position, in view pixels. */
export interface Pan {
  dx: number;
  dy: number;
}
export const NO_PAN: Pan = { dx: 0, dy: 0 };

/**
 * The pan that keeps a part in view: the outlines' box (image pixels) is moved into the free band between the
 * header and the bottom sheet, so a card never covers what it points at. Sideways the frame still covers the screen;
 * upwards it may leave a strip under the sheet uncovered, never above it. No box, no pan.
 */
export function focusPan(
  imgW: number,
  imgH: number,
  viewW: number,
  viewH: number,
  box: { x0: number; y0: number; x1: number; y1: number } | null,
  free: { top: number; bottom: number },
  margin: number,
): Pan {
  if (!box) return NO_PAN;
  const s = Math.max(viewW / imgW, viewH / imgH);
  const ox = (viewW - imgW * s) / 2;
  const oy = (viewH - imgH * s) / 2;
  const x0 = box.x0 * s + ox;
  const x1 = box.x1 * s + ox;
  const y0 = box.y0 * s + oy;
  const y1 = box.y1 * s + oy;
  let dx = 0;
  if (x0 < margin || x1 > viewW - margin) dx = viewW / 2 - (x0 + x1) / 2;
  dx = Math.min(-ox, Math.max(viewW - ox - imgW * s, dx));
  let dy = 0;
  if (y1 > free.bottom - margin) dy = free.bottom - margin - y1;
  if (y0 + dy < free.top + margin) dy = free.top + margin - y0;
  dy = Math.min(-oy, Math.max(Math.min(0, free.bottom - oy - imgH * s), dy));
  return { dx: dx + 0, dy: dy + 0 }; // never -0
}

export interface DrawShape {
  id: string;
  kind: "target" | "context" | "do_not_touch" | "zone";
  polys: Pt[][];
  /** When this shape first appeared (performance.now()), for the draw-on. */
  since: number;
  pulseSince: number | null;
  label: string | null;
  /** Where the manual shows it, for the tag: "p. 43". */
  page?: string | null;
}

export interface DrawZone {
  id: string;
  polys: Pt[][];
  ringPx: number;
  /** The hand's gap to the ring in px, when a hand is near; null when none. */
  handGapPx: number | null;
  testZone: boolean;
}

function pathLen(poly: Pt[]): number {
  let n = 0;
  for (let i = 0; i < poly.length; i++) {
    const a = poly[i]!;
    const b = poly[(i + 1) % poly.length]!;
    n += Math.hypot(b.x - a.x, b.y - a.y);
  }
  return n;
}

/** Fewer points: drops each one within eps of the line its neighbours make (Ramer-Douglas-Peucker, on a closed ring). */
export function simplify(poly: Pt[], eps: number): Pt[] {
  if (poly.length < 5) return poly;
  const rdp = (pts: Pt[]): Pt[] => {
    const a = pts[0]!;
    const b = pts[pts.length - 1]!;
    const len = Math.hypot(b.x - a.x, b.y - a.y) || 1;
    let far = 0;
    let at = 0;
    for (let i = 1; i < pts.length - 1; i++) {
      const p = pts[i]!;
      const d = Math.abs((b.x - a.x) * (a.y - p.y) - (a.x - p.x) * (b.y - a.y)) / len;
      if (d > far) [far, at] = [d, i];
    }
    return far <= eps ? [a, b] : [...rdp(pts.slice(0, at + 1)).slice(0, -1), ...rdp(pts.slice(at))];
  };
  // Split the ring at its two farthest-apart points, so neither half is degenerate.
  let j = 0;
  let best = 0;
  for (let i = 1; i < poly.length; i++) {
    const d = Math.hypot(poly[i]!.x - poly[0]!.x, poly[i]!.y - poly[0]!.y);
    if (d > best) [best, j] = [d, i];
  }
  const out = [...rdp(poly.slice(0, j + 1)).slice(0, -1), ...rdp([...poly.slice(j), poly[0]!]).slice(0, -1)];
  return out.length >= 3 ? out : poly;
}

/** A closed curve through the midpoints of the polygon's sides, its corners as the control points: rounded, not jagged. */
function smooth(ctx: CanvasRenderingContext2D, pts: Pt[]): void {
  ctx.beginPath();
  const n = pts.length;
  const mid = (a: Pt, b: Pt): Pt => ({ x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 });
  const m0 = mid(pts[n - 1]!, pts[0]!);
  ctx.moveTo(m0.x, m0.y);
  for (let i = 0; i < n; i++) {
    const m = mid(pts[i]!, pts[(i + 1) % n]!);
    ctx.quadraticCurveTo(pts[i]!.x, pts[i]!.y, m.x, m.y);
  }
  ctx.closePath();
}

type Box = { x0: number; y0: number; x1: number; y1: number };

/** The mark's two corners, top-left and bottom-right (never four: four is the stock scan glyph), `out` px outside the
 * box and `k` of the way from 130% of it to the box itself. Square caps, on a dark under-stroke for bright steel. */
function corners(ctx: CanvasRenderingContext2D, box: Box, colour: string, look: Look, px: number, k: number): Box {
  const out = 12 * px;
  const cx = (box.x0 + box.x1) / 2;
  const cy = (box.y0 + box.y1) / 2;
  const s = 1.3 - 0.3 * ease(k);
  const hw = ((box.x1 - box.x0) / 2 + out) * s;
  const hh = ((box.y1 - box.y0) / 2 + out) * s;
  // Never off the screen: a part that fills the view keeps its corners at the edges.
  const { width, height } = ctx.canvas;
  const edge = 6 * px;
  const c = { x0: Math.max(edge, cx - hw), y0: Math.max(edge, cy - hh), x1: Math.min(width - edge, cx + hw), y1: Math.min(height - edge, cy + hh) };
  const arm = Math.min(40 * px, hw, hh);
  ctx.save();
  ctx.lineCap = "square";
  ctx.lineJoin = "miter";
  ctx.globalAlpha = Math.min(1, k * 2);
  const path = (): void => {
    ctx.beginPath();
    ctx.moveTo(c.x0, c.y0 + arm);
    ctx.lineTo(c.x0, c.y0);
    ctx.lineTo(c.x0 + arm, c.y0);
    ctx.moveTo(c.x1, c.y1 - arm);
    ctx.lineTo(c.x1, c.y1);
    ctx.lineTo(c.x1 - arm, c.y1);
  };
  path();
  ctx.strokeStyle = alpha(look.ink, 0.55);
  ctx.lineWidth = 8 * px;
  ctx.stroke();
  path();
  ctx.strokeStyle = colour;
  ctx.lineWidth = 5 * px;
  ctx.stroke();
  ctx.restore();
  return c;
}

/** The part's tag, as the site's callouts, pinned to the top-left corner: a numbered blue square, the name on the
 * sheet, then the page in mono. Under the shape when there is no room above it. */
function tag(ctx: CanvasRenderingContext2D, n: number, text: string, page: string | null, at: Box, look: Look, px: number, k: number): void {
  const { width, height } = ctx.canvas;
  ctx.save();
  ctx.globalAlpha = k;
  const pad = 10 * px;
  const h = 26 * px;
  const num = h;
  const name = `600 ${13 * px}px Inter, system-ui, sans-serif`;
  const mono = `500 ${11 * px}px "JetBrains Mono", ui-monospace, monospace`;
  ctx.font = name;
  const tw = ctx.measureText(text).width;
  ctx.font = mono;
  const pw = page ? ctx.measureText(page).width + pad : 0;
  const w = Math.min(width - 16 * px, num + pad + tw + pad + pw);
  let x = Math.max(8 * px, Math.min(width - 8 * px - w, at.x0 - 2.5 * px));
  let y = at.y0 - 2.5 * px - h;
  if (y < 8 * px) y = Math.min(height - h - 8 * px, at.y1 + 6 * px);
  x = Math.round(x);
  y = Math.round(y);
  ctx.fillStyle = look.sheet;
  ctx.fillRect(x, y, w, h);
  ctx.fillStyle = look.target;
  ctx.fillRect(x, y, num, h);
  ctx.textBaseline = "middle";
  ctx.textAlign = "center";
  ctx.fillStyle = "#ffffff";
  ctx.font = `600 ${14 * px}px Inter, system-ui, sans-serif`;
  ctx.fillText(String(n), x + num / 2, y + h / 2 + px);
  ctx.textAlign = "left";
  ctx.save();
  ctx.beginPath();
  ctx.rect(x + num, y, w - num - pad / 2, h);
  ctx.clip();
  ctx.fillStyle = look.fg;
  ctx.font = name;
  ctx.fillText(text, x + num + pad, y + h / 2 + px);
  if (page) {
    ctx.fillStyle = look.muted;
    ctx.font = mono;
    ctx.fillText(page, x + num + pad + tw + pad, y + h / 2 + px);
  }
  ctx.restore();
  ctx.restore();
}

/** A rough outer ring: the polygon pushed out from its centroid by r pixels. */
function grow(poly: Pt[], r: number): Pt[] {
  const c = centroid(poly);
  return poly.map((p) => {
    const d = Math.hypot(p.x - c.x, p.y - c.y) || 1;
    return { x: p.x + ((p.x - c.x) / d) * r, y: p.y + ((p.y - c.y) / d) * r };
  });
}

export function draw(
  ctx: CanvasRenderingContext2D,
  shapes: Iterable<DrawShape>,
  zones: Iterable<DrawZone>,
  map: (p: Pt) => Pt,
  now: number,
  /** Canvas pixels per CSS pixel, so a 3 px line is 3 px on the screen. */
  px = 1,
  look: Look = LOOK,
): void {
  const { width, height } = ctx.canvas;
  ctx.clearRect(0, 0, width, height);
  ctx.lineJoin = "round";
  ctx.lineCap = "round";

  // A red zone's dashed ring, on the screen: the corners of the shape it grows from frame it too.
  const zs = [...zones];
  const rings = zs.flatMap((z) => z.polys.map((p) => bbox(grow(p, z.ringPx).map(map))));
  let n = 0;
  for (const s of shapes) {
    // The corners close first (targets and do-not-touch), then the line draws on as they land.
    const marked = s.kind !== "context";
    const lock = look.still ? 1 : Math.max(0, Math.min(1, (now - s.since) / LOCK_MS));
    const k = look.still ? 1 : Math.max(0, Math.min(1, (now - s.since - (marked ? LOCK_MS : 0)) / DRAW_ON_MS));
    const red = s.kind === "do_not_touch" || s.kind === "zone";
    const colour = s.kind === "target" ? look.target : red ? look.red : look.context;
    const pulse = s.pulseSince === null ? 0 : Math.max(0, 1 - (now - s.pulseSince) / PULSE_MS);
    const line = (s.kind === "target" ? 3 : 2.5) * px + 6 * px * Math.sin(pulse * Math.PI);
    let box: { x0: number; y0: number; x1: number; y1: number } | null = null;
    for (const poly of s.polys) {
      const screen = poly.map(map);
      if (screen.length < 3) continue;
      const b = bbox(screen);
      box = box ? { x0: Math.min(box.x0, b.x0), y0: Math.min(box.y0, b.y0), x1: Math.max(box.x1, b.x1), y1: Math.max(box.y1, b.y1) } : b;
      const pts = simplify(screen, Math.max(2 * px, 0.012 * Math.hypot(b.x1 - b.x0, b.y1 - b.y0)));
      const len = pathLen(pts);
      // The fill comes in with the line; a pass flashes it green.
      smooth(ctx, pts);
      ctx.fillStyle = pulse > 0 ? alpha(look.green, 0.35 * pulse) : alpha(colour, (red ? 0.16 : 0.14) * k);
      ctx.fill();
      // Before the line starts there is nothing to stroke: a zero dash with round caps would leave a dot.
      if (k <= 0) continue;
      ctx.setLineDash([len * k, len]);
      // Red on a white halo reads pink: parts not to touch sit on a dark one.
      ctx.strokeStyle = s.kind === "target" ? HALO : DARK_HALO;
      ctx.lineWidth = line + 4 * px;
      ctx.stroke();
      ctx.strokeStyle = colour;
      ctx.lineWidth = line;
      ctx.stroke();
      ctx.setLineDash([]);
    }
    if (marked && box && red)
      for (const r of rings)
        if (r.x0 <= box.x1 && r.x1 >= box.x0 && r.y0 <= box.y1 && r.y1 >= box.y0)
          box = { x0: Math.min(box.x0, r.x0), y0: Math.min(box.y0, r.y0), x1: Math.max(box.x1, r.x1), y1: Math.max(box.y1, r.y1) };
    if (marked && box) {
      const at = corners(ctx, box, s.kind === "target" ? look.fg : look.red, look, px, lock);
      if (s.kind === "target") {
        n += 1;
        if (s.label) tag(ctx, n, s.label, s.page ?? null, at, look, px, k);
      }
    }
  }

  for (const z of zs) {
    for (const poly of z.polys) {
      // One red shape on a dark halo, and its ring the same shape grown, dashed.
      const screen = poly.map(map);
      const b = bbox(screen);
      const pts = screen.length >= 3 ? simplify(screen, Math.max(2 * px, 0.012 * Math.hypot(b.x1 - b.x0, b.y1 - b.y0))) : screen;
      smooth(ctx, pts);
      ctx.fillStyle = alpha(look.red, 0.16);
      ctx.fill();
      ctx.strokeStyle = DARK_HALO;
      ctx.lineWidth = 9 * px;
      ctx.stroke();
      ctx.strokeStyle = look.red;
      ctx.lineWidth = 3 * px;
      ctx.stroke();
      ctx.setLineDash([6 * px, 6 * px]);
      ctx.lineWidth = 2 * px;
      smooth(ctx, grow(poly, z.ringPx).map(map));
      ctx.stroke();
      ctx.setLineDash([]);
      if (z.handGapPx !== null) {
        // The ring that tightens: drawn at the hand's distance, thicker and more opaque as it closes.
        const close = Math.max(0, Math.min(1, 1 - z.handGapPx / (z.ringPx * 8)));
        ctx.strokeStyle = alpha(look.red, 0.3 + 0.7 * close);
        ctx.lineWidth = 2 + 8 * close;
        smooth(ctx, grow(poly, z.ringPx + z.handGapPx).map(map));
        ctx.stroke();
      }
      if (z.testZone) {
        const b = bbox(poly);
        const q = map({ x: b.x0, y: b.y0 - 8 });
        ctx.fillStyle = "#f0b54a";
        ctx.font = "bold 16px system-ui, sans-serif";
        ctx.fillText("TEST ZONE", q.x, q.y);
      }
    }
  }
}

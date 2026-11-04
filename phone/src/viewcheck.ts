// The view check: for a step near a red zone the phone must look from the side, so a reach crosses the image
// instead of running along the lens. A front view never starts such a step.
//
// What "side" means here, in code: the camera's heading has turned 60–120° from the heading it had when the part was
// located from the front (device orientation, same session), and both outlines (the target and the red zone) sit
// whole inside the image, apart from each other. The scale comes from the zone part's known width.

import { bbox, type Pt } from "./geom.ts";
import { langOf } from "./words.ts";

export const SIDE_MIN_DEG = 60;
export const SIDE_MAX_DEG = 120;
/** Outlines must sit this far inside the image edge (fraction of the short side). */
export const EDGE_MARGIN = 0.03;

export interface ViewInput {
  imgW: number;
  imgH: number;
  /** Heading (deg) when the job's front-view locate ran; null when never recorded. */
  frontHeadingDeg: number | null;
  headingDeg: number | null;
  zone: Pt[] | null;
  target: Pt[] | null;
  knownWidthCm: number | null;
}

export type ViewResult =
  | { ok: true; kind: "side"; scaleCmPerPx: number }
  | { ok: false; kind: "front" | "side"; scaleCmPerPx: number | null; why: ViewWhy };

export type ViewWhy = "front_view" | "no_heading" | "zone_not_seen" | "target_not_seen" | "outlines_overlap" | "no_scale";

export const VIEW_TEXT: Record<ViewWhy, string> = {
  front_view: "Prop the phone side-on, beside the part, so I can see your hand cross.",
  no_heading: "Point the phone at the front first, then move it to the side.",
  zone_not_seen: "Move so the red part is fully in view.",
  target_not_seen: "Move so both parts are fully in view.",
  outlines_overlap: "Move further to the side so the two parts stand apart.",
  no_scale: "Hold still while I measure the view.",
};

export function headingDelta(a: number, b: number): number {
  const d = Math.abs(((a - b) % 360) + 360) % 360;
  return d > 180 ? 360 - d : d;
}

function wholeInside(poly: Pt[], w: number, h: number): boolean {
  const m = EDGE_MARGIN * Math.min(w, h);
  const b = bbox(poly);
  return b.x0 >= m && b.y0 >= m && b.x1 <= w - m && b.y1 <= h - m;
}

function overlap(a: Pt[], b: Pt[]): boolean {
  const p = bbox(a);
  const q = bbox(b);
  return p.x0 < q.x1 && q.x0 < p.x1 && p.y0 < q.y1 && q.y0 < p.y1;
}

export function checkView(v: ViewInput): ViewResult {
  const scale = v.zone && v.knownWidthCm ? v.knownWidthCm / Math.max(1, bbox(v.zone).x1 - bbox(v.zone).x0) : null;
  if (v.frontHeadingDeg === null || v.headingDeg === null)
    return { ok: false, kind: "front", scaleCmPerPx: scale, why: "no_heading" };
  const d = headingDelta(v.headingDeg, v.frontHeadingDeg);
  const side = d >= SIDE_MIN_DEG && d <= SIDE_MAX_DEG;
  const kind = side ? "side" : "front";
  if (!side) return { ok: false, kind, scaleCmPerPx: scale, why: "front_view" };
  if (!v.zone || !wholeInside(v.zone, v.imgW, v.imgH)) return { ok: false, kind, scaleCmPerPx: scale, why: "zone_not_seen" };
  if (!v.target || !wholeInside(v.target, v.imgW, v.imgH))
    return { ok: false, kind, scaleCmPerPx: scale, why: "target_not_seen" };
  if (overlap(v.zone, v.target)) return { ok: false, kind, scaleCmPerPx: scale, why: "outlines_overlap" };
  if (scale === null || !(scale > 0)) return { ok: false, kind, scaleCmPerPx: null, why: "no_scale" };
  return { ok: true, kind: "side", scaleCmPerPx: scale };
}

/** Whether a step may start: a step near a red zone needs a passed side view; any other step needs nothing. */
export function mayStart(step: { view: "front" | "side" }, nearRedZone: boolean, last: ViewResult | null): boolean {
  if (!nearRedZone && step.view !== "side") return true;
  return last?.ok === true;
}

/** The warning before a ring step: the zone has shrunk to a ring around the part itself. */
export function ringWarning(ringCm: number, label: string, lang = "en"): string {
  // The label comes from an English rule id, so the Dutch line says "the part" rather than mix the two.
  if (langOf(lang) === "nl") return `Let op: hier is de rode zone maar ${Math.round(ringCm)} cm rond het onderdeel. Werk ernaast; raak het onderdeel niet aan.`;
  return `Careful: here the red zone is only ${Math.round(ringCm)} cm around the ${label}. Work beside it; don't touch the ${label}.`;
}

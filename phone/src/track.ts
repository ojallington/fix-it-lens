// Keeps outlines on the part between SAM answers. The LK worker reports each frame's
// motion against the previous one; this file chains them into T_i (frame 0 → frame i), keeps a ring of them by frame
// id, and draws a late mask for frame k at T_now · T_k⁻¹ ("warp forward"). It also says when the view has drifted
// enough to want a fresh SAM anchor, and when tracking is too weak to trust (G1 then pauses: it fails closed).

import { apply, compose, IDENTITY, invert, type Affine, type Pt } from "./geom.ts";

export const RING_FRAMES = 300; // 10 s at 30 fps, longer than SAM's p95 of 3 s
export const MIN_INLIER_RATIO = 0.5;
export const MIN_POINTS = 12;
/** Re-anchor when the transform since the last anchor passes any of these. */
export const REANCHOR = { scale: 0.15, rotDeg: 10, shiftFrac: 0.25 };

export interface FlowResult {
  frameId: string;
  /** Motion from the previous frame to this one, in image pixels; null when the fit failed. */
  delta: Affine | null;
  points: number;
  inliers: number;
}

export class Tracker {
  private ring = new Map<string, Affine>();
  private order: string[] = [];
  private now: Affine = IDENTITY;
  private anchor: Affine = IDENTITY;
  private anchorSize = 100;
  nowId: string | null = null;
  confident = false;

  /** Feeds one frame's flow; returns whether tracking is good enough to hold a red zone. */
  push(r: FlowResult): boolean {
    this.confident = r.delta !== null && r.points >= MIN_POINTS && r.inliers / Math.max(1, r.points) >= MIN_INLIER_RATIO;
    if (r.delta) this.now = compose(r.delta, this.now);
    this.ring.set(r.frameId, this.now);
    this.order.push(r.frameId);
    while (this.order.length > RING_FRAMES) this.ring.delete(this.order.shift()!);
    this.nowId = r.frameId;
    return this.confident;
  }

  /** The transform that takes points seen in frame k to where they are now; null when k fell out of the ring. */
  warpFrom(frameId: string): Affine | null {
    const k = this.ring.get(frameId);
    if (!k) return null;
    return compose(this.now, invert(k));
  }

  warpPoly(frameId: string, poly: readonly Pt[]): Pt[] | null {
    const m = this.warpFrom(frameId);
    return m ? poly.map((p) => apply(m, p)) : null;
  }

  /** A SAM answer for frame k became the anchor; size is the outline's box size, for the shift threshold. */
  anchored(frameId: string, size: number): void {
    this.anchor = this.ring.get(frameId) ?? this.now;
    this.anchorSize = Math.max(1, size);
  }

  /** How far the view moved since the anchor, as scale change, rotation and shift. */
  drift(): { scale: number; rotDeg: number; shift: number } {
    const d = compose(this.now, invert(this.anchor));
    const sx = Math.hypot(d[0], d[3]);
    const sy = Math.hypot(d[1], d[4]);
    return {
      scale: Math.abs(Math.sqrt(sx * sy) - 1),
      rotDeg: Math.abs((Math.atan2(d[3], d[0]) * 180) / Math.PI),
      shift: Math.hypot(d[2], d[5]) / this.anchorSize,
    };
  }

  wantsAnchor(): boolean {
    const d = this.drift();
    return d.scale > REANCHOR.scale || d.rotDeg > REANCHOR.rotDeg || d.shift > REANCHOR.shiftFrac;
  }

  /** Scale (cm per pixel) now, from the scale measured on frame k; the view zooming in makes pixels smaller. */
  scaleNow(frameId: string, cmPerPxAtK: number): number | null {
    const m = this.warpFrom(frameId);
    if (!m) return null;
    const det = Math.abs(m[0] * m[4] - m[1] * m[3]);
    return det > 0 ? cmPerPxAtK / Math.sqrt(det) : null;
  }
}

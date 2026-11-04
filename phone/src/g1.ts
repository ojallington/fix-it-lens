// G1, the hand stop, on the device. Pure: landmarks, a zone and a scale in; at most one event per frame out.
// The constants and the shared vectors come from rules/g1.json; the Python twin is rules/g1_ref.py.
// Order per frame: stale frame, hand lost, covered, contact, distance, time to contact.
// Nothing here can be switched off: there is no option that skips a check.

import type { G1Constants, G1Frame } from "../../contracts/ts/src/g1.ts";
import { distToPolygon, inside, type Pt } from "./geom.ts";

export type G1Kind = "stop" | "pause";
export type G1Reason = "ttc" | "distance" | "covered" | "hand_lost" | "stale_frame";

export interface G1Event {
  t_ms: number;
  kind: G1Kind;
  reason: G1Reason;
  /** Smallest time to contact over the fingertips this frame, when one was computed. */
  ttc_ms: number | null;
  /** Smallest gap over the fingertips this frame (cm, after ring and tool), when a hand was seen. */
  dist_cm: number | null;
}

export type Constants = G1Constants;

interface Prev {
  t_ms: number;
  gaps: number[];
}

export class G1 {
  private prev: Prev | null = null;

  constructor(private readonly c: Constants) {}

  get nearCm(): number {
    return this.c.near_cm;
  }

  /** The ring actually enforced: never under the floor, grown by the tool in the hand. */
  ringCm(ringCm: number, toolLenCm: number): number {
    return Math.max(ringCm, this.c.ring_floor_cm) + Math.max(0, toolLenCm);
  }

  reset(): void {
    this.prev = null;
  }

  step(f: G1Frame): G1Event | null {
    const prev = this.prev;
    const tips: Pt[] = f.fingertips;
    const ring = this.ringCm(f.zone.ring_cm, f.tool_len_cm);
    const gaps = tips.map((p) => Math.max(0, distToPolygon(p, f.zone.polygon) * f.zone.scale_cm_per_px - ring));
    this.prev = { t_ms: f.t_ms, gaps };
    const minGap = gaps.length ? Math.min(...gaps) : null;
    const ev = (kind: G1Kind, reason: G1Reason, ttc: number | null = null): G1Event => ({
      t_ms: f.t_ms,
      kind,
      reason,
      ttc_ms: ttc,
      dist_cm: minGap,
    });

    if ((f.frame_age_ms ?? 0) > this.c.frame_max_age_ms) return ev("pause", "stale_frame");

    if (tips.length === 0) {
      if (prev && prev.gaps.length > 0 && Math.min(...prev.gaps) < this.nearCm) return ev("pause", "hand_lost");
      return null;
    }

    if (tips.some((p) => inside(p, f.zone.polygon))) return ev("stop", "covered", 0);
    if (minGap === 0) return ev("stop", "distance", 0);
    if (!f.zone.ring_step && minGap! < this.c.dist_cm) return ev("stop", "distance");

    const dt = prev ? f.t_ms - prev.t_ms : 0;
    if (prev && dt > 0 && prev.gaps.length === gaps.length) {
      let best: number | null = null;
      gaps.forEach((g, i) => {
        const closing = (prev.gaps[i]! - g) / dt;
        if (closing > 0) best = Math.min(best ?? Infinity, g / closing);
      });
      if (best !== null && best < this.c.ttc_ms) return ev("stop", "ttc", best);
    }
    return null;
  }
}

/** Runs a vector's frames through a fresh G1 and returns the first event, as the shared vectors expect. */
export function runFrames(c: Constants, frames: readonly G1Frame[]): G1Event | null {
  const g = new G1(c);
  for (const f of frames) {
    const e = g.step(f);
    if (e) return e;
  }
  return null;
}

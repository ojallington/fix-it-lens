// The fast path's decision loop, run on every camera frame: one G1 per red zone, against the zone as the tracker holds
// it now. A stop latches until code clears it (the server's safety state back to OK after the stop, or, with no
// server, the hand well clear for 2 s). A pause lasts while its cause lasts. Losing a zone fails closed: it pauses.
// The model never clears anything here, and no option turns a check off.

import type { Zone } from "../../contracts/ts/src/g1.ts";
import type { G1 as G1Msg } from "../../contracts/ts/src/ws.ts";
import { G1, type Constants, type G1Event } from "./g1.ts";
import { distToPolygon, type Pt } from "./geom.ts";

export const OFFLINE_CLEAR_MS = 2000;

export interface ZoneNow {
  zoneId: string;
  label: string;
  /** The zone where it is in this frame; null when the tracker cannot place it (it fails closed). */
  polygon: Pt[] | null;
  ringCm: number;
  ringStep: boolean;
  scaleCmPerPx: number | null;
  toolLenCm: number;
  testZone: boolean;
}

export interface FrameIn {
  frameId: string;
  tMs: number;
  ageMs: number;
  /** Every hand landmark in image space; empty when no hand is seen. */
  points: Pt[];
  trackerConfident: boolean;
  zones: ZoneNow[];
}

export type GuardState =
  | { kind: "clear" }
  | { kind: "stop"; zoneId: string; label: string; event: G1Event; at: number }
  | { kind: "pause"; zoneId: string; label: string; why: "hand_lost" | "stale_frame" | "zone_lost"; event: G1Event | null };

type Unsent = Omit<G1Msg, "seq" | "t_ms"> & { t_ms: number };

export class Guard {
  private g1 = new Map<string, G1>();
  state: GuardState = { kind: "clear" };
  private clearSince: number | null = null;
  /** Gap per zone this frame, for the tightening ring. */
  gaps = new Map<string, number | null>();

  constructor(
    private readonly c: Constants,
    private readonly emit: (m: Unsent) => void,
  ) {}

  private for(zoneId: string): G1 {
    let g = this.g1.get(zoneId);
    if (!g) this.g1.set(zoneId, (g = new G1(this.c)));
    return g;
  }

  /** The server moved safety back to OK after our stop: code clears the latch. */
  serverCleared(): void {
    if (this.state.kind === "stop") this.state = { kind: "clear" };
    this.clearSince = null;
  }

  /** The ring G1 enforces round this zone, in cm (floor and tool included). */
  ringFor(z: ZoneNow): number {
    return this.for(z.zoneId).ringCm(z.ringCm, z.toolLenCm);
  }

  dropZone(zoneId: string): void {
    this.g1.delete(zoneId);
    this.gaps.delete(zoneId);
  }

  frame(f: FrameIn, serverUp: boolean): GuardState {
    let pause: GuardState | null = null;
    let anyNear = false;
    this.gaps.clear();

    for (const z of f.zones) {
      if (!z.polygon || z.polygon.length < 3 || !f.trackerConfident || z.scaleCmPerPx === null) {
        this.for(z.zoneId).reset();
        this.gaps.set(z.zoneId, null);
        pause ??= { kind: "pause", zoneId: z.zoneId, label: z.label, why: "zone_lost", event: null };
        continue;
      }
      const e = this.for(z.zoneId).step({
        t_ms: f.tMs,
        frame_age_ms: f.ageMs,
        fingertips: f.points,
        zone: { polygon: z.polygon as Zone["polygon"], ring_cm: z.ringCm, ring_step: z.ringStep, scale_cm_per_px: z.scaleCmPerPx },
        tool_len_cm: z.toolLenCm,
      });
      const gap = e?.dist_cm ?? this.minGap(f.points, z);
      this.gaps.set(z.zoneId, gap);
      if (gap !== null && gap < this.c.near_cm) anyNear = true;
      if (e?.kind === "stop") {
        if (this.state.kind !== "stop") {
          this.state = { kind: "stop", zoneId: z.zoneId, label: z.label, event: e, at: f.tMs };
          this.send(z.zoneId, e, f.frameId);
        }
        this.clearSince = null;
        return this.state;
      }
      if (e?.kind === "pause") pause ??= { kind: "pause", zoneId: z.zoneId, label: z.label, why: e.reason as "hand_lost" | "stale_frame", event: e };
    }

    if (this.state.kind === "stop") {
      // Offline only: with no server to clear it, the hand must stay clear of every zone for 2 s.
      if (!serverUp && !anyNear && f.points.length > 0) {
        this.clearSince ??= f.tMs;
        if (f.tMs - this.clearSince >= OFFLINE_CLEAR_MS) this.state = { kind: "clear" };
      } else this.clearSince = null;
      if (this.state.kind === "stop") return this.state;
    }

    if (pause) {
      const was = this.state;
      // hand_lost fires on the frame the hand vanished; it holds until the hand is back.
      const sameLost = was.kind === "pause" && was.why === "hand_lost" && f.points.length === 0;
      if (!(was.kind === "pause" && was.why === pause.why && was.zoneId === pause.zoneId)) {
        this.send(pause.zoneId, pause.event, f.frameId, f.tMs);
      }
      this.state = sameLost ? was : pause;
      return this.state;
    }
    if (this.state.kind === "pause" && this.state.why === "hand_lost" && f.points.length === 0) return this.state;
    this.state = { kind: "clear" };
    return this.state;
  }

  private minGap(points: Pt[], z: ZoneNow): number | null {
    if (!points.length || !z.polygon || z.scaleCmPerPx === null) return null;
    const g = this.for(z.zoneId);
    const ring = g.ringCm(z.ringCm, z.toolLenCm);
    let best = Infinity;
    for (const p of points) best = Math.min(best, Math.max(0, distToPolygon(p, z.polygon) * z.scaleCmPerPx - ring));
    return best;
  }

  private send(zoneId: string, e: G1Event | null, frameId: string, t?: number): void {
    this.emit({
      type: "g1",
      t_ms: e?.t_ms ?? t ?? 0,
      zone_id: zoneId,
      kind: e?.kind ?? "pause",
      ttc_ms: e?.ttc_ms ?? null,
      dist_cm: e?.dist_cm ?? null,
      frame_id: frameId,
      ...(e ? { reason: e.reason } : {}),
    });
  }
}

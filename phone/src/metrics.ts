// On-device timings for the acceptance numbers: frame → hand landmarks per frame, landmarks → G1, the LK
// worker, and frame → STOP on screen (capture time of the frame that fired to the paint that shows red; the display's
// own next refresh comes after that). Each stop keeps its frame id, so a saved run lines up with the session record's
// g1 event for the same frame (its dist_cm and zone). Read only: it measures and never changes what runs.

export class Ring {
  private v: number[] = [];
  constructor(private readonly n = 600) {}
  push(x: number): void {
    this.v.push(x);
    if (this.v.length > this.n) this.v.shift();
  }
  get count(): number {
    return this.v.length;
  }
  q(p: number): number | null {
    if (!this.v.length) return null;
    const s = this.v.slice().sort((a, b) => a - b);
    return s[Math.min(s.length - 1, Math.floor(p * s.length))]!;
  }
  all(): readonly number[] {
    return this.v;
  }
}

export interface Snapshot {
  frames: number;
  hands_p50: number | null;
  hands_p95: number | null;
  g1_p95: number | null;
  flow_p50: number | null;
  flow_p95: number | null;
  stop_p50: number | null;
  stop_p95: number | null;
  stops: number[];
  /** Each stop's frame (the id in its g1 event) and its frame → STOP ms, oldest first. */
  stop_frames: { frame_id: string; ms: number }[];
  frames_sent: number;
  frames_held: number;
}

export class Metrics {
  readonly hands = new Ring();
  readonly g1 = new Ring();
  readonly flow = new Ring();
  readonly stop = new Ring(200);
  private readonly stopFrames: { frame_id: string; ms: number }[] = [];
  /** The frame whose G1 stop is waiting to be painted, and when it was captured. */
  pendingStop: { frameId: string; captureMs: number } | null = null;
  frames = 0;
  /** What the session record hasn't had yet (the `timings` message): kept bounded while the socket is down. */
  private freshHands: number[] = [];
  private freshStops: { frame_id: string; ms: number }[] = [];

  /** One frame's hand landmarks, in ms. */
  handsTook(ms: number): void {
    this.hands.push(ms);
    this.freshHands.push(Math.round(ms * 10) / 10);
    if (this.freshHands.length > 600) this.freshHands.shift();
  }

  /** True when this paint timed a stop. */
  painted(now: number, red: boolean): boolean {
    if (!red || this.pendingStop === null) return false;
    const ms = now - this.pendingStop.captureMs;
    const one = { frame_id: this.pendingStop.frameId, ms: Math.round(ms * 10) / 10 };
    this.stop.push(ms);
    this.stopFrames.push(one);
    if (this.stopFrames.length > 200) this.stopFrames.shift();
    this.freshStops.push({ ...one });
    if (this.freshStops.length > 200) this.freshStops.shift();
    this.pendingStop = null;
    return true;
  }

  /** The timings since the last call, for the session record; null when there are none. */
  drain(): { hands_ms: number[]; stops: { frame_id: string; ms: number }[] } | null {
    if (!this.freshHands.length && !this.freshStops.length) return null;
    const out = { hands_ms: this.freshHands, stops: this.freshStops };
    this.freshHands = [];
    this.freshStops = [];
    return out;
  }

  snapshot(sent: number, held: number): Snapshot {
    const r = (x: number | null): number | null => (x === null ? null : Math.round(x * 10) / 10);
    return {
      frames: this.frames,
      hands_p50: r(this.hands.q(0.5)),
      hands_p95: r(this.hands.q(0.95)),
      g1_p95: r(this.g1.q(0.95)),
      flow_p50: r(this.flow.q(0.5)),
      flow_p95: r(this.flow.q(0.95)),
      stop_p50: r(this.stop.q(0.5)),
      stop_p95: r(this.stop.q(0.95)),
      stops: this.stop.all().map((x) => Math.round(x)),
      stop_frames: this.stopFrames.map((x) => ({ ...x })),
      frames_sent: sent,
      frames_held: held,
    };
  }
}

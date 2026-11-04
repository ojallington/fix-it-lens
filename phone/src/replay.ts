// ?replay=<id>: drives the real UI from a recorded session at its original timing, with the REPLAY badge. It opens no
// camera and no model API: the recorded frames are the camera, the recorded s2c messages are the server, and the
// recorded g1 events re-show the phone's own stops.

import type { G1, S2C } from "../../contracts/ts/src/ws.ts";

export interface RecordedEvent {
  t_ms: number;
  dir: "c2s" | "s2c" | "state";
  kind: string;
  msg?: Record<string, unknown>;
  [k: string]: unknown;
}

export interface Recording {
  meta: { session_id: string; [k: string]: unknown };
  events: RecordedEvent[];
  /** The id the server resolved (golden names the curated recording). */
  replay_id?: string;
  /** From the job's card; null fields when the recording has none. Public: the appliance's kind, never its house name. */
  summary?: { appliance_alias: string | null; job_title: string | null; steps_total: number | null } | null;
}

export type ReplayItem =
  | { t: number; what: "s2c"; msg: S2C }
  | { t: number; what: "frame"; frameId: string }
  | { t: number; what: "g1"; msg: G1 };

/** The recorder keeps the message under `msg`; older lines inline it. */
function body(e: RecordedEvent): Record<string, unknown> {
  return (e.msg ?? e) as Record<string, unknown>;
}

/** Each c2s line's shift onto the session's clock. A record made before the server stamped c2s lines with its own
 * clock has them on the phone's (time since the page loaded); each hello and the hello_ok that answers it pin the two
 * clocks together, until the next hello (a reconnect). A record on one clock gets about 0. */
function phoneShifts(events: RecordedEvent[]): number[] {
  const out: number[] = [];
  let shift = 0;
  events.forEach((e, i) => {
    if (e.dir === "c2s" && e.kind === "hello") {
      const ok = events.slice(i + 1).find((x) => x.dir === "s2c" && x.kind === "hello_ok");
      if (ok) shift = e.t_ms - ok.t_ms;
    }
    out.push(shift);
  });
  return out;
}

export function timeline(r: Recording): ReplayItem[] {
  const out: ReplayItem[] = [];
  const shifts = phoneShifts(r.events);
  r.events.forEach((e, i) => {
    const m = body(e);
    const t = e.t_ms - shifts[i]!;
    if (e.dir === "s2c") out.push({ t: e.t_ms, what: "s2c", msg: m as unknown as S2C });
    else if (e.dir !== "c2s") return; // state lines: picks, call outcomes, phases
    else if (e.kind === "frame" && typeof m.frame_id === "string") out.push({ t, what: "frame", frameId: m.frame_id });
    else if (e.kind === "g1") out.push({ t, what: "g1", msg: m as unknown as G1 });
  });
  out.sort((a, b) => a.t - b.t);
  const t0 = out[0]?.t ?? 0;
  for (const it of out) it.t -= t0;
  return out;
}

export function frameUrl(apiBase: string, sessionId: string, frameId: string): string {
  return `${apiBase}/replay/${encodeURIComponent(sessionId)}/frames/${encodeURIComponent(frameId)}.jpg`;
}

export async function loadRecording(apiBase: string, id: string, fetcher: typeof fetch): Promise<Recording> {
  const r = await fetcher(`${apiBase}/replay/${encodeURIComponent(id)}`);
  if (!r.ok) throw new Error(`replay ${id}: HTTP ${r.status}`);
  return (await r.json()) as Recording;
}

/** Plays a timeline against a clock. `tick(now)` emits everything due; the caller drives it from rAF or a test. */
export class Player {
  private i = 0;
  private start: number | null = null;

  constructor(
    private readonly items: ReplayItem[],
    private readonly emit: (it: ReplayItem) => void,
  ) {}

  get done(): boolean {
    return this.i >= this.items.length;
  }

  tick(now: number): void {
    this.start ??= now;
    const t = now - this.start;
    while (this.i < this.items.length && this.items[this.i]!.t <= t) this.emit(this.items[this.i++]!);
  }
}

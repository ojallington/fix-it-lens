// The phone's WebSocket client: hello and resume by session id and last seq, a 2 s heartbeat, reconnect after 5 s of
// silence with backoff (0.5 → 4 s), and latest-wins camera frames that are skipped while the socket is backed up.
// A close with 1008 is the server refusing the hello (no rules for the country, a refused job, an unknown token or
// appliance, the day's cap): it is final, so the client stops and says so instead of retrying forever.

import type { C2S, Frame, S2C } from "../../contracts/ts/src/ws.ts";

export const HEARTBEAT_MS = 2000;
export const SILENCE_MS = 5000;
/** A heartbeat tick this late means the page itself was stalled (a WASM compile on the main thread), not the server. */
export const LATE_TICK_MS = 1000;
export const MAX_BUFFERED = 256 * 1024;
export const REFUSED = 1008;
/** A spend cap was reached: final like 1008, and the close reason is the CapError JSON (ws.schema.json CapClose). */
export const CAPPED = 4429;
/** The household was deleted (ws.schema.json GoneClose): final, and every later hello gets the same close. */
export const GONE = 4410;
const BACKOFF_MS = [500, 1000, 2000, 4000];

/** The subset of WebSocket the client uses, so tests can pass a fake. */
export interface SocketLike {
  readonly readyState: number;
  readonly bufferedAmount: number;
  onopen: ((ev: unknown) => void) | null;
  onmessage: ((ev: { data: unknown }) => void) | null;
  onclose: ((ev: unknown) => void) | null;
  onerror: ((ev: unknown) => void) | null;
  send(data: string): void;
  close(): void;
}

// Distributes Omit over the union, so each message keeps its own fields.
type Unsent<T> = T extends unknown ? Omit<T, "seq" | "t_ms"> & { t_ms?: number } : never;
export type Outgoing = Unsent<C2S>;

export interface ClientOptions {
  url: string;
  lang: string;
  household?: string | null;
  replayId?: string | null;
  applianceId?: string | null;
  jobId?: string | null;
  open?: (url: string) => SocketLike;
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (h: unknown) => void;
}

const OPEN = 1;

export class WsClient {
  sessionId: string | null = null;
  lastSeq: number | null = null;
  connected = false;
  framesSkipped = 0;

  private sock: SocketLike | null = null;
  private seq = 0;
  private lastHeard = 0;
  private attempt = 0;
  private heartbeat: unknown = null;
  private retry: unknown = null;
  private closed = false;
  private listeners = new Set<(m: S2C) => void>();
  private statusListeners = new Set<(up: boolean) => void>();
  private refusedListeners = new Set<() => void>();
  private cappedListeners = new Set<(reason: string) => void>();
  private goneListeners = new Set<() => void>();
  refused = false;

  constructor(private readonly o: ClientOptions) {}

  private now(): number {
    return (this.o.now ?? (() => performance.now()))();
  }

  private timer(fn: () => void, ms: number): unknown {
    return (this.o.setTimer ?? ((f, t) => setTimeout(f, t)))(fn, ms);
  }

  private clear(h: unknown): void {
    if (h !== null) (this.o.clearTimer ?? ((x) => clearTimeout(x as ReturnType<typeof setTimeout>)))(h);
  }

  on(fn: (m: S2C) => void): () => void {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  onStatus(fn: (up: boolean) => void): () => void {
    this.statusListeners.add(fn);
    return () => this.statusListeners.delete(fn);
  }

  /** A spend cap closed the session (4429), with the close reason; no reconnect follows. */
  onCapped(fn: (reason: string) => void): () => void {
    this.cappedListeners.add(fn);
    return () => this.cappedListeners.delete(fn);
  }

  /** The household was deleted (4410); no reconnect follows. */
  onGone(fn: () => void): () => void {
    this.goneListeners.add(fn);
    return () => this.goneListeners.delete(fn);
  }

  /** The server refused the hello (close 1008); no reconnect follows. */
  onRefused(fn: () => void): () => void {
    this.refusedListeners.add(fn);
    return () => this.refusedListeners.delete(fn);
  }

  connect(): void {
    this.closed = false;
    const open = this.o.open ?? ((u: string) => new WebSocket(u) as unknown as SocketLike);
    const s = open(this.o.url);
    this.sock = s;
    s.onopen = () => {
      this.attempt = 0;
      this.lastHeard = this.now();
      this.send({
        type: "hello",
        session_id: this.sessionId,
        last_seq: this.lastSeq,
        household: this.o.household ?? null,
        client: "phone",
        lang: this.o.lang,
        replay_id: this.o.replayId ?? null,
        appliance_id: this.o.applianceId ?? null,
        job_id: this.o.jobId ?? null,
      });
      this.beat();
    };
    s.onmessage = (ev) => {
      this.lastHeard = this.now();
      if (typeof ev.data !== "string") return;
      const m = JSON.parse(ev.data) as S2C;
      // A resume re-sends what came after last_seq; a notice always passes (a refusal's has seq 0).
      if (this.lastSeq !== null && m.seq <= this.lastSeq && m.type !== "hello_ok" && m.type !== "notice") return;
      if (m.type === "hello_ok") {
        this.sessionId = m.session_id;
        this.setUp(true);
      } else if (m.type !== "notice" || m.seq > (this.lastSeq ?? -1)) this.lastSeq = m.seq;
      for (const fn of this.listeners) fn(m);
    };
    s.onclose = (ev) => {
      const e = ev as { code?: number; reason?: unknown } | null;
      this.dropped(e?.code, typeof e?.reason === "string" ? e.reason : "");
    };
    s.onerror = () => undefined; // a close always follows, with its code
  }

  /** The person ended the session: the server closes it and records how it went; no reconnect follows. */
  end(): void {
    this.send({ type: "tap", action: "end" });
    this.close();
  }

  close(): void {
    this.closed = true;
    this.clear(this.heartbeat);
    this.clear(this.retry);
    this.sock?.close();
    this.setUp(false);
  }

  private setUp(up: boolean): void {
    if (this.connected === up) return;
    this.connected = up;
    for (const fn of this.statusListeners) fn(up);
  }

  private beat(): void {
    this.clear(this.heartbeat);
    const due = this.now() + HEARTBEAT_MS;
    this.heartbeat = this.timer(() => {
      // Whatever the server sent during a stall is still queued behind this tick, so the silence isn't the server's.
      if (this.now() - due > LATE_TICK_MS) this.lastHeard = this.now();
      if (this.now() - this.lastHeard > SILENCE_MS) {
        this.sock?.close();
        this.dropped();
        return;
      }
      this.send({ type: "ping" });
      this.beat();
    }, HEARTBEAT_MS);
  }

  private dropped(code?: number, reason = ""): void {
    if (this.sock === null) return;
    const s = this.sock;
    this.sock = null;
    s.onopen = s.onmessage = s.onclose = s.onerror = null;
    this.clear(this.heartbeat);
    this.setUp(false);
    if (this.closed) return;
    if (code === CAPPED) {
      this.closed = true;
      this.refused = true;
      for (const fn of this.cappedListeners) fn(reason);
      return;
    }
    if (code === GONE) {
      this.closed = true;
      this.refused = true;
      for (const fn of this.goneListeners) fn();
      return;
    }
    if (code === REFUSED) {
      this.closed = true;
      this.refused = true;
      for (const fn of this.refusedListeners) fn();
      return;
    }
    const wait = BACKOFF_MS[Math.min(this.attempt, BACKOFF_MS.length - 1)]!;
    this.attempt++;
    this.retry = this.timer(() => this.connect(), wait);
  }

  /** Sends one message; returns false when the socket is not open. */
  send(m: Outgoing): boolean {
    const s = this.sock;
    if (!s || s.readyState !== OPEN) return false;
    const full = { ...m, seq: this.seq++, t_ms: m.t_ms ?? this.now() };
    s.send(JSON.stringify(full));
    return true;
  }

  /** Latest-wins: a frame is dropped, never queued, while more than 256 kB waits to go out. */
  sendFrame(f: Unsent<Frame>): boolean {
    const s = this.sock;
    if (!s || s.readyState !== OPEN || s.bufferedAmount > MAX_BUFFERED) {
      this.framesSkipped++;
      return false;
    }
    return this.send(f);
  }
}

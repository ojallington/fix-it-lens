import { describe, expect, it } from "vitest";
import { MAX_BUFFERED, WsClient, type SocketLike } from "../src/ws.ts";

class FakeSocket implements SocketLike {
  readyState = 0;
  bufferedAmount = 0;
  sent: Record<string, unknown>[] = [];
  onopen: ((ev: unknown) => void) | null = null;
  onmessage: ((ev: { data: unknown }) => void) | null = null;
  onclose: ((ev: unknown) => void) | null = null;
  onerror: ((ev: unknown) => void) | null = null;
  send(d: string): void {
    this.sent.push(JSON.parse(d));
  }
  close(): void {
    this.readyState = 3;
  }
  open(): void {
    this.readyState = 1;
    this.onopen?.({});
  }
  recv(m: object): void {
    this.onmessage?.({ data: JSON.stringify(m) });
  }
}

function rig() {
  let t = 0;
  const timers: { at: number; fn: () => void }[] = [];
  const socks: FakeSocket[] = [];
  const c = new WsClient({
    url: "wss://x/ws",
    lang: "nl",
    open: () => {
      const s = new FakeSocket();
      socks.push(s);
      return s;
    },
    now: () => t,
    setTimer: (fn, ms) => {
      const h = { at: t + ms, fn };
      timers.push(h);
      return h;
    },
    clearTimer: (h) => {
      const i = timers.indexOf(h as (typeof timers)[number]);
      if (i >= 0) timers.splice(i, 1);
    },
  });
  const advance = (ms: number): void => {
    const end = t + ms;
    for (;;) {
      timers.sort((a, b) => a.at - b.at);
      const next = timers[0];
      if (!next || next.at > end) break;
      timers.shift();
      t = Math.max(t, next.at);
      next.fn();
    }
    t = end;
  };
  /** The main thread blocked for ms: the clock moves, no timer fires until it ends. */
  const stall = (ms: number): void => {
    t += ms;
    advance(0);
  };
  return { c, socks, advance, stall };
}

describe("WsClient", () => {
  it("says hello, then resumes with the session id and last seq after a drop", () => {
    const { c, socks, advance } = rig();
    c.connect();
    socks[0]!.open();
    expect(socks[0]!.sent[0]).toMatchObject({ type: "hello", session_id: null, last_seq: null, client: "phone" });
    socks[0]!.recv({ type: "hello_ok", seq: 0, t_ms: 0, session_id: "s1", resumed: false, mode: "api" });
    socks[0]!.recv({ type: "notice", seq: 1, t_ms: 1, kind: "resumed" });
    socks[0]!.recv({ type: "pong", seq: 2, t_ms: 2 });
    socks[0]!.onclose?.({});
    expect(c.connected).toBe(false);
    advance(500);
    socks[1]!.open();
    expect(socks[1]!.sent[0]).toMatchObject({ type: "hello", session_id: "s1", last_seq: 2 });
  });

  it("ends the session: the server is told, the socket closes and nothing reconnects", () => {
    const { c, socks, advance } = rig();
    c.connect();
    socks[0]!.open();
    socks[0]!.recv({ type: "hello_ok", seq: 0, t_ms: 0, session_id: "s1", resumed: false, mode: "api" });
    c.end();
    expect(socks[0]!.sent.at(-1)).toMatchObject({ type: "tap", action: "end" });
    expect(socks[0]!.readyState).toBe(3);
    socks[0]!.onclose?.({ code: 1000 });
    advance(10000);
    expect(socks.length).toBe(1);
    expect(c.connected).toBe(false);
  });

  it("treats a 1008 close as the server's refusal: no reconnect, and the refusal is reported", () => {
    const { c, socks, advance } = rig();
    let refused = 0;
    const kinds: string[] = [];
    c.onRefused(() => void refused++);
    c.on((m) => m.type === "notice" && kinds.push(m.kind));
    c.connect();
    socks[0]!.open();
    socks[0]!.recv({ type: "notice", seq: 0, t_ms: 0, kind: "no_rules_for_country", text: null });
    socks[0]!.onclose?.({ code: 1008 });
    advance(10000);
    expect(socks.length).toBe(1);
    expect(refused).toBe(1);
    expect(kinds).toEqual(["no_rules_for_country"]);
  });

  it("drops s2c messages a resume re-sends", () => {
    const { c, socks } = rig();
    const got: number[] = [];
    c.on((m) => got.push(m.seq));
    c.connect();
    socks[0]!.open();
    socks[0]!.recv({ type: "hello_ok", seq: 0, t_ms: 0, session_id: "s1", resumed: false, mode: "api" });
    socks[0]!.recv({ type: "pong", seq: 1, t_ms: 0 });
    socks[0]!.recv({ type: "pong", seq: 1, t_ms: 0 });
    socks[0]!.recv({ type: "pong", seq: 2, t_ms: 0 });
    expect(got).toEqual([0, 1, 2]);
  });

  it("pings every 2 s and reconnects after 5 s of silence", () => {
    const { c, socks, advance } = rig();
    c.connect();
    socks[0]!.open();
    advance(2000);
    expect(socks[0]!.sent.some((m) => m.type === "ping")).toBe(true);
    advance(4100);
    expect(c.connected).toBe(false);
    advance(600);
    expect(socks.length).toBe(2);
  });

  it("takes a heartbeat tick that fires over 1 s late for a stalled page, not a silent server", () => {
    const { c, socks, advance, stall } = rig();
    c.connect();
    socks[0]!.open();
    socks[0]!.recv({ type: "hello_ok", seq: 0, t_ms: 0, session_id: "s1", resumed: false, mode: "api" });
    stall(8000);
    expect(c.connected).toBe(true);
    expect(socks.length).toBe(1);
    expect(socks[0]!.sent.some((m) => m.type === "ping")).toBe(true);
    // Real silence after the stall still drops the socket.
    advance(6100);
    expect(c.connected).toBe(false);
  });

  it("skips frames while more than 256 kB is buffered, and never queues them", () => {
    const { c, socks } = rig();
    c.connect();
    socks[0]!.open();
    socks[0]!.bufferedAmount = MAX_BUFFERED + 1;
    const f = { type: "frame" as const, frame_id: "f1", jpeg_1024: "", phash: "0".repeat(16) };
    expect(c.sendFrame(f)).toBe(false);
    socks[0]!.bufferedAmount = 0;
    expect(c.sendFrame({ ...f, frame_id: "f2" })).toBe(true);
    expect(socks[0]!.sent.filter((m) => m.type === "frame").map((m) => m.frame_id)).toEqual(["f2"]);
    expect(c.framesSkipped).toBe(1);
  });
});

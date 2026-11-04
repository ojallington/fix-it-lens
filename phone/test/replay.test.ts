import { describe, expect, it } from "vitest";
import { Player, timeline, type Recording } from "../src/replay.ts";

const REC: Recording = {
  meta: { session_id: "m1" },
  events: [
    { t_ms: 1000, dir: "c2s", kind: "frame", msg: { type: "frame", frame_id: "f1", jpeg_1024: null } },
    { t_ms: 1200, dir: "s2c", kind: "checking", msg: { type: "checking", seq: 3, t_ms: 1200, step_id: "s" } },
    { t_ms: 1300, dir: "state", kind: "frame", msg: { frame_id: "nope" } },
    { t_ms: 1500, dir: "c2s", kind: "g1", msg: { type: "g1", kind: "stop", zone_id: "z", frame_id: "f1" } },
    { t_ms: 1100, dir: "c2s", kind: "tap", msg: { type: "tap", action: "done" } },
  ],
};

describe("replay", () => {
  it("builds a timeline from s2c messages, recorded frames and the phone's g1 events; ignores state lines", () => {
    const tl = timeline(REC);
    expect(tl.map((x) => [x.t, x.what])).toEqual([
      [0, "frame"],
      [200, "s2c"],
      [500, "g1"],
    ]);
  });

  it("plays at the original timing", () => {
    const got: string[] = [];
    const p = new Player(timeline(REC), (it) => got.push(it.what));
    p.tick(10_000);
    expect(got).toEqual(["frame"]);
    p.tick(10_250);
    expect(got).toEqual(["frame", "s2c"]);
    p.tick(10_600);
    expect(p.done).toBe(true);
  });

  it("puts an older record's phone-clock lines on the session's clock, again after each reconnect", () => {
    const hello = (t: number) => ({ t_ms: t, dir: "c2s" as const, kind: "hello", msg: { type: "hello" } });
    const ok = (t: number) => ({ t_ms: t, dir: "s2c" as const, kind: "hello_ok", msg: { type: "hello_ok", seq: 0, t_ms: t } });
    const frame = (t: number, id: string) => ({ t_ms: t, dir: "c2s" as const, kind: "frame", msg: { type: "frame", frame_id: id } });
    const tl = timeline({
      meta: { session_id: "m2" },
      events: [hello(190_000), ok(20), frame(190_100, "a"), frame(190_600, "b"), hello(222_000), ok(32_000), frame(222_100, "c")],
    });
    expect(tl.map((x) => [x.t, x.what === "frame" ? x.frameId : x.what])).toEqual([
      [0, "s2c"],
      [100, "a"],
      [600, "b"],
      [31_980, "s2c"],
      [32_080, "c"],
    ]);
  });
});


// On-device timings: a stop is timed once, from the capture of the frame that fired it to the paint that shows red,
// and keeps that frame's id so a saved run lines up with the session record.
import { describe, expect, it } from "vitest";
import { Metrics } from "../src/metrics.ts";

describe("metrics", () => {
  it("times each stop once, to the first red paint, with its frame id", () => {
    const m = new Metrics();
    m.painted(100, true); // red with nothing pending: not a stop of this frame
    m.pendingStop = { frameId: "f1", captureMs: 1000 };
    m.painted(1010, false); // not red yet
    m.painted(1046.44, true);
    m.painted(1063, true); // still red: the same stop, not counted again
    m.pendingStop = { frameId: "f9", captureMs: 2000 };
    m.painted(2090, true);
    const s = m.snapshot(3, 1);
    expect(s.stops).toEqual([46, 90]);
    expect(s.stop_frames).toEqual([{ frame_id: "f1", ms: 46.4 }, { frame_id: "f9", ms: 90 }]);
    expect(s.stop_p95).toBe(90);
    expect(s).toMatchObject({ frames_sent: 3, frames_held: 1 });
  });

  it("hands the session record each frame's landmark time and each stop once, then nothing until there are more", () => {
    const m = new Metrics();
    expect(m.drain()).toBeNull();
    m.handsTook(12.34);
    m.handsTook(9);
    m.pendingStop = { frameId: "f3", captureMs: 100 };
    expect(m.painted(150, true)).toBe(true);
    expect(m.painted(170, true)).toBe(false);
    expect(m.drain()).toEqual({ hands_ms: [12.3, 9], stops: [{ frame_id: "f3", ms: 50 }] });
    expect(m.drain()).toBeNull();
    expect(m.snapshot(0, 0).hands_p95).toBe(12.3);
  });
});

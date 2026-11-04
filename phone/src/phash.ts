// 64-bit perceptual hash (DCT of a 32x32 gray thumbnail, the 8x8 lowest frequencies against their median), and the
// frame gate built on it: SAM is the live-camera cost, so no frame goes up while the view has not moved.

const N = 32;
const K = 8;

const COS: Float64Array = (() => {
  const c = new Float64Array(K * N);
  for (let u = 0; u < K; u++) for (let x = 0; x < N; x++) c[u * N + x] = Math.cos(((2 * x + 1) * u * Math.PI) / (2 * N));
  return c;
})();

/** Box-downsamples a gray image (one byte per pixel, row-major) to 32x32. */
export function thumb32(gray: ArrayLike<number>, w: number, h: number): Float64Array {
  const out = new Float64Array(N * N);
  for (let ty = 0; ty < N; ty++) {
    const y0 = Math.floor((ty * h) / N);
    const y1 = Math.max(y0 + 1, Math.floor(((ty + 1) * h) / N));
    for (let tx = 0; tx < N; tx++) {
      const x0 = Math.floor((tx * w) / N);
      const x1 = Math.max(x0 + 1, Math.floor(((tx + 1) * w) / N));
      let s = 0;
      for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) s += gray[y * w + x]!;
      out[ty * N + tx] = s / ((y1 - y0) * (x1 - x0));
    }
  }
  return out;
}

/** The hash as 16 hex digits, as the ws Frame contract wants. */
export function phash(gray: ArrayLike<number>, w: number, h: number): string {
  const t = thumb32(gray, w, h);
  const rows = new Float64Array(K * N);
  for (let u = 0; u < K; u++)
    for (let y = 0; y < N; y++) {
      let s = 0;
      for (let x = 0; x < N; x++) s += t[y * N + x]! * COS[u * N + x]!;
      rows[u * N + y] = s;
    }
  const d: number[] = [];
  for (let v = 0; v < K; v++)
    for (let u = 0; u < K; u++) {
      let s = 0;
      for (let y = 0; y < N; y++) s += rows[u * N + y]! * COS[v * N + y]!;
      d.push(s);
    }
  const sorted = d.slice(1).sort((a, b) => a - b); // DC term excluded from the median
  const med = sorted[sorted.length >> 1]!;
  let hex = "";
  for (let i = 0; i < 64; i += 4) {
    let nib = 0;
    for (let b = 0; b < 4; b++) nib = (nib << 1) | (d[i + b]! > med ? 1 : 0);
    hex += nib.toString(16);
  }
  return hex;
}

export function hamming(a: string, b: string): number {
  let n = 0;
  for (let i = 0; i < 16; i += 4) {
    let x = parseInt(a.slice(i, i + 4), 16) ^ parseInt(b.slice(i, i + 4), 16);
    while (x) {
      n += x & 1;
      x >>>= 1;
    }
  }
  return n;
}

export interface GateOptions {
  /** Bits that count as "the view moved" (out of 64). */
  movedBits: number;
  /** At most one moved frame per this many ms: SAM re-anchors about once a second. */
  minIntervalMs: number;
  /** While a step check runs the judge needs fresh frames even from a still view. */
  checkIntervalMs: number;
}

export const GATE_DEFAULTS: GateOptions = { movedBits: 8, minIntervalMs: 1000, checkIntervalMs: 500 };

export type GateReason = "first" | "moved" | "tracker" | "checking" | "fresh";

/** Decides which camera frames go up. Everything else stays on the phone. */
export class FrameGate {
  private lastHash: string | null = null;
  private lastT = -Infinity;
  checking = false;
  /** Set while the session holds for a frame newer than its limit: a still view must still send one. */
  wantsFresh = false;
  /** Set by the tracker when the transform since the last anchor passes its re-anchor threshold. */
  trackerWantsAnchor = false;
  sent = 0;
  held = 0;

  constructor(private readonly o: GateOptions = GATE_DEFAULTS) {}

  offer(hash: string, t: number): GateReason | null {
    const since = t - this.lastT;
    let why: GateReason | null = null;
    if (this.lastHash === null) why = "first";
    else if (this.checking && since >= this.o.checkIntervalMs) why = "checking";
    else if (this.wantsFresh && since >= this.o.checkIntervalMs) why = "fresh";
    else if (since >= this.o.minIntervalMs) {
      if (hamming(hash, this.lastHash) >= this.o.movedBits) why = "moved";
      else if (this.trackerWantsAnchor) why = "tracker";
    }
    if (why === null) {
      this.held++;
      return null;
    }
    this.lastHash = hash;
    this.lastT = t;
    this.trackerWantsAnchor = false;
    this.sent++;
    return why;
  }

  /** After a drop the server may have lost its anchor; send the next frame. */
  reset(): void {
    this.lastHash = null;
  }
}

// The LK worker: pyramidal Lucas–Kanade on a 480 px gray frame, corners seeded inside the outlines' box (grown 30%),
// points whose flow disagrees with the median by more than 3x dropped, then a RANSAC affine (estimateAffine2D, 3 px).
// It also computes the frame's perceptual hash from the same gray pixels. OpenCV.js loads when the worker starts.

/* eslint-disable @typescript-eslint/no-explicit-any */
import { phash } from "./phash.ts";

export interface FlowIn {
  frameId: string;
  bitmap: ImageBitmap;
  /** Width and height of image space (the frame as sent: long edge 1024). */
  imgW: number;
  imgH: number;
  /** Box to seed corners in, image space; null seeds over the whole frame. */
  roi: { x0: number; y0: number; x1: number; y1: number } | null;
}

export interface FlowOut {
  frameId: string;
  delta: [number, number, number, number, number, number] | null;
  points: number;
  inliers: number;
  phash: string;
  ms: number;
  error?: string;
}

const TRACK_W = 480;
const SEED = 80;
const RESEED_BELOW = 30;

let cv: any = null;
let prev: any = null; // cv.Mat gray
let prevPts: any = null; // cv.Mat CV_32FC2
let canvas: OffscreenCanvas | null = null;

// OpenCV.js is run as the classic script it is (public/cv/opencv.js, copied by scripts/assets.mjs), by a global eval.
// Imported as an ES module it runs in strict mode and spins forever on load, in the worker and on the main thread
// alike. Its Module is also thenable (its then() hands back the Module), so it is never resolved through a promise.
void fetch(`${import.meta.env.BASE_URL}cv/opencv.js`)
  .then((r) => r.text())
  .then((src) => {
    (0, eval)(src);
    const c = (self as any).cv;
    if (c.Mat) cv = c;
    else c.onRuntimeInitialized = () => (cv = c);
  });

function seed(gray: any, roi: FlowIn["roi"], s: number): any {
  const mask = new cv.Mat.zeros(gray.rows, gray.cols, cv.CV_8UC1);
  if (roi) {
    const gw = (roi.x1 - roi.x0) * 0.3;
    const gh = (roi.y1 - roi.y0) * 0.3;
    const x0 = Math.max(0, Math.floor((roi.x0 - gw) / s));
    const y0 = Math.max(0, Math.floor((roi.y0 - gh) / s));
    const x1 = Math.min(gray.cols, Math.ceil((roi.x1 + gw) / s));
    const y1 = Math.min(gray.rows, Math.ceil((roi.y1 + gh) / s));
    cv.rectangle(mask, new cv.Point(x0, y0), new cv.Point(x1, y1), new cv.Scalar(255), -1);
  } else mask.setTo(new cv.Scalar(255));
  const pts = new cv.Mat();
  cv.goodFeaturesToTrack(gray, pts, SEED, 0.01, 6, mask);
  mask.delete();
  return pts;
}

async function step(m: FlowIn): Promise<FlowOut> {
  const t0 = performance.now();
  const h = Math.round((TRACK_W * m.bitmap.height) / m.bitmap.width);
  canvas ??= new OffscreenCanvas(TRACK_W, h);
  if (canvas.height !== h) canvas.height = h;
  const ctx = canvas.getContext("2d", { willReadFrequently: true })!;
  ctx.drawImage(m.bitmap, 0, 0, TRACK_W, h);
  m.bitmap.close();
  const rgba = ctx.getImageData(0, 0, TRACK_W, h);
  const g8 = new Uint8Array(TRACK_W * h);
  for (let i = 0, j = 0; j < g8.length; i += 4, j++)
    g8[j] = (rgba.data[i]! * 77 + rgba.data[i + 1]! * 150 + rgba.data[i + 2]! * 29) >> 8;
  const hash = phash(g8, TRACK_W, h);

  if (!cv) {
    return { frameId: m.frameId, delta: null, points: 0, inliers: 0, phash: hash, ms: performance.now() - t0 };
  }

  const s = m.imgW / TRACK_W; // image px per track px
  const gray = cv.matFromArray(h, TRACK_W, cv.CV_8UC1, Array.from(g8));
  let delta: FlowOut["delta"] = null;
  let points = 0;
  let inliers = 0;

  if (prev && prevPts && prevPts.rows > 0) {
    const next = new cv.Mat();
    const status = new cv.Mat();
    const err = new cv.Mat();
    cv.calcOpticalFlowPyrLK(prev, gray, prevPts, next, status, err, new cv.Size(21, 21), 3);
    const a: number[] = [];
    const b: number[] = [];
    for (let i = 0; i < status.rows; i++) {
      if (!status.data[i]) continue;
      a.push(prevPts.data32F[2 * i], prevPts.data32F[2 * i + 1]);
      b.push(next.data32F[2 * i], next.data32F[2 * i + 1]);
    }
    // Drop points dragged by a passing hand: flow more than 3x the median away from the median flow.
    const n = a.length / 2;
    const fx = Array.from({ length: n }, (_, i) => b[2 * i]! - a[2 * i]!);
    const fy = Array.from({ length: n }, (_, i) => b[2 * i + 1]! - a[2 * i + 1]!);
    const med = (v: number[]): number => v.slice().sort((p, q) => p - q)[v.length >> 1] ?? 0;
    const mx = med(fx);
    const my = med(fy);
    const dev = fx.map((x, i) => Math.hypot(x - mx, fy[i]! - my));
    const md = Math.max(0.5, med(dev));
    const keepA: number[] = [];
    const keepB: number[] = [];
    for (let i = 0; i < n; i++)
      if (dev[i]! <= 3 * md) {
        keepA.push(a[2 * i]!, a[2 * i + 1]!);
        keepB.push(b[2 * i]!, b[2 * i + 1]!);
      }
    points = keepA.length / 2;
    if (points >= 3) {
      const src = cv.matFromArray(points, 1, cv.CV_32FC2, keepA);
      const dst = cv.matFromArray(points, 1, cv.CV_32FC2, keepB);
      const inl = new cv.Mat();
      const M = cv.estimateAffine2D(src, dst, inl, cv.RANSAC, 3);
      if (M && !M.empty()) {
        const d = M.data64F;
        delta = [d[0], d[1], d[2] * s, d[3], d[4], d[5] * s];
        for (let i = 0; i < inl.rows; i++) inliers += inl.data[i] ? 1 : 0;
      }
      M?.delete();
      src.delete();
      dst.delete();
      inl.delete();
      prevPts.delete();
      prevPts = points >= RESEED_BELOW ? cv.matFromArray(points, 1, cv.CV_32FC2, keepB) : null;
    } else {
      prevPts.delete();
      prevPts = null;
    }
    next.delete();
    status.delete();
    err.delete();
  }
  if (!prevPts || prevPts.rows < RESEED_BELOW) {
    prevPts?.delete();
    prevPts = seed(gray, m.roi, s);
  }
  prev?.delete();
  prev = gray;
  return { frameId: m.frameId, delta, points, inliers, phash: hash, ms: performance.now() - t0 };
}

self.onmessage = (ev: MessageEvent<FlowIn>) => {
  const t0 = performance.now();
  void step(ev.data)
    .catch((e: unknown): FlowOut => {
      // Always answer: the tracker then loses confidence and G1 fails closed, rather than waiting forever.
      prevPts?.delete?.();
      prevPts = null;
      return { frameId: ev.data.frameId, delta: null, points: 0, inliers: 0, phash: "0".repeat(16), ms: performance.now() - t0, error: String((e as Error)?.stack ?? e) };
    })
    .then((out) => (self as unknown as Worker).postMessage(out));
};

// MediaPipe Hands (Apache-2.0) on every camera frame, on the device. Returns every landmark of every hand in image
// space: G1 treats the nearest landmark as the fingertip, so a palm or knuckle that leads the reach counts too.

import { FilesetResolver, HandLandmarker } from "@mediapipe/tasks-vision";
import type { Pt } from "./geom.ts";

export interface HandsResult {
  points: Pt[];
  hands: number;
  ms: number;
}

export class Hands {
  private constructor(private readonly lm: HandLandmarker) {}

  static async load(base = "./mp"): Promise<Hands> {
    const files = await FilesetResolver.forVisionTasks(`${base}/wasm`);
    const lm = await HandLandmarker.createFromOptions(files, {
      baseOptions: { modelAssetPath: `${base}/hand_landmarker.task`, delegate: "GPU" },
      runningMode: "VIDEO",
      numHands: 2,
      minHandDetectionConfidence: 0.4,
      minHandPresenceConfidence: 0.4,
      minTrackingConfidence: 0.4,
    });
    return new Hands(lm);
  }

  /** Landmarks for the frame on `source`, scaled to image space (imgW x imgH). */
  detect(source: TexImageSource, tMs: number, imgW: number, imgH: number): HandsResult {
    const t0 = performance.now();
    const r = this.lm.detectForVideo(source as HTMLVideoElement, tMs);
    const points: Pt[] = [];
    for (const hand of r.landmarks) for (const p of hand) points.push({ x: p.x * imgW, y: p.y * imgH });
    return { points, hands: r.landmarks.length, ms: performance.now() - t0 };
  }
}

// The camera: the back camera at 30 fps, frames delivered with their capture time so G1 can tell a stale frame,
// and the JPEG (long edge 1024) the server gets. Image space throughout the phone is that JPEG's pixels.

export const LONG_EDGE = 1024;

export interface FrameTick {
  /** performance.now() clock: when the camera captured it (or presented it, where capture time is unknown). */
  captureMs: number;
  /** How old the frame is now. */
  ageMs: number;
  frameId: string;
}

export function imageSize(videoW: number, videoH: number): { w: number; h: number } {
  const s = LONG_EDGE / Math.max(videoW, videoH);
  return { w: Math.round(videoW * s), h: Math.round(videoH * s) };
}

export async function openCamera(video: HTMLVideoElement): Promise<MediaStream> {
  const stream = await navigator.mediaDevices.getUserMedia({
    audio: false,
    video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30 } },
  });
  video.srcObject = stream;
  video.muted = true;
  video.playsInline = true;
  await video.play();
  return stream;
}

let n = 0;

/** Calls fn once per decoded camera frame (requestVideoFrameCallback), until the returned stop is called. */
export function eachFrame(video: HTMLVideoElement, fn: (t: FrameTick) => void): () => void {
  let live = true;
  let handle = 0;
  const loop = (now: number, meta: VideoFrameCallbackMetadata): void => {
    if (!live) return;
    const capture = meta.captureTime ?? meta.presentationTime ?? now;
    fn({ captureMs: capture, ageMs: Math.max(0, performance.now() - capture), frameId: `f${Date.now().toString(36)}-${n++}` });
    handle = video.requestVideoFrameCallback(loop);
  };
  handle = video.requestVideoFrameCallback(loop);
  return () => {
    live = false;
    video.cancelVideoFrameCallback(handle);
  };
}

/** The frame as the server gets it: JPEG, long edge 1024, base64 without the data: prefix. */
export async function jpeg1024(source: CanvasImageSource, w: number, h: number, quality = 0.8): Promise<string> {
  const c = new OffscreenCanvas(w, h);
  c.getContext("2d")!.drawImage(source, 0, 0, w, h);
  const blob = await c.convertToBlob({ type: "image/jpeg", quality });
  const bytes = new Uint8Array(await blob.arrayBuffer());
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

// Puts MediaPipe's wasm and the hand landmarker model under public/mp/, and OpenCV.js under public/cv/ (both gitignored).
// Run by `npm run assets`.
import { cpSync, existsSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const out = join(root, "public", "mp");
mkdirSync(out, { recursive: true });
cpSync(join(root, "node_modules", "@mediapipe", "tasks-vision", "wasm"), join(out, "wasm"), { recursive: true });
const MODEL = "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task";
const dst = join(out, "hand_landmarker.task");
if (!existsSync(dst)) {
  const r = await fetch(MODEL);
  if (!r.ok) throw new Error(`hand model: HTTP ${r.status}`);
  writeFileSync(dst, Buffer.from(await r.arrayBuffer()));
}
const cvOut = join(root, "public", "cv");
mkdirSync(cvOut, { recursive: true });
cpSync(join(root, "node_modules", "@techstark", "opencv-js", "dist", "opencv.js"), join(cvOut, "opencv.js"));
console.log("assets ok:", out, cvOut);

// Browser entry: real dependencies in, then App. Nothing here decides anything about safety.

import { App, API } from "./app.ts";
import { Listener, Recorder } from "./audio.ts";
import { eachFrame, jpeg1024, openCamera } from "./camera.ts";
import type { Disclaimers } from "./disclaimer.ts";
import { bundleOf, newBuild } from "./fresh.ts";
import { authed, consentLine, localTokenStore, type ConsentFile } from "./household.ts";
import type { Constants } from "./g1.ts";
import { Hands } from "./hands.ts";
import { browserInstall } from "./install.ts";
import { adoptHomeLang, chooseLang, localLangStore, pickLang } from "./lang.ts";
import { browserSynth } from "./speech.ts";
import { browserAlarm } from "./stop.ts";
import type { BarLayout, PhoneNumber } from "./ui.ts";
import FlowWorker from "./flow.worker.ts?worker";

// G1's constants come from rules/g1.json at build time; the contract's example has the same values and stands in
// when the file is missing.
const rules = import.meta.glob<{ constants: Constants }>("../../rules/g1.json", { eager: true, import: "default" });
const example = import.meta.glob<{ constants: Constants }>("../../contracts/examples/g1/example.json", { eager: true, import: "default" });
const g1 = (Object.values(rules)[0] ?? Object.values(example)[0])!.constants;
// The fixed disclaimer texts, generated from rules/disclaimer.yaml.
const disclaimers = Object.values(import.meta.glob<Disclaimers>("../../rules/disclaimer.json", { eager: true, import: "default" }))[0] ?? null;
// The numbers the rules' texts name, per country (rules/numbers.json): any of them may appear in a stop script.
const numbersFile = Object.values(import.meta.glob<Record<string, PhoneNumber[]>>("../../rules/numbers.json", { eager: true, import: "default" }))[0] ?? {};
const numbers = Object.values(numbersFile).flat();
// Each stop script's lead line, the rule's own (rules/stops.json): the stop shows it before the call buttons.
const stopLeads = Object.values(import.meta.glob<Record<string, string>>("../../rules/stops.json", { eager: true, import: "default" }))[0] ?? {};
const FIRST_RUN_KEY = "fixit.first_run.v1";
const CLEAR_KEY = "fixit.clear_used.v1";
const household = localTokenStore();
const langs = localLangStore();
const lang = chooseLang(location.search, langs, navigator.language);
// The in-app consent every door shows (eval/consent/app-consent.json), once it is published.
const appConsentFile = Object.values(import.meta.glob<ConsentFile>("../../eval/consent/app-consent.json", { eager: true, import: "default" }))[0] ?? null;

let heading: number | null = null;
const onOrient = (e: DeviceOrientationEvent): void => {
  if (e.alpha !== null) heading = e.alpha;
};
addEventListener("deviceorientationabsolute", onOrient as EventListener);
addEventListener("deviceorientation", onOrient);

const wsUrl = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
// The amber bar's wording: plain words; ?debug=1 adds the rule id. Words only: G0 decides on the server what is held.
const barLayout: BarLayout = "words";

const app = new App({
  doc: document,
  search: location.search,
  wsUrl,
  fetch: (...a) => fetch(...a),
  synth: browserSynth(),
  alarm: browserAlarm(),
  openSocket: (u) => new WebSocket(u) as never,
  openCamera,
  eachFrame,
  loadHands: () => Hands.load(`${import.meta.env.BASE_URL}mp`),
  flowWorker: () => new FlowWorker() as never,
  jpeg: (b, w, h) => jpeg1024(b, w, h),
  bitmap: (v) => createImageBitmap(v),
  heading: () => heading,
  now: () => performance.now(),
  raf: (fn) => void requestAnimationFrame(fn),
  g1,
  disclaimers,
  household,
  pushMove,
  appConsent: consentLine(appConsentFile, lang),
  origin: location.origin,
  copy: (text) => navigator.clipboard.writeText(text),
  recorder: () => new Recorder(),
  listener: () => new Listener(),
  navigate: (search) => location.assign(`${location.pathname}${search}`),
  replaceSearch: (search) => history.replaceState(null, "", `${location.pathname}${search}`),
  firstRunSeen: () => {
    try {
      return localStorage.getItem(FIRST_RUN_KEY) === "1";
    } catch {
      return false;
    }
  },
  clearSeen: () => {
    try {
      return localStorage.getItem(CLEAR_KEY) === "1";
    } catch {
      return false;
    }
  },
  markClear: () => {
    try {
      localStorage.setItem(CLEAR_KEY, "1");
    } catch {
      // A private window: the hint shows again next time.
    }
  },
  markFirstRun: () => {
    try {
      localStorage.setItem(FIRST_RUN_KEY, "1");
    } catch {
      // A private window: the screen shows again next time.
    }
  },
  lang,
  barLayout,
  dev: import.meta.env.DEV,
  numbers,
  stopLeads,
  vibrate: "vibrate" in navigator ? (ms) => void navigator.vibrate(ms) : undefined,
  install: browserInstall(window),
  followHomeLang,
  setLang: (v) =>
    pickLang(langs, v, {
      search: location.search,
      replaceSearch: (s) => history.replaceState(null, "", `${location.pathname}${s}${location.hash}`),
      reload: () => location.reload(),
    }),
  golden: async () => (await fetch(`${API}/replay/golden`, { method: "GET" })).ok,
  upload: xhrUpload,
  sharedPdf: takeSharedPdf,
  objectUrl: { make: (b) => URL.createObjectURL(b), drop: (u) => URL.revokeObjectURL(u) },
  hash: location.hash,
  path: location.pathname,
  // A door's code leaves the address once read: the app's own page, without the code, the invite or the /try path.
  clearDoor: () => history.replaceState(null, "", `/${withoutDoor(location.search)}`),
  pushHash: (h) => history.pushState(null, "", h ? `#${h}` : `${location.pathname}${location.search}`),
});
void app.boot();
void followHomeLang();
// Back from a job's sheet, or a #job/… link followed inside the app.
window.addEventListener("popstate", () => app.route(location.hash));
window.addEventListener("hashchange", () => app.route(location.hash));
// Back in front after a while: a new build on the server reloads the app, unless something is under way.
const running = bundleOf([...document.scripts].map((x) => x.src).join(" "));
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible" || !app.quiet()) return;
  void newBuild(fetch, running).then((fresh) => fresh && app.quiet() && location.reload());
});
// For the on-device timing runs (docs/build-log): read with `fixitMetrics()` over remote devtools.
(globalThis as { fixitMetrics?: () => unknown }).fixitMetrics = () => app.metrics.snapshot(app.gate.sent, app.gate.held);

if ("serviceWorker" in navigator) void navigator.serviceWorker.register(`${import.meta.env.BASE_URL}sw.js`).then(subscribePush);

/** The PDF the service worker held from a share (?share=pdf): taken once, and the address loses ?share so a reload
 * doesn't ask again. */
async function takeSharedPdf(): Promise<File | null> {
  const p = new URLSearchParams(location.search);
  p.delete("share");
  history.replaceState(null, "", `${location.pathname}${p.size ? `?${p}` : ""}${location.hash}`);
  try {
    const c = await caches.open("fixit-share");
    const key = `${import.meta.env.BASE_URL}shared.pdf`;
    const r = await c.match(key);
    if (!r) return null;
    await c.delete(key);
    const name = decodeURIComponent(r.headers.get("x-name") ?? "manual.pdf");
    return new File([await r.blob()], name, { type: "application/pdf" });
  } catch {
    return null;
  }
}

/** POST with the upload's progress, which fetch can't report; the answer comes back as a Response. */
function xhrUpload(url: string, body: Blob, headers: Record<string, string>, sent: (f: number) => void): Promise<Response> {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open("POST", url);
    for (const [k, v] of Object.entries(headers)) x.setRequestHeader(k, v);
    x.upload.onprogress = (e) => e.lengthComputable && sent(e.loaded / e.total);
    x.onload = () => resolve(new Response(x.responseText, { status: x.status, headers: { "content-type": x.getResponseHeader("content-type") ?? "" } }));
    x.onerror = () => reject(new Error("upload failed"));
    x.onabort = () => reject(new Error("upload aborted"));
    x.send(body);
  });
}

async function subscribePush(reg: ServiceWorkerRegistration): Promise<void> {
  if (!("PushManager" in window) || Notification.permission === "denied") return;
  document.addEventListener(
    "click",
    async () => {
      if ((await Notification.requestPermission()) !== "granted") return;
      const r = await fetch(`${API}/push/vapid-public-key`, authed(undefined, household.get()));
      if (!r.ok) return;
      const { key } = (await r.json()) as { key: string };
      const raw = Uint8Array.from(atob(key.replace(/-/g, "+").replace(/_/g, "/")), (c) => c.charCodeAt(0));
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: raw });
      // With no home yet there is nobody to remind: signing in saves it then (pushMove).
      const token = household.get();
      if (token) await savePush(sub, token);
    },
    { once: true },
  );
}

/** Saves the subscription under a person's token: reminders then follow that person's alerts. */
async function savePush(sub: PushSubscription, token: string): Promise<void> {
  await fetch(`${API}/push/subscriptions`, authed({ method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(sub.toJSON()) }, token));
}

/** A new token on this phone: the subscription leaves the old one (that home, or that person, stops reaching this
 * phone) and is saved under the new one. Nothing when the browser has none. */
async function pushMove(from: string | null, to: string): Promise<void> {
  if (!("serviceWorker" in navigator) || !("PushManager" in window) || Notification.permission !== "granted") return;
  const sub = await (await navigator.serviceWorker.ready).pushManager.getSubscription();
  if (!sub) return;
  if (from && from !== to) {
    await fetch(`${API}/push/subscriptions`, authed({ method: "DELETE", headers: { "content-type": "application/json" }, body: JSON.stringify({ endpoint: sub.endpoint }) }, from)).catch(() => undefined);
  }
  await savePush(sub, to);
}

/** The home's language, once the server says it: a phone set to another language still reads the home's, unless this
 * device picked one in Settings or the address names one. A change reloads once; after that it is remembered. */
async function followHomeLang(): Promise<void> {
  const token = household.get();
  if (!token) return;
  const r = await fetch(`${API}/household`, authed(undefined, token)).catch(() => null);
  if (!r?.ok) return;
  const h = (await r.json().catch(() => null)) as { lang?: unknown } | null;
  if (adoptHomeLang(location.search, langs, navigator.language, h?.lang, lang)) location.reload();
}

/** The query without ?invite= or ?start, which must not stay in the address once used. */
function withoutDoor(search: string): string {
  const q = new URLSearchParams(search);
  q.delete("invite");
  q.delete("deleted");
  q.delete("start");
  const out = q.toString();
  return out ? `?${out}` : "";
}

// Adding an appliance (contracts 1.6.0, api.openapi.yaml): after the plate read is settled, or with a typed model
// code, the phone POSTs /appliances and polls GET /appliances/{id} until the card is checked, pages_only or failed.
// The stage words are the phone's own; a notice is the server's fixed, code-written text, never model text.
// Since 1.15: after a lookup miss the manual's PDF can be sent from the phone (POST /appliances/{id}/manual), and the
// ingest reports its progress, the manual, the quotes checked and the jobs ready so far, all written by code.

import type { PlateRead } from "../../contracts/ts/src/ws.ts";
import { langOf, tr, trf } from "./words.ts";

export type CardStatus = "checked" | "draft" | "pages_only" | "failed" | "pending";
export type Stage = "plate" | "lookup" | "fetch" | "ingest" | "done";

export type NoticeReason =
  | "no_manual"
  | "site_blocked"
  | "scanned_unreadable"
  | "too_large"
  | "not_a_manual"
  | "wrong_language"
  | "timeout"
  | "cap_reached"
  | "build_failed"
  | "maker_terms"
  | "not_mine";

export interface IngestNotice {
  kind: "lookup_failed" | "ingest_failed" | "plate_unreadable";
  maker_page?: string | null;
  text: { en?: string; nl?: string };
  reason?: NoticeReason;
}

export type RefusedReason = "gas" | "mains" | "needs_professional" | "other";

export interface ReadyJob {
  id: string;
  title: { en?: string; nl?: string };
  steps: number;
  page: number;
  duration_min: number | null;
  kind: "upkeep" | "fix";
  startable: boolean;
}

export interface Ingest {
  stage: Stage;
  progress?: number;
  elapsed_s?: number;
  manual?: { pages: number; lang: string; user_lang?: boolean; title?: string | null; thumb_url?: string | null };
  eta_s?: number | null;
  quotes?: { checked: number; passed: number };
  jobs_ready?: ReadyJob[];
  jobs_refused?: { id: string; reason: RefusedReason }[];
  notice?: IngestNotice | null;
}

export interface Appliance {
  id: string;
  alias: string;
  kind: string;
  brand: string;
  model: string;
  plate_confirmed: boolean;
  card_status: CardStatus;
  ingest?: Ingest;
}

export const POLL_MS = 2000;
export const FINAL: readonly CardStatus[] = ["checked", "pages_only", "failed"];

export const STAGE_TEXT: Record<Stage, string> = {
  plate: "Reading the model label…",
  lookup: "Finding the manual…",
  fetch: "Downloading the manual…",
  ingest: "Reading the manual…",
  done: "Nearly ready…",
};

/** The wait as a short list, each stage named plainly; the one under way is said with STAGE_TEXT's words. */
export const STAGE_STEPS: Record<Exclude<Stage, "done">, string> = {
  plate: "Read the model label",
  lookup: "Find the manual",
  fetch: "Download it",
  ingest: "Read it and check each quote",
};

/** Each stage of the wait, done, under way or still to come. The label's stage shows only when a photo was sent. */
export function stageRows(stage: Stage, fromPhoto: boolean): { stage: Exclude<Stage, "done">; state: "done" | "now" | "next" }[] {
  const order: Exclude<Stage, "done">[] = fromPhoto || stage === "plate" ? ["plate", "lookup", "fetch", "ingest"] : ["lookup", "fetch", "ingest"];
  const at = stage === "done" ? order.length : order.indexOf(stage);
  return order.map((s, i) => ({ stage: s, state: i < at ? "done" : i === at ? "now" : "next" }));
}

export function statusText(a: Appliance, lang = "en"): string {
  const t = (en: string) => tr(lang, en);
  const tf = (en: string, v: Record<string, string | number>) => trf(lang, en, v);
  if (a.card_status === "checked") return tf("Ready: {name}.", { name: a.alias });
  if (a.card_status === "pages_only") return tf("Ready: {name}. I'll guide from the manual's pages.", { name: a.alias });
  if (a.card_status === "failed") return t("I couldn't set it up.");
  return tr(lang, STAGE_TEXT[a.ingest?.stage ?? "plate"]);
}

/** A plate read with nothing left to ask: every field settled. */
export function settled(p: PlateRead): boolean {
  return p.fields.every((f) => f.uncertain.length === 0);
}

export function noticeText(n: IngestNotice | null | undefined, lang: string): string | null {
  if (!n) return null;
  const l = lang.slice(0, 2).toLowerCase();
  return (l === "nl" ? n.text.nl : undefined) ?? n.text.en ?? null;
}

/** Only an https link is ever made clickable. */
export function httpsUrl(u: string | null | undefined): string | null {
  if (!u) return null;
  try {
    return new URL(u).protocol === "https:" ? u : null;
  } catch {
    return null;
  }
}

export interface AddInput {
  alias?: string;
  photoId?: string | null;
  model?: string;
  pdfUrl?: string;
  brand?: string;
}

/** The ApplianceIn body: a plate photo id or a typed model (one is required), an optional name and manual link. */
export function applianceBody(i: AddInput): Record<string, string> {
  const b: Record<string, string> = {};
  const alias = i.alias?.trim();
  if (alias) b.alias = alias.slice(0, 40);
  if (i.photoId) b.plate_photo_id = i.photoId;
  const model = i.model?.trim();
  if (model) b.model = model;
  const brand = i.brand?.trim();
  if (brand) b.brand = brand.slice(0, 40);
  const pdf = i.pdfUrl?.trim();
  if (pdf) b.pdf_url = pdf;
  return b;
}

export function postError(status: number, error: string | undefined, lang = "en"): string {
  const t = (en: string) => tr(lang, en);
  if (status === 409 || error === "alias_taken") return t("That name is already used. Pick another.");
  if (error === "brand_needed") return t("Which brand is it? Type the brand from the model label, then tap Add.");
  if (status === 422) return t("That didn't work. Check the model code or the link.");
  if (status === 401) return t("Open your invite link on this phone to add appliances.");
  return t("Couldn't add it just now. Try again.");
}

/** POST /appliances/{id}/manual's limit (x-max-bytes), checked on the phone before anything is sent. */
export const MAX_PDF_BYTES = 52_428_800;

/** The phone's own words for a file it won't send, and for the upload's answers; they name the screen's controls. */
export const PDF_TEXT = {
  too_large: "That file is over 50 MB, too big for me to read. Paste a link to the manual under Manual link instead.",
  not_a_pdf: "That file isn't a PDF manual. Tap Add the PDF and pick the manual, or paste a link to it under Manual link.",
  view_only: "You can see this home but not change it, so you can't add a manual.",
  gone: "That appliance is no longer in this home. Close this and add it again.",
  failed: "Couldn't send the PDF. Check the connection and try again.",
} as const;

/** Why a picked file can't be sent, from its size and first bytes; null when it may go. */
export function pdfProblem(size: number, head: Uint8Array): keyof typeof PDF_TEXT | null {
  if (size > MAX_PDF_BYTES) return "too_large";
  const magic = [0x25, 0x50, 0x44, 0x46]; // %PDF
  return magic.every((b, i) => head[i] === b) ? null : "not_a_pdf";
}

/** A file's first bytes, through FileReader: every browser the app supports has it, and so does the test DOM. */
export function firstBytes(b: Blob, n: number): Promise<Uint8Array> {
  return new Promise((ok, fail) => {
    const r = new FileReader();
    r.onload = () => ok(new Uint8Array(r.result as ArrayBuffer));
    r.onerror = () => fail(r.error);
    r.readAsArrayBuffer(b.slice(0, n));
  });
}

export function uploadError(status: number, error: string | undefined, lang = "en"): string {
  if (status === 413 || error === "too_large") return tr(lang, PDF_TEXT.too_large);
  if (status === 415 || error === "not_a_pdf") return tr(lang, PDF_TEXT.not_a_pdf);
  if (status === 403) return tr(lang, PDF_TEXT.view_only);
  if (status === 404) return tr(lang, PDF_TEXT.gone);
  return tr(lang, PDF_TEXT.failed);
}

/** Whether the notice asks for the manual: then the screen offers the Manual link field and Add the PDF. */
export function wantsManual(n: IngestNotice | null | undefined): boolean {
  if (!n) return false;
  // A maker whose terms forbid reading its manuals: no upload either, until it agrees.
  if (n.reason === "maker_terms") return false;
  if (n.kind === "lookup_failed") return true;
  const fixable: readonly (NoticeReason | undefined)[] = ["site_blocked", "too_large", "not_a_manual", "scanned_unreadable", "wrong_language"];
  return n.kind === "ingest_failed" && fixable.includes(n.reason);
}

export const LANG_NAMES: Record<string, string> = { en: "English", nl: "Dutch", de: "German", fr: "French", it: "Italian", es: "Spanish" };
const LANG_NAMES_NL: Record<string, string> = { en: "het Engels", nl: "het Nederlands", de: "het Duits", fr: "het Frans", it: "het Italiaans", es: "het Spaans" };

/** "About 2 min left" from the server's estimate; nothing under ten seconds or without one. */
export function etaText(s: number | null | undefined, lang = "en"): string | null {
  if (s == null || s < 10) return null;
  return s < 90 ? tr(lang, "Less than a minute left") : trf(lang, "About {n} min left", { n: Math.round(s / 60) });
}

/** The fetched manual in one line: its pages, and its language when that isn't the household's. */
export function manualLine(m: Ingest["manual"], lang = "en"): string | null {
  if (!m) return null;
  const nl = langOf(lang) === "nl";
  const tf = (en: string, v: Record<string, string | number>) => trf(lang, en, v);
  const pages = nl ? `${m.pages} ${m.pages === 1 ? "pagina" : "pagina's"}` : `${m.pages} ${m.pages === 1 ? "page" : "pages"}`;
  if (m.user_lang !== false) return tf("Manual found: {pages}.", { pages });
  const name = (nl ? LANG_NAMES_NL : LANG_NAMES)[m.lang];
  return name ? tf("Manual found: {pages}, in {language}.", { pages, language: name }) : tf("Manual found: {pages}, not in your language.", { pages });
}

export function quotesLine(q: Ingest["quotes"], lang = "en"): string | null {
  if (!q || q.checked === 0) return null;
  return trf(lang, "Quotes checked against the pages: {passed} of {checked} found.", { passed: q.passed, checked: q.checked });
}

/** A refused job has no title (it may be the very text that was held), only why. */
export const REFUSED_TEXT: Record<RefusedReason, string> = {
  gas: "it works on the gas supply",
  mains: "it means opening the mains wiring",
  needs_professional: "it's a job for a professional",
  other: "the safety rules don't allow it",
};

export function refusedLines(r: Ingest["jobs_refused"], lang = "en"): string[] {
  const counts = new Map<RefusedReason, number>();
  for (const j of r ?? []) counts.set(j.reason, (counts.get(j.reason) ?? 0) + 1);
  const nl = langOf(lang) === "nl";
  return [...counts].map(([reason, n]) => {
    const jobs = nl ? (n === 1 ? "1 klus" : `${n} klussen`) : n === 1 ? "1 job" : `${n} jobs`;
    return trf(lang, "{jobs} left out: {why}.", { jobs, why: tr(lang, REFUSED_TEXT[reason]) });
  });
}

/** A ready job's facts line: steps, the page it starts on, and how long, when the card says. */
export function readyFacts(j: ReadyJob, lang = "en"): string {
  const nl = langOf(lang) === "nl";
  const tf = (en: string, v: Record<string, string | number>) => trf(lang, en, v);
  const steps = nl ? `${j.steps} ${j.steps === 1 ? "stap" : "stappen"}` : `${j.steps} ${j.steps === 1 ? "step" : "steps"}`;
  const parts = [steps, tf("page {n}", { n: j.page })];
  if (j.duration_min) parts.push(tf("about {n} min", { n: j.duration_min }));
  return parts.join(" · ");
}

export function titleIn(t: { en?: string; nl?: string }, lang: string): string {
  const l = lang.slice(0, 2).toLowerCase();
  return (l === "nl" ? t.nl : t.en) ?? t.en ?? t.nl ?? "";
}

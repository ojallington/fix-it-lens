// Your appliances (#devices) and one appliance's page (#devices/<id>): its jobs and when each is due, the manual and
// where it came from, how its card was checked, and a report on the card. A maker's manual is never shown from our
// server: a citation opens the maker's own PDF at its page. A home's own upload is shown in the app, to that home only.
// Every line here is the phone's own wording over fixed fields; card text arrives through textContent only.

import { httpsUrl, LANG_NAMES, noticeText, REFUSED_TEXT, wantsManual, type Appliance, type RefusedReason } from "./appliance.ts";
import { chevron, kindGlyph, pageBack } from "./home.ts";
import { KEEP_RUNNING, KIND_TEXT, SOMETHING_WRONG } from "./names.ts";
import { shortDate, whenText, type DueItem, type Unscheduled } from "./schedule.ts";
import { langOf, tr, trf } from "./words.ts";

/** The drawing shorthands for tr and trf, in the form the Dutch scan in words.test.ts reads. */
function words(lang: string) {
  return {
    t: (en: string) => tr(lang, en),
    tf: (en: string, v: Record<string, string | number>) => trf(lang, en, v),
  };
}

/** Where a card's manual came from, as the phone shows it. */
export interface ManualRef {
  source: "maker_site" | "household_upload";
  /** The maker's own https URL (maker_site only); null for an upload or a URL the phone won't open. */
  url: string | null;
  title: string | null;
  lang: string;
  pages: number;
}

export interface CardStep {
  id: string;
  text: string;
  /** null only on a step of our own (1.26 origin fixit), which no page of the manual says. */
  page: number | null;
}

export interface CardJob {
  id: string;
  title: string;
  kind: "upkeep" | "fix";
  /** The manual gives this job an interval. Without one (1.20) it has no schedule until a person in the home sets one. */
  interval: boolean;
  steps: CardStep[];
}

export interface CardRefused {
  /** Optional on the card since contracts 1.27; "" when absent. */
  id: string;
  title: string;
  reason: RefusedReason;
  page: number;
}

type CheckStatus = "pass" | "fail" | "unchecked";

/** What the phone keeps of GET /appliances/{id}/card. */
export interface CardView {
  manual: ManualRef;
  jobs: CardJob[];
  refused: CardRefused[];
  /** Procedures the manual covers that the build couldn't make into steps (refused reason not_built, contracts 1.27):
   * the manual's own heading, in its language, and its page. */
  notBuilt: { heading: string; page: number }[];
  checks: { citation: CheckStatus; rules: CheckStatus } | null;
}

/** GET /appliances/{id}/card/record (contracts 1.25). */
export interface CardRecord {
  card_sha256: string;
  version: number;
  built_at: string;
  shared: boolean;
  private_reason: string | null;
  reviewed: { by_person: boolean; at: string | null };
  homes: number;
  quotes: { checked: number; passed: number };
  reports_open: { total: number; safety: number };
  /** Fault codes read from a drawing in the manual that no person has confirmed yet (contracts 1.26); absent before. */
  unconfirmed_codes?: number;
  /** Restore steps ingest code added that no person has accepted yet (contracts 1.28); absent before. */
  unaccepted_steps?: number;
}

export type ReportKind = "wrong_step" | "wrong_page" | "not_mine" | "unsafe" | "other";

export interface ReportDraft {
  kind: ReportKind | null;
  /** "<job>/<step>", or "" for the card as a whole. */
  step: string;
  page: string;
  note: string;
  busy: boolean;
  error: string | null;
}

/** One row of GET …/meaning: what the code means, from the checked card, with its page and the manual's own words. */
export interface FaultMatch {
  code: string | null;
  jobId: string | null;
  title: string;
  page: number;
  quote: string;
  quoteEn: string | null;
  meaning: string | null;
  cause: string | null;
  remedy: string | null;
  /** Who the manual says does it: a technician's or installer's remedy has no job to start. */
  who: "user" | "technician" | "installer" | null;
  /** The code was read from a drawing in the manual and no person has confirmed it yet (contracts 1.26). */
  unconfirmed: boolean;
}

export interface FaultAnswer {
  code: string;
  matches: FaultMatch[];
  candidates: string[];
  /** The server's own fixed line when nothing matched (in the household's language), or null. */
  notice: string | null;
  tablePage: number | null;
  landlord: boolean;
}

/** "Something's wrong": the code typed, and what the card says about it. */
export interface FaultState {
  code: string;
  busy: boolean;
  answer: FaultAnswer | null;
  error: string | null;
}

export interface DevicesState {
  /** null: the list of appliances; else that appliance's page. */
  applianceId: string | null;
  /** The check record; null until fetched, "none" when the server has none for this card. */
  record: CardRecord | "none" | null;
  report: ReportDraft | null;
  /** What the last report did, said once on the page. */
  done: "sent" | "unlinked" | null;
  fault: FaultState;
  /** The list was opened from "Something's wrong": it asks which appliance. */
  wrong: boolean;
  /** Bring the code box into view once drawn. */
  scroll: "fault" | null;
  /** The list's Add button was tapped: the two ways to add one, in place of the list. */
  adding: boolean;
  /** Remove this appliance: asked, then sent. */
  remove: "ask" | "busy" | null;
  removeError: string | null;
}

export function devicesInitial(applianceId: string | null): DevicesState {
  return { applianceId, record: null, report: null, done: null, fault: { code: "", busy: false, answer: null, error: null }, wrong: false, scroll: null, adding: false, remove: null, removeError: null };
}

export function reportInitial(step = ""): ReportDraft {
  return { kind: null, step, page: "", note: "", busy: false, error: null };
}

// ---- the address ---------------------------------------------------------------------------------------------------

export function devicesHash(applianceId: string | null): string {
  return applianceId ? `devices/${encodeURIComponent(applianceId)}` : "devices";
}

/** #devices, or #devices/<id>; null for any other address. */
export function parseDevicesHash(hash: string): { applianceId: string | null } | null {
  const m = /^#?devices(?:\/([^/]+))?$/.exec(hash);
  if (!m) return null;
  if (!m[1]) return { applianceId: null };
  try {
    return { applianceId: decodeURIComponent(m[1]) };
  } catch {
    return null;
  }
}

// ---- the card ------------------------------------------------------------------------------------------------------

function str(v: unknown): string | null {
  return typeof v === "string" && v !== "" ? v : null;
}

function inLang(t: unknown, lang: string): string {
  if (!t || typeof t !== "object") return "";
  const o = t as Record<string, unknown>;
  return str(o[lang.slice(0, 2).toLowerCase()]) ?? str(o.en) ?? str(Object.values(o)[0]) ?? "";
}

function status(v: unknown): CheckStatus {
  return v === "pass" || v === "fail" ? v : "unchecked";
}

/** The parts of a card the phone shows; null for anything that isn't one. */
export function cardView(raw: unknown, lang: string): CardView | null {
  if (!raw || typeof raw !== "object") return null;
  const c = raw as Record<string, unknown>;
  const m = c.manual as Record<string, unknown> | undefined;
  if (!m || typeof m !== "object") return null;
  // A card from before 1.23 has no source: all of those were fetched from the maker's site.
  const source = m.source === "household_upload" ? "household_upload" : "maker_site";
  const pages = typeof m.page_count === "number" && m.page_count > 0 ? m.page_count : 0;
  const manual: ManualRef = {
    source,
    url: source === "maker_site" ? httpsUrl(str(m.source_url)) : null,
    title: str(m.title),
    lang: str(m.lang) ?? "",
    pages,
  };
  const jobs: CardJob[] = (Array.isArray(c.jobs) ? c.jobs : []).flatMap((j: Record<string, unknown>) =>
    typeof j?.id === "string"
      ? [
          {
            id: j.id,
            title: keepCodes(inLang(j.title, lang)) || j.id,
            kind: j.kind === "fix" ? ("fix" as const) : ("upkeep" as const),
            interval: !!j.interval && typeof j.interval === "object",
            steps: (Array.isArray(j.steps) ? j.steps : []).flatMap((s: Record<string, unknown>) =>
              typeof s?.id === "string" && (typeof s.page === "number" || s.origin === "fixit")
                ? [{ id: s.id, text: inLang(s.text, lang), page: typeof s.page === "number" ? s.page : null }]
                : [],
            ),
          },
        ]
      : [],
  );
  const reasons: RefusedReason[] = ["gas", "mains", "needs_professional", "other"];
  const rows = (Array.isArray(c.refused) ? c.refused : []).filter((r: Record<string, unknown>) => r && typeof r.page === "number");
  const refused: CardRefused[] = rows
    .filter((r: Record<string, unknown>) => r.reason !== "not_built")
    .map((r: Record<string, unknown>) => ({
      id: str(r.id) ?? "",
      title: inLang(r.title, lang),
      reason: reasons.includes(r.reason as RefusedReason) ? (r.reason as RefusedReason) : "other",
      page: r.page as number,
    }));
  const notBuilt = rows.flatMap((r: Record<string, unknown>) =>
    r.reason === "not_built" && typeof r.heading === "string" && r.heading.trim() ? [{ heading: r.heading.trim(), page: r.page as number }] : [],
  );
  const ch = c.checks as Record<string, Record<string, unknown> | undefined> | null | undefined;
  const checks = ch && typeof ch === "object" ? { citation: status(ch.citation?.status), rules: status(ch.rules?.status) } : null;
  return { manual, jobs, refused, notBuilt, checks };
}

/** A code in a title, such as E:32-00, never breaks at its hyphen: a narrow screen would show "E:32-" and "00" on two
 * lines. The hyphen becomes U+2011, which looks the same. */
export function keepCodes(s: string): string {
  return s.replace(/\b([A-Z]{1,3}:?\d{1,3})-(\d{1,3})\b/g, "$1\u2011$2");
}

/** GET …/meaning's answer as the phone shows it; anything malformed is left out. */
export function faultAnswer(raw: unknown, lang: string): FaultAnswer | null {
  if (!raw || typeof raw !== "object") return null;
  const r = raw as Record<string, unknown>;
  const text = (v: unknown): string | null => (v && typeof v === "object" ? inLang(v, lang) || null : typeof v === "string" ? v : null);
  const matches: FaultMatch[] = (Array.isArray(r.matches) ? r.matches : []).flatMap((m: Record<string, unknown>) =>
    m && typeof m.page === "number" && typeof m.quote === "string"
      ? [
          {
            code: str(m.code),
            jobId: str(m.job_id),
            title: keepCodes(text(m.title) ?? ""),
            page: m.page,
            quote: m.quote,
            quoteEn: str(m.quote_en),
            meaning: text(m.meaning),
            cause: text(m.cause),
            remedy: text(m.remedy),
            who: m.remedy_kind === "user" || m.remedy_kind === "technician" || m.remedy_kind === "installer" ? m.remedy_kind : null,
            unconfirmed: m.unconfirmed === true,
          },
        ]
      : [],
  );
  return {
    code: str(r.code) ?? "",
    matches,
    candidates: (Array.isArray(r.candidates) ? r.candidates : []).filter((c): c is string => typeof c === "string").slice(0, 5),
    notice: str(r.notice),
    tablePage: typeof r.table_page === "number" && r.table_page > 0 ? r.table_page : null,
    landlord: r.landlord === true,
  };
}

export const FAULT_TEXT = {
  label: "Code on the display",
  hint: "Type it as the display shows it, for example E24. I look it up in this appliance's manual only.",
  look: "Look it up",
  empty: "Type the code first.",
  long: "That's longer than any code. Type it as the display shows it.",
  noCard: "This appliance has no checked card yet, so I can't look codes up.",
  failed: "Couldn't look that up. Try again.",
  pick: "Did you mean",
  table: "The manual's fault table",
  landlord: "You rent: the rules say this one is for your landlord. Ask them before you start.",
  technician: "The manual leaves this to a technician. Don't open the appliance; call one.",
  installer: "The manual leaves this to the installer. Don't open the appliance; call them.",
  start: "Start the fix",
  unconfirmed: "Read from a drawing in the manual. No person has confirmed this code yet: check it against the display.",
} as const;

/** A procedure the build couldn't make into steps: the manual still covers it, and its page opens. */
export const NOT_BUILT_TEXT = {
  head: "Also in the manual",
} as const;

export function faultError(status: number, lang = "en"): string {
  return tr(lang, status === 404 ? FAULT_TEXT.noCard : status === 422 ? FAULT_TEXT.long : FAULT_TEXT.failed);
}

/** The maker's own PDF at a page ("…/manual.pdf#page=43"): what every citation of a maker_site card opens. */
export function makerPage(m: ManualRef | null | undefined, page: number): string | null {
  if (!m || m.source !== "maker_site" || !m.url) return null;
  return `${m.url.split("#")[0]}#page=${Math.max(1, Math.floor(page))}`;
}

function host(u: string): string {
  try {
    return new URL(u).hostname.replace(/^www\./, "");
  } catch {
    return "";
  }
}

function langName(lang: string): string | null {
  return LANG_NAMES[lang.slice(0, 2).toLowerCase()] ?? null;
}

/** "48 pages · in German". */
export function manualFacts(m: ManualRef, lang = "en"): string {
  const { t, tf } = words(lang);
  const parts: string[] = [];
  if (m.pages) parts.push(m.pages === 1 ? t("1 page") : tf("{n} pages", { n: m.pages }));
  const name = langName(m.lang);
  if (name) parts.push(tf("in {language}", { language: tr(lang, name) }));
  return parts.join(" · ");
}

/** Where the manual came from, in words. */
export function sourceText(m: ManualRef, lang = "en"): string {
  const { t, tf } = words(lang);
  if (m.source === "household_upload") return t("Your own copy of the PDF. Only your home can open it.");
  const h = m.url ? host(m.url) : "";
  return h ? tf("The maker's own PDF, on {host}. It opens on the maker's site.", { host: h }) : t("The maker's own PDF, from the maker's site.");
}

// ---- how this card was checked (K3) ---------------------------------------------------------------------------------

export const PRIVATE_TEXT: Record<string, string> = {
  uploaded: "Kept to your home: it was built from your own copy of the manual.",
  no_model_list: "Kept to your home: the manual doesn't list the models it covers, so it can't be matched to the same model in another home.",
  model_not_in_list: "Kept to your home: your model isn't in the manual's own list of models.",
  maker_terms: "Kept to your home: the maker's terms don't allow sharing it.",
  version_conflict: "Kept to your home: this copy of the manual differs from the one other homes use, so it waits for review.",
  codes_unconfirmed: "Kept to your home until a person confirms the fault codes read from the manual's drawings.",
  steps_unaccepted: "Kept to your home until a person accepts the steps we added to it.",
};

const CHECK_TEXT = {
  citation: {
    pass: "Code checked every quote against the text on its page.",
    fail: "Some quotes didn't match the text on their page, and were taken out.",
    unchecked: "The quotes have not been checked against the pages yet.",
  },
  rules: {
    pass: "Every step passed the safety rules.",
    fail: "The safety rules refused part of this card.",
    unchecked: "The safety rules have not checked this card yet.",
  },
} as const;

/** The safety line at the top of a card with an open unsafe report; null without one. */
export function safetyAlert(r: CardRecord | "none" | null, lang = "en"): string | null {
  if (!r || r === "none" || r.reports_open.safety === 0) return null;
  const { t, tf } = words(lang);
  const n = r.reports_open.safety;
  return n === 1
    ? t("A home has reported something unsafe on this card. It is waiting for a person to check it against the manual.")
    : tf("{n} homes have reported something unsafe on this card. It is waiting for a person to check it against the manual.", { n });
}

/** "1 job left out: …" for each reason the card refused a job, in the order the card names them. */
function leftOutLines(refused: CardRefused[], lang: string): string[] {
  const { t, tf } = words(lang);
  const counts = new Map<RefusedReason, number>();
  for (const j of refused) counts.set(j.reason, (counts.get(j.reason) ?? 0) + 1);
  return [...counts].map(([reason, n]) =>
    n === 1 ? tf("1 job left out: {reason}.", { reason: t(REFUSED_TEXT[reason]) }) : tf("{n} jobs left out: {reason}.", { n, reason: t(REFUSED_TEXT[reason]) }),
  );
}

/** The panel's lines, from the card and its check record only, in the brief's order. */
export function recordLines(v: CardView, r: CardRecord | "none" | null, today: string, lang = "en"): string[] {
  const { t, tf } = words(lang);
  const m = v.manual;
  const rec = r && r !== "none" ? r : null;
  const out: string[] = [];
  const f = manualFacts(m, lang);
  const facts = f ? ` (${f})` : "";
  const source = sourceText(m, lang);
  out.push(m.title ? tf("Built from “{title}”{facts}. {source}", { title: m.title, facts, source }) : tf("Built from the manual{facts}. {source}", { facts, source }));
  if (rec) {
    const date = shortDate(rec.built_at.slice(0, 10), today, lang);
    out.push(rec.version > 1 ? tf("Built {date}, version {version}.", { date, version: rec.version }) : tf("Built {date}.", { date }));
  }
  if (rec && rec.quotes.checked > 0)
    out.push(
      tf("Code checked every quote against the text on its page: {passed} of {checked} matched, and the card keeps only those.", {
        passed: rec.quotes.passed,
        checked: rec.quotes.checked,
      }),
    );
  else if (v.checks) out.push(t(CHECK_TEXT.citation[v.checks.citation]));
  const drawn = rec?.unconfirmed_codes ?? 0;
  if (drawn === 1) out.push(t("1 fault code was read from a drawing in the manual, not its text; no person has confirmed it yet."));
  else if (drawn > 1) out.push(tf("{n} fault codes were read from a drawing in the manual, not its text; no person has confirmed them yet.", { n: drawn }));
  if (v.checks) out.push(t(CHECK_TEXT.rules[v.checks.rules]));
  const added = rec?.unaccepted_steps ?? 0;
  if (added === 1) out.push(t("1 step is ours, not from the manual: added by our code to put the appliance back in use, and no person has accepted it yet."));
  else if (added > 1)
    out.push(tf("{n} steps are ours, not from the manual: added by our code to put the appliance back in use, and no person has accepted them yet.", { n: added }));
  const left = leftOutLines(v.refused, lang);
  const n = v.notBuilt.length;
  if (n === 1) left.push(t("1 procedure in the manual couldn't be made into steps; this page points at its page."));
  else if (n > 1) left.push(tf("{n} procedures in the manual couldn't be made into steps; this page points at their pages.", { n }));
  out.push(...(left.length ? left : [t("No jobs left out.")]));
  if (rec) {
    out.push(
      rec.reviewed.by_person && rec.reviewed.at
        ? tf("A person read this card against the manual, {date}.", { date: shortDate(rec.reviewed.at.slice(0, 10), today, lang) })
        : t("No person has reviewed this card yet; the checks above are code."),
    );
    out.push(
      rec.shared
        ? rec.homes > 1
          ? tf("Shared with every home that has this model: {n} homes use it.", { n: rec.homes })
          : t("Shared with every home that has this model. Only your home uses it so far.")
        : t(PRIVATE_TEXT[rec.private_reason ?? ""] ?? "Kept to your home."),
    );
    const total = rec.reports_open.total;
    const safety = rec.reports_open.safety;
    out.push(
      total === 0
        ? t("No open reports.")
        : total === 1
          ? safety
            ? tf("1 open report, {safety} about safety. A report never changes the card by itself.", { safety })
            : t("1 open report. A report never changes the card by itself.")
          : safety
            ? tf("{n} open reports, {safety} about safety. A report never changes the card by itself.", { n: total, safety })
            : tf("{n} open reports. A report never changes the card by itself.", { n: total }),
    );
  } else if (r === "none") out.push(t("The rest of the check record isn't available for this card."));
  return out;
}

// ---- report a problem (K4) ------------------------------------------------------------------------------------------

export const REPORT_KIND_TEXT: Record<ReportKind, string> = {
  wrong_step: "A step is wrong",
  wrong_page: "A page number is wrong",
  not_mine: "This isn't my appliance",
  unsafe: "Something is unsafe",
  other: "Something else",
};

export const REPORT_TEXT = {
  sent: "Sent. Thank you. A person checks it against the manual; the card doesn't change until then.",
  unlinked: "Done. Your home no longer uses that card; other homes keep theirs.",
  notMine: "Your home stops using this card, and its jobs leave your list. Other homes keep it. Then you can add your own manual.",
  pickKind: "Pick what's wrong.",
  pickStep: "Pick the step that's wrong.",
  page: "Type the page the manual should show.",
  note: "Say what's wrong in a few words.",
} as const;

/** The body for POST .../reports or .../not-mine, or what is missing. */
export function reportBody(d: ReportDraft, pages: number, lang = "en"): { body: Record<string, unknown> } | { error: string } {
  const { t, tf } = words(lang);
  if (!d.kind) return { error: t(REPORT_TEXT.pickKind) };
  const note = d.note.trim().slice(0, 500);
  if (d.kind === "not_mine") return { body: note ? { note } : {} };
  const [job, step] = d.step ? d.step.split("/") : [];
  if (d.kind === "wrong_step" && !step) return { error: t(REPORT_TEXT.pickStep) };
  const page = Number(d.page);
  const pageOk = Number.isInteger(page) && page >= 1 && (!pages || page <= pages);
  if (d.kind === "wrong_page" && !pageOk) return { error: pages ? tf("Type the page the manual should show, 1 to {pages}.", { pages }) : t(REPORT_TEXT.page) };
  if (d.kind === "other" && !note) return { error: t(REPORT_TEXT.note) };
  const body: Record<string, unknown> = { kind: d.kind };
  if (job) body.job_id = job;
  if (step) body.step_id = step;
  if (d.kind === "wrong_page") body.page = page;
  if (note) body.note = note;
  return { body };
}

export function reportError(status: number, error: string | undefined, lang = "en"): string {
  const { t } = words(lang);
  if (status === 429 || error === "too_many_reports") return t("Your home already has 20 reports waiting for review. Try again once some are answered.");
  if (status === 403) return t("This phone can only view this home, so it can't send that.");
  if (status === 404) return t("That appliance is no longer in this home.");
  if (status === 409 || error === "no_card") return t("There's no card for this appliance yet, so nothing to report.");
  if (status === 422) return t("That can't be sent. Pick the step again, or check the page number.");
  return t("Couldn't send that. Check the connection and try again.");
}

// ---- the screens ----------------------------------------------------------------------------------------------------

function el<K extends keyof HTMLElementTagNameMap>(doc: Document, tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = doc.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function button(doc: Document, act: string, text: string, cls?: string, data: Record<string, string> = {}): HTMLButtonElement {
  const b = el(doc, "button", cls, text);
  b.type = "button";
  b.dataset.act = act;
  Object.assign(b.dataset, data);
  return b;
}

function cap(t: string): string {
  return `${t.charAt(0).toUpperCase()}${t.slice(1)}`;
}

/** A translated line with {holes} filled by nodes (a citation, a manual's own heading), in the order the language
 * puts them. */
function fillNodes(doc: Document, line: string, nodes: Record<string, Node>): Node[] {
  return line.split(/(\{\w+\})/).flatMap((part) => {
    const k = /^\{(\w+)\}$/.exec(part)?.[1];
    if (k && k in nodes) return [nodes[k]!];
    return part ? [doc.createTextNode(part)] : [];
  });
}

/** A citation that opens the manual at its page: the maker's PDF in a new tab, the home's own copy in the app, or
 * plain text when neither is known. */
export function citeNode(doc: Document, applianceId: string, m: ManualRef | null | undefined, page: number, text: string, lang = "en"): HTMLElement {
  const url = makerPage(m, page);
  if (url) {
    const a = el(doc, "a", "cite-link", text);
    a.href = url;
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    a.setAttribute("aria-label", trf(lang, "{text}: opens the maker's PDF at page {page}", { text, page }));
    return a;
  }
  if (m?.source === "household_upload") return button(doc, "view-page", text, "cite-link", { appliance: applianceId, page: String(page) });
  return el(doc, "span", "cite-text", text);
}

/** The card's upkeep jobs whose manual gives no interval and that nobody has scheduled yet, in card order. */
export function unscheduledJobs(cards: Record<string, CardView>, due: DueItem[], appliances: Appliance[]): Unscheduled[] {
  return appliances.flatMap((a) =>
    a.card_status !== "checked"
      ? []
      : (cards[a.id]?.jobs ?? [])
          .filter((j) => j.kind === "upkeep" && !j.interval && !due.some((d) => d.applianceId === a.id && d.jobId === j.id))
          .map((j) => ({ applianceId: a.id, jobId: j.id, title: j.title, alias: a.alias })),
  );
}

/** A step of our own in a list: no page to name. */
const OUR_STEP_SHORT = "our step";

export const NO_SCHEDULE_TEXT = "No schedule yet · the manual gives no interval";

/** One appliance's state in a few words, for the list. */
export function stateLine(a: Appliance, due: DueItem[], today: string, lang = "en"): string {
  const { t, tf } = words(lang);
  if (a.card_status === "checked") {
    const mine = due.filter((d) => d.applianceId === a.id && d.dueOn !== null).sort((x, y) => (x.dueOn! < y.dueOn! ? -1 : 1));
    const next = mine[0];
    return next ? tf("Next: {job}, {when}", { job: next.title, when: whenText(next, today, lang) }) : t("Ready");
  }
  if (a.card_status === "pages_only") return t("Guided from the manual's pages");
  if (a.card_status === "failed")
    return a.ingest?.notice?.reason === "maker_terms" ? t("The maker doesn't allow it yet") : wantsManual(a.ingest?.notice) ? t("Needs its manual") : t("Couldn't set it up");
  return t("Reading the manual…");
}

/** The list row's state: the next job and when, or the card's state; amber when it wants something. */
function rowState(a: Appliance, due: DueItem[], today: string, lang: string): { line: string; chip: string; late: boolean; needs: boolean } {
  const { tf } = words(lang);
  if (a.card_status === "checked") {
    const next = due.filter((d) => d.applianceId === a.id && d.dueOn !== null).sort((x, y) => (x.dueOn! < y.dueOn! ? -1 : 1))[0];
    if (next) {
      const late = next.dueOn! <= today;
      return { line: tf("Next: {job}", { job: next.title }), chip: whenText(next, today, lang), late, needs: late };
    }
  }
  return { line: stateLine(a, due, today, lang), chip: "", late: false, needs: a.card_status === "failed" };
}

/** #devices: every appliance in the home under one Add button; the Add button opens the two ways to add one. */
export function devicesNodes(doc: Document, appliances: Appliance[], due: DueItem[], today: string, wrong = false, lang = "en", adding = false): Node[] {
  const { t } = words(lang);
  const out: Node[] = [];
  if (adding) {
    out.push(pageBack(doc, "devices-add-close", t("Your appliances")));
    const add = el(doc, "section", "devices-add");
    add.append(el(doc, "h1", undefined, t("Add an appliance")));
    add.append(el(doc, "p", "sub", t("Point the camera at the label with the model number (the rating plate), or type the model code from it.")));
    const row = el(doc, "div", "row");
    row.append(button(doc, "start", t("Use the camera"), "ok"), button(doc, "devices-type", t("Type the model")));
    add.append(row);
    out.push(add);
    return out;
  }
  out.push(el(doc, "h1", undefined, wrong ? tr(lang, SOMETHING_WRONG) : t("Your appliances")));
  if (wrong && appliances.length) out.push(el(doc, "p", "sub", t("Which appliance? Its page looks the code up in its own manual.")));
  if (!wrong) out.push(button(doc, "devices-add-open", t("Add an appliance"), "ok add-one"));
  if (!appliances.length) {
    // Where the list will be: three faint rows, each the shape of an appliance it could hold.
    const empty = el(doc, "div", "devices-empty");
    const ghosts = el(doc, "div", "ghosts");
    ghosts.setAttribute("aria-hidden", "true");
    for (const k of ["washer", "boiler", "dishwasher"]) {
      const g = el(doc, "span", "ghost");
      g.append(kindGlyph(doc, k), el(doc, "span", "ghost-line"));
      ghosts.append(g);
    }
    empty.append(ghosts, el(doc, "p", "sub", t("No appliances yet. Add the first one above.")));
    out.push(empty);
  }
  const listBox = el(doc, "div", "list");
  for (const a of appliances) {
    const open = button(doc, "devices-open", "", "item-open", { appliance: a.id });
    const txt = el(doc, "span", "item-text");
    // Three lines: the name, the model quiet in mono, then the state; when it is due sits on the right.
    const st = rowState(a, due, today, lang);
    const model = `${a.brand} ${a.model}`.trim();
    txt.append(el(doc, "strong", undefined, cap(a.alias)));
    if (model) txt.append(el(doc, "span", "item-model", model));
    txt.append(el(doc, "span", `sub item-state${st.late ? " late" : ""}`, st.line));
    open.append(kindGlyph(doc, a.kind), txt);
    if (st.chip) open.append(el(doc, "span", `when${st.late ? " late" : ""}`, st.chip));
    open.append(chevron(doc));
    const row = el(doc, "div", `item${st.needs ? " needs" : ""}`);
    row.append(open);
    listBox.append(row);
  }
  out.push(listBox);
  return out;
}

export interface AppliancePage {
  a: Appliance;
  due: DueItem[];
  /** The card's upkeep jobs with no interval that nobody has scheduled. */
  unscheduled: Unscheduled[];
  card: CardView | null;
  st: DevicesState;
  today: string;
  lang: string;
  /** Whether this person may take the appliance out of the home (not a view-only one). */
  canRemove?: boolean;
}

/** The redraw key: everything shown, but not what is typed into the report (a redraw would lose the caret). */
export function applianceKey(p: AppliancePage): string {
  const r = p.st.report;
  const f = p.st.fault;
  return JSON.stringify([p.a, p.due, p.unscheduled, p.card, p.st.record, p.st.done, r && [r.kind, r.step, r.busy, r.error], [f.busy, f.answer, f.error], p.st.remove, p.st.removeError, p.canRemove, p.today, p.lang]);
}

/** #devices/<id>: the appliance, its jobs, its manual, how its card was checked, and Report a problem. */
export function applianceNodes(doc: Document, p: AppliancePage): Node[] {
  const { a, card, st, today, lang } = p;
  const { t, tf } = words(lang);
  const due = p.due.filter((d) => d.applianceId === a.id);
  const out: Node[] = [];
  out.push(pageBack(doc, "devices-back", t("Your appliances")));
  const alert = safetyAlert(st.record, lang);
  if (alert) {
    const al = el(doc, "p", "safety-alert", alert);
    al.setAttribute("role", "alert");
    out.push(al);
  }
  const head = el(doc, "header", "ap-head");
  const ht = el(doc, "div");
  ht.append(el(doc, "h1", undefined, cap(a.alias)), el(doc, "p", "sub", `${a.brand} ${a.model}`.trim()));
  head.append(kindGlyph(doc, a.kind), ht);
  out.push(head);

  if (st.done) {
    const d = el(doc, "p", "done-line", t(REPORT_TEXT[st.done]));
    d.setAttribute("role", "status");
    out.push(d);
  }

  // Not set up: why, and the way on when there is one.
  if (a.card_status !== "checked" && a.card_status !== "pages_only") {
    const why = a.card_status === "failed" ? (noticeText(a.ingest?.notice, lang) ?? t("I couldn't set it up.")) : t("Reading the manual…");
    out.push(el(doc, "p", a.card_status === "failed" ? "notice-line" : "sub", why));
    if (a.card_status === "failed" && wantsManual(a.ingest?.notice)) out.push(button(doc, "devices-manual", t("Add the manual"), "ok", { appliance: a.id }));
  }

  // Keep it running: the home's schedule for upkeep (and the jobs still waiting for one). Then Something's wrong: a
  // code looked up in the card, and the card's fixes.
  const unscheduled = p.unscheduled.filter((u) => u.applianceId === a.id);
  if (due.length || unscheduled.length) {
    const sec = el(doc, "section", "ap-jobs");
    sec.append(el(doc, "h2", "way", tr(lang, KEEP_RUNNING)));
    for (const d of due) {
      const open = button(doc, "open-sheet", "", "item-open", { appliance: a.id, job: d.jobId });
      const txt = el(doc, "span", "item-text");
      txt.append(kindTag(doc, "upkeep", lang), el(doc, "strong", undefined, d.title), el(doc, "span", "sub", d.unanswered ? t("When did you last do it? Tap to say") : cap(whenText(d, today, lang))));
      open.append(txt, chevron(doc));
      const row = el(doc, "div", "item");
      row.append(open);
      sec.append(row);
    }
    for (const u of unscheduled) {
      const open = button(doc, "open-sheet", "", "item-open", { appliance: a.id, job: u.jobId });
      const txt = el(doc, "span", "item-text");
      txt.append(kindTag(doc, "upkeep", lang), el(doc, "strong", undefined, u.title), el(doc, "span", "sub", t(NO_SCHEDULE_TEXT)));
      open.append(txt, chevron(doc));
      const row = el(doc, "div", "item");
      row.append(open);
      sec.append(row);
    }
    out.push(sec);
  }
  const fixes = (card?.jobs ?? []).filter((j) => j.kind === "fix" && !due.some((d) => d.jobId === j.id));
  if (card) {
    const sec = el(doc, "section", "ap-jobs ap-wrong");
    sec.id = "something-wrong";
    sec.append(el(doc, "h2", "way", tr(lang, SOMETHING_WRONG)));
    sec.append(faultNodes(doc, a.id, card, st.fault, lang));
    if (fixes.length) sec.append(el(doc, "h3", undefined, t("Fixes in the manual")));
    for (const j of fixes) {
      const go = button(doc, "start-job", "", "item-open", { appliance: a.id, job: j.id });
      const txt = el(doc, "span", "item-text");
      const first = j.steps.find((s) => s.page !== null)?.page;
      const steps = j.steps.length === 1 ? t("1 step") : tf("{n} steps", { n: j.steps.length });
      txt.append(kindTag(doc, "fix", lang), el(doc, "strong", undefined, j.title), el(doc, "span", "sub", [steps, first ? tf("starts on p. {page}", { page: first }) : ""].filter(Boolean).join(" · ")));
      go.append(txt, chevron(doc));
      const row = el(doc, "div", "item");
      row.append(go);
      sec.append(row);
    }
    out.push(sec);
  }
  if (card?.refused.length) {
    const sec = el(doc, "section", "ap-refused");
    sec.append(el(doc, "h2", undefined, t("Left to a professional")));
    const ul = el(doc, "ul", "refused");
    for (const r of card.refused) {
      const li = el(doc, "li");
      const why = el(doc, "span", "sub", `${cap(t(REFUSED_TEXT[r.reason]))} · `);
      why.append(citeNode(doc, a.id, card.manual, r.page, `p. ${r.page}`, lang));
      li.append(el(doc, "strong", undefined, r.title || t("A job in the manual")), why);
      ul.append(li);
    }
    sec.append(ul);
    out.push(sec);
  }

  if (card?.notBuilt.length) {
    const sec = el(doc, "section", "ap-not-built");
    sec.append(el(doc, "h2", undefined, t(NOT_BUILT_TEXT.head)));
    const ul = el(doc, "ul", "refused");
    for (const r of card.notBuilt) {
      const li = el(doc, "li");
      const h = el(doc, "strong", undefined, r.heading);
      if (card.manual.lang) h.lang = card.manual.lang;
      h.translate = false;
      const page = citeNode(doc, a.id, card.manual, r.page, `p. ${r.page}`, lang);
      li.append(...fillNodes(doc, t("The manual also covers {heading} on {page}; we couldn't make it into steps."), { heading: h, page }));
      ul.append(li);
    }
    sec.append(ul);
    out.push(sec);
  }

  if (card) {
    const m = card.manual;
    const sec = el(doc, "section", "ap-manual");
    sec.append(el(doc, "h2", undefined, t("Manual")));
    if (m.title) sec.append(el(doc, "p", "manual-title", m.title));
    const facts = manualFacts(m, lang);
    if (facts) sec.append(el(doc, "p", "sub", facts));
    sec.append(el(doc, "p", "sub", sourceText(m, lang)));
    const open = citeNode(doc, a.id, m, 1, t("Open the manual"), lang);
    open.classList.remove("cite-link");
    open.classList.add("button");
    if (open.tagName !== "SPAN") sec.append(open);
    out.push(sec);

    const chk = el(doc, "section", "ap-checked");
    chk.append(el(doc, "h2", undefined, t("How this card was checked")));
    const ul = el(doc, "ul", "checked-lines");
    for (const line of recordLines(card, st.record, today, lang)) ul.append(el(doc, "li", undefined, line));
    chk.append(ul);
    out.push(chk);

    out.push(st.report ? reportForm(doc, card, st.report, lang) : button(doc, "report-open", t("Report a problem"), undefined, { appliance: a.id }));
  }
  // Last on the page: take it out of the home (a dead add, or one the home no longer has). Asked once first.
  if (p.canRemove) {
    const rm = el(doc, "section", "ap-remove");
    if (st.remove) {
      rm.append(el(doc, "p", undefined, tf("Remove {name} from this home? Its jobs and reminders go; what you did with it stays in the history.", { name: a.alias })));
      const row = el(doc, "div", "row");
      const go = button(doc, "remove-go", t(st.remove === "busy" ? "Removing…" : "Remove"), "danger", { appliance: a.id });
      go.disabled = st.remove === "busy";
      row.append(go, button(doc, "remove-cancel", t("Keep it"), "text"));
      rm.append(row);
    } else rm.append(button(doc, "remove-ask", t("Remove this appliance"), "text", { appliance: a.id }));
    if (st.removeError) rm.append(el(doc, "p", "notice-line", st.removeError));
    out.push(rm);
  }
  return out;
}


function kindTag(doc: Document, kind: "upkeep" | "fix", lang: string): HTMLElement {
  return el(doc, "span", `tag kind ${kind}`, tr(lang, KIND_TEXT[kind]));
}

/** The code box and what the card says about the code. Every line of an answer is the card's (cited, checked) or
 * the server's fixed notice; the phone adds only its own fixed words around them. */
function faultNodes(doc: Document, applianceId: string, card: CardView, f: FaultState, lang: string): HTMLElement {
  const { t, tf } = words(lang);
  const box = el(doc, "div", "fault");
  // A form, so Enter on the phone's keyboard taps Look it up; the app stops the submit itself.
  const form = el(doc, "form", "fault-form");
  const label = el(doc, "label", "fault-label");
  const input = el(doc, "input");
  input.type = "text";
  input.dataset.fault = "code";
  input.value = f.code;
  input.maxLength = 20;
  input.autocapitalize = "characters";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.enterKeyHint = "search";
  label.append(el(doc, "span", undefined, t(FAULT_TEXT.label)), input);
  const go = button(doc, "fault-look", f.busy ? t("Looking…") : t(FAULT_TEXT.look), "ok", { appliance: applianceId });
  go.type = "submit";
  go.disabled = f.busy;
  const row = el(doc, "div", "row");
  row.append(label, go);
  form.append(row, el(doc, "p", "sub", t(FAULT_TEXT.hint)));
  box.append(form);
  if (f.error) {
    // The app sets FAULT_TEXT's lines in English; anything else it sets is already worded and passes through.
    const p = el(doc, "p", "error", t(f.error));
    p.setAttribute("role", "alert");
    box.append(p);
  }
  const ans = f.answer;
  if (!ans) return box;
  const out = el(doc, "div", "fault-answer");
  out.setAttribute("role", "status");
  for (const m of ans.matches) {
    const it = el(doc, "article", "fault-match");
    const head = el(doc, "p", "fault-head");
    if (m.code) head.append(el(doc, "span", "tag code", m.code));
    head.append(el(doc, "strong", undefined, m.meaning ?? m.title));
    it.append(head);
    if (m.unconfirmed) it.append(el(doc, "p", "fault-unconfirmed", t(FAULT_TEXT.unconfirmed)));
    const q = el(doc, "blockquote", "sched-quote");
    const v = el(doc, "p", undefined, `“${m.quote}”`);
    if (card.manual.lang) v.lang = card.manual.lang;
    v.translate = false;
    q.append(v);
    if (m.quoteEn && m.quoteEn !== m.quote) {
      const english = el(doc, "p", "translation");
      english.lang = "en";
      // The tag is the phone's own word, in the screen's language; only the quote after it is English.
      const tag = el(doc, "span", "tag", t("In English"));
      tag.lang = langOf(lang);
      english.append(tag, doc.createTextNode(m.quoteEn));
      q.append(english);
    }
    it.append(q);
    const cite = el(doc, "p", "sub");
    cite.append(...fillNodes(doc, t("The manual, {page}"), { page: citeNode(doc, applianceId, card.manual, m.page, `p. ${m.page}`, lang) }));
    it.append(cite);
    const line = (label: string, text: string) => {
      const p = el(doc, "p");
      p.append(el(doc, "strong", undefined, `${label}: `), doc.createTextNode(text));
      return p;
    };
    if (m.cause) it.append(line(t("Cause"), m.cause));
    if (m.remedy) it.append(line(t("What to do"), m.remedy));
    if (m.who === "technician" || m.who === "installer") it.append(el(doc, "p", "notice-line", t(FAULT_TEXT[m.who])));
    else if (m.jobId && card.jobs.some((j) => j.id === m.jobId)) {
      if (ans.landlord) it.append(el(doc, "p", "notice-line", t(FAULT_TEXT.landlord)));
      const start = button(doc, "start-job", tf("Start the fix: {job}", { job: card.jobs.find((j) => j.id === m.jobId)!.title }), ans.landlord ? undefined : "ok", { appliance: applianceId, job: m.jobId });
      it.append(start);
    }
    out.append(it);
  }
  if (!ans.matches.length && ans.notice) out.append(el(doc, "p", "notice-line", ans.notice));
  if (ans.candidates.length) {
    const pick = el(doc, "div", "fault-pick");
    pick.append(el(doc, "span", "sub", `${t(FAULT_TEXT.pick)}:`));
    for (const c of ans.candidates) pick.append(button(doc, "fault-pick", c, "code-pick", { appliance: applianceId, code: c }));
    out.append(pick);
  }
  if (!ans.matches.length && ans.tablePage) {
    const p = el(doc, "p", "sub");
    p.append(doc.createTextNode(`${t(FAULT_TEXT.table)}: `), citeNode(doc, applianceId, card.manual, ans.tablePage, `p. ${ans.tablePage}`, lang));
    out.append(p);
  }
  box.append(out);
  return box;
}

function reportForm(doc: Document, card: CardView, d: ReportDraft, lang: string): HTMLElement {
  const { t } = words(lang);
  const box = el(doc, "fieldset", "report");
  box.disabled = d.busy;
  box.append(el(doc, "legend", undefined, t("Report a problem")));
  box.append(el(doc, "p", "sub", t("A report never changes the card. A person checks it against the manual first.")));
  const kinds = el(doc, "div", "report-kinds");
  for (const k of Object.keys(REPORT_KIND_TEXT) as ReportKind[]) {
    const row = el(doc, "label", "report-kind");
    const r = el(doc, "input");
    r.type = "radio";
    r.name = "report-kind";
    r.value = k;
    r.dataset.report = "kind";
    r.checked = d.kind === k;
    row.append(r, doc.createTextNode(` ${t(REPORT_KIND_TEXT[k])}`));
    kinds.append(row);
  }
  box.append(kinds);

  if (d.kind === "not_mine") box.append(el(doc, "p", "sub", t(REPORT_TEXT.notMine)));
  if (d.kind === "wrong_step" || d.kind === "wrong_page" || d.kind === "unsafe") {
    const lab = el(doc, "label", "report-field", d.kind === "wrong_step" ? t("Which step") : t("Which step (if it's one step)"));
    const sel = el(doc, "select");
    sel.dataset.report = "step";
    const none = el(doc, "option", undefined, d.kind === "wrong_step" ? t("Pick a step") : t("The card as a whole"));
    none.value = "";
    sel.append(none);
    for (const j of card.jobs) {
      const g = el(doc, "optgroup");
      g.label = j.title;
      j.steps.forEach((s, i) => {
        const text = s.text.length > 60 ? `${s.text.slice(0, 57)}…` : s.text;
        const o = el(doc, "option", undefined, `${i + 1}. ${text} (${s.page === null ? t(OUR_STEP_SHORT) : `p. ${s.page}`})`);
        o.value = `${j.id}/${s.id}`;
        o.selected = d.step === o.value;
        g.append(o);
      });
      sel.append(g);
    }
    lab.append(sel);
    box.append(lab);
  }
  if (d.kind === "wrong_page") {
    const lab = el(doc, "label", "report-field", t("The right page in the manual"));
    const i = el(doc, "input");
    i.type = "number";
    i.inputMode = "numeric";
    i.min = "1";
    if (card.manual.pages) i.max = String(card.manual.pages);
    i.value = d.page;
    i.dataset.report = "page";
    lab.append(i);
    box.append(lab);
  }
  if (d.kind) {
    const lab = el(doc, "label", "report-field", d.kind === "other" ? t("What's wrong") : t("A note (optional)"));
    const area = el(doc, "textarea");
    area.maxLength = 500;
    area.rows = 3;
    area.value = d.note;
    area.dataset.report = "note";
    lab.append(area);
    box.append(lab, el(doc, "p", "sub", t("Your note stays with your home and the reviewer. It is never sent to an AI model.")));
  }
  if (d.error) {
    const p = el(doc, "p", "error", t(d.error));
    p.setAttribute("role", "alert");
    box.append(p);
  }
  const row = el(doc, "div", "row");
  row.append(
    button(doc, "report-send", d.kind === "not_mine" ? t("Stop using this card") : t("Send the report"), d.kind === "not_mine" ? "danger" : "ok"),
    button(doc, "report-cancel", t("Cancel"), "dismiss"),
  );
  box.append(row);
  return box;
}

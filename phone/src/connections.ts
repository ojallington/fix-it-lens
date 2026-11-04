// Connections (#connections): the person's own calendar address, and, for an admin, the keys this home gave Home
// Assistant and AI agents. Each connection has its own key, shown once when it is made and never again; removing it
// stops it at once. No connection can run a repair, clear a stop or change a card: the server takes none of them on
// the session, and a write key may change the schedule only. The texts tables and the refusals stay English, as the
// app stores them; each is put in the person's language where it is drawn.

import { pageBack, rowMore } from "./home.ts";
import { button, el, errorLine } from "./people.ts";
import { shortDate } from "./schedule.ts";
import { langOf, tr, trf } from "./words.ts";

export type ConnKind = "ha" | "agent";
export type Scope = "read" | "write";

/** One of the home's keys, as GET /household/tokens lists it: never its secret. */
export interface Connection {
  id: string;
  by_kind: ConnKind | "feed";
  scope: Scope;
  label: string;
  created_at: string;
  last_used_at: string | null;
}

function isConnection(x: unknown): x is Connection {
  const t = x as Connection;
  return (
    !!t &&
    typeof t.id === "string" &&
    (t.by_kind === "ha" || t.by_kind === "agent" || t.by_kind === "feed") &&
    (t.scope === "read" || t.scope === "write") &&
    typeof t.label === "string" &&
    typeof t.created_at === "string" &&
    (t.last_used_at === null || typeof t.last_used_at === "string")
  );
}

export function parseConnections(x: unknown): Connection[] | null {
  return Array.isArray(x) && x.every(isConnection) ? x : null;
}

/** POST /household/tokens' answer: the key, shown once. */
export function parseMade(x: unknown): { id: string; token: string } | null {
  const m = x as { id?: unknown; token?: unknown };
  return m && typeof m.id === "string" && typeof m.token === "string" && m.token.length > 0 ? { id: m.id, token: m.token } : null;
}

export const CONN_TEXT = {
  failed: "Couldn't do that. Try again.",
  notAdmin: "Only an admin can do that.",
  label: "Give it a name of 1 to 60 characters.",
  loading: "Getting your connections…",
  copied: "Copied.",
} as const;

export function connError(status: number): string {
  return status === 403 ? CONN_TEXT.notAdmin : CONN_TEXT.failed;
}

export const KIND_TEXT: Record<ConnKind | "feed", string> = { ha: "Home Assistant", agent: "AI agent", feed: "Calendar" };
export const SCOPE_TEXT: Record<Scope, string> = { read: "Can only look", write: "Can look and change the schedule" };

export interface ConnDraft {
  kind: ConnKind;
  scope: Scope;
  /** Whether the person picked the access themselves; until then it follows the kind. */
  scopeSet: boolean;
  label: string;
  /** Whether the person typed a name; until then it follows the kind. */
  labelSet: boolean;
  busy: boolean;
  error: string | null;
}

/** Home Assistant's Done button and services need a key that can write; an agent starts with one that only looks.
 * The name it starts with is the kind's, in the person's language; once made it is the home's data. */
export function connDraft(kind: ConnKind = "ha", lang = "en"): ConnDraft {
  return { kind, scope: kind === "ha" ? "write" : "read", scopeSet: false, label: tr(lang, KIND_TEXT[kind]), labelSet: false, busy: false, error: null };
}

export function connBody(d: ConnDraft): { body: { by_kind: ConnKind; scope: Scope; label: string } } | { error: string } {
  const label = d.label.trim();
  if (label.length < 1 || label.length > 60) return { error: CONN_TEXT.label };
  return { body: { by_kind: d.kind, scope: d.scope, label } };
}

/** Where a new key goes: the app's own address for Home Assistant, its /mcp endpoint for an agent. */
export function connAddress(kind: ConnKind, origin: string): string {
  return kind === "agent" ? `${origin.replace(/\/$/, "")}/mcp` : origin;
}

export interface ConnState {
  /** The home's keys; null until fetched, and for anyone but an admin (the server answers 403). */
  tokens: Connection[] | null;
  loaded: boolean;
  /** The caller's own .ics address (GET /household/feeds); null until fetched or on a server without it. */
  feed: string | null;
  feedShown: boolean;
  draft: ConnDraft | null;
  /** A key just made: kept in memory only, until Done or the screen closes. */
  made: { kind: ConnKind; label: string; token: string; address: string } | null;
  /** What was copied last, for its button's label. */
  copied: "feed" | "key" | "address" | null;
  /** The key asked about before it is removed. */
  confirm: string | null;
  busy: boolean;
  error: string | null;
}

export function connInitial(): ConnState {
  return { tokens: null, loaded: false, feed: null, feedShown: false, draft: null, made: null, copied: null, confirm: null, busy: false, error: null };
}

/** #connections */
export const CONN_HASH = "#connections";

function radio(doc: Document, field: string, value: string, label: string, on: boolean, help?: string): HTMLLabelElement {
  const l = el(doc, "label", "check");
  const i = el(doc, "input");
  i.type = "radio";
  i.name = `conn-${field}`;
  i.value = value;
  i.dataset.cf = field;
  i.checked = on;
  const t = el(doc, "span", undefined, label);
  if (help) t.append(el(doc, "span", "sub help", help));
  l.append(i, t);
  return l;
}

function copyRow(doc: Document, label: string, copy: string, value: string, act: string, copied: boolean, lang: string): HTMLElement {
  const box = el(doc, "div", "conn-secret");
  box.append(el(doc, "p", "sub", label), el(doc, "p", "invite-url", value));
  box.append(button(doc, act, copied ? tr(lang, CONN_TEXT.copied) : copy));
  return box;
}

function calendarNodes(doc: Document, st: ConnState, lang: string): HTMLElement {
  const t = (en: string) => tr(lang, en);
  const box = el(doc, "section", "conn-calendar");
  box.append(el(doc, "h2", undefined, t("Your calendar")));
  box.append(el(doc, "p", "sub", t("Subscribe to this address in your calendar app to see your upkeep jobs there. Anyone with it can read them, so keep it to yourself.")));
  if (st.feedShown && st.feed) box.append(copyRow(doc, t("Address"), t("Copy the address"), st.feed, "conn-feed-copy", st.copied === "feed", lang));
  else box.append(button(doc, "conn-feed-show", t("Show the address")));
  return box;
}

function madeNodes(doc: Document, st: ConnState, m: NonNullable<ConnState["made"]>, lang: string): HTMLElement {
  const t = (en: string) => tr(lang, en);
  const tf = (en: string, v: Record<string, string | number>) => trf(lang, en, v);
  const box = el(doc, "section", "conn-made");
  box.append(el(doc, "h2", undefined, tf("The key for {label}", { label: m.label })));
  box.append(
    el(
      doc,
      "p",
      "sub",
      m.kind === "ha"
        ? t("In Home Assistant, go to Settings, Devices & services, Add integration, Fix-It Lens, and paste these two.")
        : t("Put these in your agent's MCP settings. Keep the key in a file only you can read, not in a chat or a command line."),
    ),
  );
  box.append(copyRow(doc, t("Address"), t("Copy the address"), m.address, "conn-copy-address", st.copied === "address", lang));
  box.append(copyRow(doc, t("Key"), t("Copy the key"), m.token, "conn-copy-key", st.copied === "key", lang));
  const warn = el(doc, "p", "sub conn-once", t("This is the only time the key is shown. If it's lost, remove this connection and add a new one."));
  warn.setAttribute("role", "note");
  box.append(warn, button(doc, "conn-done", t("Done"), "ok"));
  return box;
}

function draftNodes(doc: Document, d: ConnDraft, lang: string): HTMLElement {
  const t = (en: string) => tr(lang, en);
  const box = el(doc, "section", "conn-new");
  box.append(el(doc, "h2", undefined, t("Add a connection")));
  const kind = el(doc, "fieldset");
  kind.append(el(doc, "legend", undefined, t("For")));
  kind.append(radio(doc, "kind", "ha", KIND_TEXT.ha, d.kind === "ha"), radio(doc, "kind", "agent", t("An AI agent (MCP)"), d.kind === "agent"));
  const scope = el(doc, "fieldset");
  scope.append(el(doc, "legend", undefined, t("It may")));
  scope.append(
    radio(doc, "scope", "read", t("Only look"), d.scope === "read", t("What is due, and when each job was last done.")),
    radio(doc, "scope", "write", t("Look and change the schedule"), d.scope === "write", t("Mark jobs done, snooze them and set their dates. Every change shows under its name and can be undone.")),
  );
  const lab = el(doc, "label", "field");
  lab.append(el(doc, "span", "sub", t("Its name in this home")));
  const i = el(doc, "input");
  i.dataset.cf = "label";
  i.value = d.label;
  i.maxLength = 60;
  i.enterKeyHint = "go";
  lab.append(i);
  box.append(kind, scope, lab);
  if (d.error) box.append(errorLine(doc, t(d.error)));
  const row = el(doc, "div", "row");
  const go = button(doc, "conn-make", t("Make the key"), "ok");
  go.disabled = d.busy;
  row.append(go, button(doc, "conn-cancel", t("Cancel"), "link"));
  box.append(row);
  return box;
}

function connRow(doc: Document, st: ConnState, c: Connection, today: string, lang: string): HTMLElement {
  const t = (en: string) => tr(lang, en);
  const tf = (en: string, v: Record<string, string | number>) => trf(lang, en, v);
  const li = el(doc, "li", "person conn");
  const who = el(doc, "div", "who");
  who.append(el(doc, "strong", undefined, c.label));
  const used = c.last_used_at ? tf("last used {date}", { date: shortDate(c.last_used_at.slice(0, 10), today, lang) }) : t("not used yet");
  const added = tf("added {date}", { date: shortDate(c.created_at.slice(0, 10), today, lang) });
  // The kind only when the name doesn't already say it ("Home Assistant" named Home Assistant).
  const kind = t(KIND_TEXT[c.by_kind]);
  const first = kind.toLowerCase() === c.label.trim().toLowerCase() ? [] : [kind];
  const line = [...first, t(SCOPE_TEXT[c.scope]).toLowerCase(), added, used].join(" · ");
  who.append(el(doc, "p", "sub", line.charAt(0).toUpperCase() + line.slice(1)));
  li.append(who);
  if (st.confirm === c.id) {
    li.append(el(doc, "p", "sub", tf("Remove {label}? It stops working at once.", { label: c.label })));
    const row = el(doc, "div", "row");
    const yes = button(doc, "conn-remove-yes", t("Remove"), "danger");
    yes.dataset.conn = c.id;
    yes.disabled = st.busy;
    row.append(yes, button(doc, "conn-remove-no", t("Keep"), "link"));
    li.append(row);
    return li;
  }
  const b = button(doc, "conn-remove", t("Remove"), "menu-item gone");
  b.dataset.conn = c.id;
  li.append(rowMore(doc, tf("More for {name}", { name: c.label }), [b]));
  return li;
}

/** The Connections screen. admin: the caller may see and make the home's keys. The refusals in st are the English of
 * CONN_TEXT, put in the language here. */
export function connNodes(doc: Document, st: ConnState, today: string, lang = "en"): Node[] {
  const t = (en: string) => tr(lang, en);
  const close = pageBack(doc, "conn-close", t("Household"));
  const out: Node[] = [close, el(doc, "h1", undefined, t("Connections"))];
  if (!st.loaded) {
    const p = el(doc, "p", "sub loading", t(CONN_TEXT.loading));
    p.setAttribute("role", "status");
    return [...out, p];
  }
  if (st.feed) out.push(calendarNodes(doc, st, lang));
  if (st.tokens) {
    const box = el(doc, "section", "conn-keys");
    box.append(el(doc, "h2", undefined, t("Home Assistant and AI agents")));
    box.append(el(doc, "p", "sub", t("Each gets its own key. None of them can run a repair, clear a stop or change what a manual says.")));
    if (st.made) box.append(madeNodes(doc, st, st.made, lang));
    // Calendar keys can't be made here and open nothing; they are listed so they can be removed.
    const keys = st.tokens;
    if (keys.length) {
      const ul = el(doc, "ul", "people conns");
      for (const k of keys) ul.append(connRow(doc, st, k, today, lang));
      box.append(ul);
    } else if (!st.made) box.append(el(doc, "p", "sub", t("Nothing is connected yet.")));
    if (st.error) box.append(errorLine(doc, t(st.error)));
    if (st.draft) box.append(draftNodes(doc, st.draft, lang));
    else if (!st.made) box.append(button(doc, "conn-new", t("Add a connection"), "big"));
    out.push(box);
  } else if (st.error) out.push(errorLine(doc, t(st.error)));
  if (!st.feed && !st.tokens && !st.error) out.push(el(doc, "p", "sub", t("Nothing to set up here on this home.")));
  return out;
}

/** What makes the screen differ: typing a name never redraws it. */
export function connKey(st: ConnState, lang = "en"): string {
  const d = st.draft;
  return JSON.stringify([langOf(lang), st.loaded, st.tokens, st.feed !== null, st.feedShown, d && [d.kind, d.scope, d.busy, d.error, d.labelSet ? null : d.label], st.made?.address ?? null, st.copied, st.confirm, st.busy, st.error]);
}

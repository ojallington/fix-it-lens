// People in a home and each person's own alerts. Roles as the smart-home apps have them: an admin does everything
// (people, invites, deleting the home), a member also changes the schedule, and a member who can only view reads.
// An invite is a link the admin shares (or mails: the server sends no mail); the person opens it, gives a name and
// joins with their own token. The screen at #people stays hidden until the server answers GET /household/people.
// The texts tables and the refusals stay English, as the app stores them; each is put in the person's language where
// it is drawn.

import { rowMore } from "./home.ts";
import { langOf, tr, trf } from "./words.ts";

export interface Person {
  id: string;
  name: string;
  role: "admin" | "member";
  view_only: boolean;
  /** The caller. */
  me: boolean;
}

export interface Invite {
  code: string;
  join_url: string;
  role: "admin" | "member";
  view_only: boolean;
  email: string | null;
  expires_at: string;
}

export interface Alerts {
  push: boolean;
  calendar: boolean;
  hub: boolean;
  items: "all" | "chosen";
  follows: string[];
  quiet: { from: string; to: string } | null;
}

/** What the caller may do: null while not known (no people list yet), which keeps the app as it was before people. */
export type Access = "admin" | "member" | "viewer";

/** The three roles the phone offers, as one choice: view-only is a member who reads. */
export type RoleChoice = "admin" | "member" | "viewer";

const HHMM = /^([01][0-9]|2[0-3]):[0-5][0-9]$/;

function isPerson(x: unknown): x is Person {
  const p = x as Person;
  return !!p && typeof p.id === "string" && typeof p.name === "string" && (p.role === "admin" || p.role === "member") && typeof p.view_only === "boolean" && typeof p.me === "boolean";
}

export function parsePeople(x: unknown): Person[] | null {
  return Array.isArray(x) && x.every(isPerson) ? x : null;
}

export function parsePerson(x: unknown): Person | null {
  return isPerson(x) ? x : null;
}

export function parseInvite(x: unknown): Invite | null {
  const i = x as Invite;
  return i && typeof i.code === "string" && typeof i.join_url === "string" && typeof i.expires_at === "string" && (i.role === "admin" || i.role === "member") ? { ...i, view_only: !!i.view_only, email: i.email ?? null } : null;
}

export function parseAlerts(x: unknown): Alerts | null {
  const a = x as Alerts;
  if (!a || typeof a.push !== "boolean" || typeof a.calendar !== "boolean" || typeof a.hub !== "boolean") return null;
  if (a.items !== "all" && a.items !== "chosen") return null;
  if (!Array.isArray(a.follows) || !a.follows.every((f) => typeof f === "string")) return null;
  const q = a.quiet;
  if (q !== null && !(q && HHMM.test(q.from) && HHMM.test(q.to))) return null;
  return { push: a.push, calendar: a.calendar, hub: a.hub, items: a.items, follows: [...a.follows], quiet: q ? { from: q.from, to: q.to } : null };
}

export function accessOf(people: Person[] | null): Access | null {
  const me = people?.find((p) => p.me);
  if (!me) return null;
  return me.role === "admin" ? "admin" : me.view_only ? "viewer" : "member";
}

export function roleOf(p: Person): RoleChoice {
  return p.role === "admin" ? "admin" : p.view_only ? "viewer" : "member";
}

/** The PATCH body that gives a person a role; view-only is never set on an admin. */
export function roleBody(r: RoleChoice): { role: "admin" | "member"; view_only: boolean } {
  return r === "admin" ? { role: "admin", view_only: false } : { role: "member", view_only: r === "viewer" };
}

export const ROLE_TEXT: Record<RoleChoice, string> = {
  admin: "Admin",
  member: "Member",
  viewer: "Can only view",
};

/** What each role may do, under the choice. */
export const ROLE_HELP: Record<RoleChoice, string> = {
  admin: "Everything, including people, invites and deleting the home.",
  member: "Sees everything, adds appliances, runs repairs and changes the schedule.",
  viewer: "Sees the home's appliances and jobs, and sets their own alerts. Changes nothing.",
};

export const PEOPLE_TEXT = {
  failed: "Couldn't save that. Try again.",
  notAdmin: "Only an admin can do that.",
  lastAdmin: "A home keeps at least one admin. Make someone else an admin first.",
  nameTaken: "Someone in this home already has that name. Pick another.",
  name: "Give a name of 1 to 40 characters.",
  email: "That email address doesn't look right.",
  quiet: "Quiet hours need a start and an end that differ.",
  follows: "Choose at least one appliance, or pick every appliance.",
  notInHome: "One of those appliances isn't in this home any more. Choose again.",
  loading: "Getting the people in this home…",
  saved: "Saved.",
  copied: "Link copied.",
  viewer: "You can see this home but not change it. Ask an admin if you need to.",
  viewerSession: "You can see this home but not run a repair here. Ask an admin to let you change things.",
} as const;

/** A refused write in the person's words, from its status and the server's error code. */
export function peopleError(status: number, error?: string): string {
  if (status === 403) return PEOPLE_TEXT.notAdmin;
  if (error === "last_admin") return PEOPLE_TEXT.lastAdmin;
  if (error === "name_taken") return PEOPLE_TEXT.nameTaken;
  if (status === 422 && error !== undefined && /follows|appliance/.test(error)) return PEOPLE_TEXT.notInHome;
  return PEOPLE_TEXT.failed;
}

export function nameError(name: string): string | null {
  const n = name.trim();
  return n.length < 1 || n.length > 40 ? PEOPLE_TEXT.name : null;
}

export interface InviteDraft {
  /** The inviter's own name, asked at the first invite while they still have the server's FIRST_NAME; null after. */
  myName: string | null;
  role: RoleChoice;
  email: string;
  days: number;
  busy: boolean;
  error: string | null;
  made: Invite | null;
  copied: boolean;
}

/** The name the server gives a new home's first person until they give their own. */
export const FIRST_NAME = "Admin";

/** A new invite. `me` is the inviter: a first person still called FIRST_NAME is asked for a name on the form. */
export function inviteDraft(me?: Person | null): InviteDraft {
  return { myName: me?.name === FIRST_NAME ? "" : null, role: "member", email: "", days: 7, busy: false, error: null, made: null, copied: false };
}

export function inviteBody(d: InviteDraft): { body: { role: "admin" | "member"; view_only: boolean; email: string | null; expires_in_days: number } } | { error: string } {
  const email = d.email.trim();
  if (email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return { error: PEOPLE_TEXT.email };
  const days = Math.min(14, Math.max(1, Math.round(d.days) || 7));
  return { body: { ...roleBody(d.role), email: email || null, expires_in_days: days } };
}

/** The last day an invite works: as the server gives it in English, as Dutch says a date in Dutch. */
export function inviteDay(expiresAt: string, lang = "en"): string {
  const iso = expiresAt.slice(0, 10);
  if (langOf(lang) === "en") return iso;
  return new Date(`${iso}T12:00:00Z`).toLocaleDateString("nl-NL", { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}

/** The share sheet's title for an invite link: no home name travels with it. */
export function inviteShareTitle(lang = "en"): string {
  return trf(lang, "Join {home} on Fix-It Lens", { home: tr(lang, "our home") });
}

/** A mail the person's own mail app sends: the server sends none. */
export function inviteMail(inv: Invite, home: string | null, lang = "en"): string {
  const tf = (en: string, v: Record<string, string | number>) => trf(lang, en, v);
  const where = home ?? tr(lang, "our home");
  const subject = tf("Join {home} on Fix-It Lens", { home: where });
  const body = [tf("Open this link on your phone to join {home} on Fix-It Lens:", { home: where }), inv.join_url, tf("It works once, until {date}.", { date: inviteDay(inv.expires_at, lang) })].join("\n\n");
  return `mailto:${encodeURIComponent(inv.email ?? "")}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

export function alertsBody(a: Alerts): { body: Alerts } | { error: string } {
  if (a.quiet && (!HHMM.test(a.quiet.from) || !HHMM.test(a.quiet.to) || a.quiet.from === a.quiet.to)) return { error: PEOPLE_TEXT.quiet };
  if (a.items === "chosen" && a.follows.length === 0) return { error: PEOPLE_TEXT.follows };
  return { body: { ...a, follows: a.items === "chosen" ? [...a.follows] : [] } };
}

export interface PeopleState {
  /** null until fetched. */
  people: Person[] | null;
  alerts: Alerts | null;
  /** The alerts as being edited, from the server's until saved. */
  draft: Alerts | null;
  invite: InviteDraft | null;
  rename: { id: string; name: string } | null;
  confirm: { id: string; kind: "remove" | "leave" } | null;
  busy: boolean;
  /** A refusal of a change to the people, shown under the list. */
  error: string | null;
  /** The alerts' own refusal and their Saved., shown by their Save. */
  alertsError: string | null;
  note: string | null;
}

export function peopleInitial(): PeopleState {
  return { people: null, alerts: null, draft: null, invite: null, rename: null, confirm: null, busy: false, error: null, alertsError: null, note: null };
}

/** #people */
export const PEOPLE_HASH = "#people";

/** #join/<code>: a people invite. The code is in the fragment, so no server log ever sees it. */
export function joinCode(hash: string): string | null {
  const m = /^#join\/([A-Za-z0-9_-]{4,128})$/.exec(hash);
  return m ? m[1]! : null;
}

/** A people invite: POST /household/join with the code and the name the others will see. */
export interface JoinState {
  kind: "join";
  code: string;
  name: string;
  busy: boolean;
  error: string | null;
  /** The phone already opens a home: joining moves it to this one. */
  switching: boolean;
}

export const JOIN_TEXT = {
  title: "Join a home",
  lead: "Someone invited you to their home on Fix-It Lens. Give the name the others will see.",
  bad: "This invite doesn't work any more: it was used, or it ran out. Ask for a new one.",
  failed: "Couldn't join just now. Check the connection and try again.",
  switching: "This phone already opens a home. Joining moves it to this one; the other home stays as it is for everyone in it.",
} as const;

export function joinError(status: number, error?: string): string {
  if (status === 409 || error === "name_taken") return PEOPLE_TEXT.nameTaken;
  if (status === 403) return JOIN_TEXT.bad;
  return JOIN_TEXT.failed;
}

// ---- drawing -------------------------------------------------------------------------------------------------------

export function el<K extends keyof HTMLElementTagNameMap>(doc: Document, tag: K, cls?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = doc.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

export function button(doc: Document, act: string, text: string, cls?: string): HTMLButtonElement {
  const b = el(doc, "button", cls, text);
  b.type = "button";
  b.dataset.act = act;
  return b;
}

export function check(doc: Document, field: string, label: string, on: boolean, value?: string, type: "checkbox" | "radio" = "checkbox", name?: string): HTMLLabelElement {
  const l = el(doc, "label", "check");
  const i = el(doc, "input");
  i.type = type;
  i.dataset.pf = field;
  if (name) i.name = name;
  if (value !== undefined) i.value = value;
  i.checked = on;
  l.append(i, el(doc, "span", undefined, label));
  return l;
}

function roleSelect(doc: Document, field: string, value: RoleChoice, lang: string, id?: string, label = "Role"): HTMLLabelElement {
  const l = el(doc, "label", "role-pick");
  l.append(el(doc, "span", "sub", tr(lang, label)));
  const s = el(doc, "select");
  s.dataset.pf = field;
  if (id) s.dataset.person = id;
  for (const r of ["member", "viewer", "admin"] as const) {
    const o = el(doc, "option", undefined, tr(lang, ROLE_TEXT[r]));
    o.value = r;
    o.selected = r === value;
    s.append(o);
  }
  l.append(s);
  return l;
}

export function errorLine(doc: Document, text: string): HTMLElement {
  const p = el(doc, "p", "error", text);
  p.setAttribute("role", "alert");
  return p;
}

function personRow(doc: Document, st: PeopleState, p: Person, admin: boolean, admins: number, lang: string): HTMLElement {
  const t = (en: string) => tr(lang, en);
  const tf = (en: string, v: Record<string, string | number>) => trf(lang, en, v);
  const li = el(doc, "li", "person");
  const head = el(doc, "div", "person-head");
  const who = el(doc, "strong", undefined, p.name);
  const role = t(ROLE_TEXT[roleOf(p)]);
  head.append(who, el(doc, "span", "tag", p.me ? tf("{role} · you", { role }) : role));
  li.append(head);
  if (st.rename?.id === p.id) {
    const row = el(doc, "div", "row rename");
    const lab = el(doc, "label", "field");
    lab.append(el(doc, "span", "sub", t("Name")));
    const i = el(doc, "input");
    i.dataset.pf = "rename";
    i.maxLength = 40;
    i.value = st.rename.name;
    i.autocomplete = "name";
    lab.append(i);
    row.append(lab, button(doc, "people-rename-save", t("Save"), "ok"), button(doc, "people-rename-cancel", t("Cancel"), "link"));
    li.append(row);
    return li;
  }
  if (st.confirm?.id === p.id) {
    const leave = st.confirm.kind === "leave";
    li.append(
      el(
        doc,
        "p",
        "sub",
        leave ? t("Leave this home? This phone stops opening it, and your jobs go back to nobody.") : tf("Remove {name}? Their phones stop opening this home, and their jobs go back to nobody.", { name: p.name }),
      ),
    );
    const row = el(doc, "div", "row");
    const yes = button(doc, "people-remove-yes", leave ? t("Leave") : t("Remove"), "danger");
    yes.dataset.person = p.id;
    row.append(yes, button(doc, "people-remove-no", t("Keep"), "link"));
    li.append(row);
    return li;
  }
  // Rename, the role and Remove fold behind the row's ⋯; Remove comes last, in red, and still asks first.
  const items: HTMLElement[] = [];
  if (p.me || admin) {
    const r = button(doc, "people-rename", t("Rename"), "menu-item");
    r.dataset.person = p.id;
    items.push(r);
  }
  // The last admin can neither step down nor leave: the server says so too, but the phone doesn't offer it.
  const lastAdmin = p.role === "admin" && admins <= 1;
  if (admin && !p.me) items.push(roleSelect(doc, "role", roleOf(p), lang, p.id));
  if (p.me && !lastAdmin) {
    const b = button(doc, "people-leave", t("Leave this home"), "menu-item gone");
    b.dataset.person = p.id;
    items.push(b);
  } else if (admin && !p.me) {
    const b = button(doc, "people-remove", t("Remove"), "menu-item gone");
    b.dataset.person = p.id;
    items.push(b);
  }
  if (items.length) li.append(rowMore(doc, tf("More for {name}", { name: p.name }), items));
  return li;
}

/** Who an invite is for, and until when: a whole line per role, so each language can word it. */
function inviteFor(inv: Invite, today: string, lang: string): string {
  const t = (en: string) => tr(lang, en);
  const tf = (en: string, v: Record<string, string | number>) => trf(lang, en, v);
  const until = inv.expires_at.slice(0, 10) === today ? t("until the end of today") : tf("until {date}", { date: inviteDay(inv.expires_at, lang) });
  const role = roleOf({ ...inv, id: "", name: "", me: false });
  if (role === "admin") return tf("A link for one admin, {until}. It works once. Send it only to them.", { until });
  if (role === "viewer") return tf("A link for one member who can only view, {until}. It works once. Send it only to them.", { until });
  return tf("A link for one member, {until}. It works once. Send it only to them.", { until });
}

function inviteNodes(doc: Document, d: InviteDraft, today: string, lang: string): HTMLElement {
  const t = (en: string) => tr(lang, en);
  const tf = (en: string, v: Record<string, string | number>) => trf(lang, en, v);
  const box = el(doc, "section", "invite");
  box.append(el(doc, "h2", undefined, t("Invite someone")));
  if (d.made) {
    const inv = d.made;
    box.append(el(doc, "p", "sub", inviteFor(inv, today, lang)));
    box.append(el(doc, "p", "invite-url", inv.join_url));
    const row = el(doc, "div", "row");
    row.append(button(doc, "invite-share", t("Share the link"), "ok"), button(doc, "invite-copy", d.copied ? t("Link copied") : t("Copy the link")));
    if (inv.email) row.append(button(doc, "invite-mail", tf("Email it to {email}", { email: inv.email })));
    box.append(row, button(doc, "invite-again", t("Invite someone else"), "link"));
    return box;
  }
  if (d.myName !== null) {
    const me = el(doc, "label", "field");
    me.append(el(doc, "span", "sub", t("Your name, as the people you invite will see it")));
    const n = el(doc, "input");
    n.dataset.pf = "invite-myname";
    n.maxLength = 40;
    n.autocomplete = "name";
    n.value = d.myName;
    me.append(n);
    box.append(me);
  }
  box.append(roleSelect(doc, "invite-role", d.role, lang, undefined, "They join as"));
  box.append(el(doc, "p", "sub role-help", t(ROLE_HELP[d.role])));
  const lab = el(doc, "label", "field");
  lab.append(el(doc, "span", "sub", t("Their email (optional: your mail app sends it)")));
  const i = el(doc, "input");
  i.type = "email";
  i.dataset.pf = "invite-email";
  i.value = d.email;
  i.autocomplete = "email";
  lab.append(i);
  box.append(lab);
  if (d.error) box.append(errorLine(doc, t(d.error)));
  const row = el(doc, "div", "row");
  const go = button(doc, "invite-make", t("Make the invite link"), "ok");
  go.disabled = d.busy;
  row.append(go, button(doc, "invite-cancel", t("Cancel"), "link"));
  box.append(row);
  return box;
}

function alertsNodes(doc: Document, st: PeopleState, a: Alerts, appliances: { id: string; name: string }[], lang: string): HTMLElement {
  const t = (en: string) => tr(lang, en);
  const box = el(doc, "section", "alerts");
  box.append(el(doc, "h2", undefined, t("Your alerts")));
  box.append(el(doc, "p", "sub", t("Only for you, on your own phone. A job given to someone reminds only them; a job given to nobody reminds everyone who follows its appliance.")));
  const how = el(doc, "fieldset");
  how.append(el(doc, "legend", undefined, t("How")));
  how.append(
    check(doc, "push", t("Notifications on this phone"), a.push),
    check(doc, "calendar", t("In your calendar"), a.calendar),
    check(doc, "hub", t("Through Home Assistant and other hubs"), a.hub),
  );
  box.append(how);
  const what = el(doc, "fieldset");
  what.append(el(doc, "legend", undefined, t("For")));
  what.append(check(doc, "items", t("Every appliance"), a.items === "all", "all", "radio", "alert-items"), check(doc, "items", t("Only the ones you choose"), a.items === "chosen", "chosen", "radio", "alert-items"));
  if (a.items === "chosen") {
    const list = el(doc, "div", "follows");
    for (const ap of appliances) {
      const c = check(doc, "follow", ap.name, a.follows.includes(ap.id), ap.id);
      list.append(c);
    }
    if (!appliances.length) list.append(el(doc, "p", "sub", t("No appliances in this home yet.")));
    what.append(list);
  }
  box.append(what);
  const when = el(doc, "fieldset");
  when.append(el(doc, "legend", undefined, t("When")));
  when.append(check(doc, "quiet", t("Quiet hours"), a.quiet !== null));
  if (a.quiet) {
    const row = el(doc, "div", "row quiet");
    for (const [f, label] of [["quiet-from", t("From")], ["quiet-to", t("To")]] as const) {
      const lab = el(doc, "label", "field");
      lab.append(el(doc, "span", "sub", label));
      const i = el(doc, "input");
      i.type = "time";
      i.dataset.pf = f;
      i.value = f === "quiet-from" ? a.quiet.from : a.quiet.to;
      lab.append(i);
      row.append(lab);
    }
    when.append(row, el(doc, "p", "sub", t("In your home's time. A reminder due then waits until they end. A STOP is never held back.")));
  }
  box.append(when);
  if (st.alertsError) box.append(errorLine(doc, t(st.alertsError)));
  const save = button(doc, "alerts-save", t("Save your alerts"), "ok");
  save.disabled = st.busy;
  box.append(save);
  if (st.note) {
    const n = el(doc, "p", "sub note", t(st.note));
    n.setAttribute("role", "status");
    box.append(n);
  }
  return box;
}

/** The People and alerts screen: who is in the home, inviting (admins), and the caller's own alerts. The refusals
 * and notes in st are the English of PEOPLE_TEXT, put in the language here. */
export function peopleNodes(doc: Document, st: PeopleState, appliances: { id: string; name: string }[], today: string, lang = "en"): Node[] {
  const t = (en: string) => tr(lang, en);
  const out: Node[] = [el(doc, "h1", undefined, t("Household"))];
  if (st.people === null) {
    const p = el(doc, "p", "sub loading", t(PEOPLE_TEXT.loading));
    p.setAttribute("role", "status");
    return [...out, p];
  }
  const access = accessOf(st.people);
  const admin = access === "admin";
  const admins = st.people.filter((p) => p.role === "admin").length;
  if (access === "viewer") out.push(el(doc, "p", "sub", t(PEOPLE_TEXT.viewer)));
  const ul = el(doc, "ul", "people");
  // You first, then the admins, then by name.
  const sorted = [...st.people].sort((a, b) => Number(b.me) - Number(a.me) || Number(b.role === "admin") - Number(a.role === "admin") || a.name.localeCompare(b.name));
  for (const p of sorted) ul.append(personRow(doc, st, p, admin, admins, lang));
  out.push(ul);
  if (admin && st.people.length === 1 && !st.invite) out.push(el(doc, "p", "sub", t("Just you so far. Invite someone to share the jobs: each person gets their own reminders.")));
  if (st.error) out.push(errorLine(doc, t(st.error)));
  if (admin) out.push(st.invite ? inviteNodes(doc, st.invite, today, lang) : button(doc, "invite-open", t("Invite someone"), "big"));
  if (st.draft) out.push(alertsNodes(doc, st, st.draft, appliances, lang));
  return out;
}

/** What makes the screen differ: redrawn only when it changes, so typing is never interrupted. */
export function peopleKey(st: PeopleState, appliances: { id: string; name: string }[], lang = "en"): string {
  const d = st.draft;
  return JSON.stringify([
    langOf(lang),
    st.people,
    d && [d.items, d.quiet !== null, d.items === "chosen" ? d.follows : null],
    st.invite && [st.invite.made, st.invite.copied, st.invite.error, st.invite.busy, st.invite.role],
    st.rename?.id ?? null,
    st.confirm,
    st.busy,
    st.error,
    st.alertsError,
    st.note,
    appliances,
  ]);
}

/** The join screen: the name the others will see, then Join. */
export function joinNodes(doc: Document, j: JoinState, lang = "en"): Node[] {
  const t = (en: string) => tr(lang, en);
  const lab = el(doc, "label", "field");
  lab.append(el(doc, "span", "sub", t("Your name")));
  const i = el(doc, "input");
  i.dataset.pf = "join-name";
  i.maxLength = 40;
  i.autocomplete = "name";
  i.enterKeyHint = "go";
  i.value = j.name;
  lab.append(i);
  const out: Node[] = [el(doc, "h1", undefined, t(JOIN_TEXT.title)), el(doc, "p", "lead", t(JOIN_TEXT.lead))];
  if (j.switching) out.push(el(doc, "p", "sub", t(JOIN_TEXT.switching)));
  out.push(lab);
  if (j.error) out.push(errorLine(doc, t(j.error)));
  const go = button(doc, "join-go", t("Join"), "ok big");
  go.disabled = j.busy;
  out.push(go);
  return out;
}

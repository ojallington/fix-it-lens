// @vitest-environment jsdom
// People in a home: who may do what, the invite and alerts bodies, refusals in plain words, and the screen.
import { describe, expect, it } from "vitest";
import {
  accessOf,
  alertsBody,
  inviteBody,
  inviteDraft,
  inviteMail,
  joinCode,
  joinError,
  parseAlerts,
  parseInvite,
  parsePeople,
  peopleError,
  peopleInitial,
  peopleNodes,
  PEOPLE_TEXT,
  roleBody,
  type Alerts,
  type Person,
} from "../src/people.ts";

const me: Person = { id: "p1", name: "Ana", role: "admin", view_only: false, me: true };
const sam: Person = { id: "p2", name: "Sam", role: "member", view_only: false, me: false };
const kid: Person = { id: "p3", name: "Kit", role: "member", view_only: true, me: false };
const alerts: Alerts = { push: true, calendar: true, hub: true, items: "all", follows: [], quiet: null };

describe("roles", () => {
  it("reads the caller's access from the people list; unknown keeps the app as it was", () => {
    expect(accessOf([me, sam])).toBe("admin");
    expect(accessOf([{ ...sam, me: true }])).toBe("member");
    expect(accessOf([{ ...kid, me: true }])).toBe("viewer");
    expect(accessOf(null)).toBeNull();
    expect(accessOf([sam])).toBeNull();
  });

  it("an admin is never view-only", () => {
    expect(roleBody("admin")).toEqual({ role: "admin", view_only: false });
    expect(roleBody("viewer")).toEqual({ role: "member", view_only: true });
    expect(roleBody("member")).toEqual({ role: "member", view_only: false });
  });

  it("parses only well-formed replies", () => {
    expect(parsePeople([me, sam])).toEqual([me, sam]);
    expect(parsePeople([{ id: "x" }])).toBeNull();
    expect(parseAlerts({ ...alerts, quiet: { from: "22:00", to: "07:00" } })?.quiet).toEqual({ from: "22:00", to: "07:00" });
    expect(parseAlerts({ ...alerts, quiet: { from: "25:00", to: "07:00" } })).toBeNull();
    expect(parseInvite({ code: "c", join_url: "https://x/#join/c", role: "member", view_only: true, email: null, expires_at: "2026-11-03T00:00:00Z" })?.view_only).toBe(true);
    expect(parseInvite({ code: "c" })).toBeNull();
  });
});

describe("invites", () => {
  it("sends the role, an email only when given and well formed, and a 1-14 day life", () => {
    expect(inviteBody({ ...inviteDraft(), role: "viewer" })).toEqual({ body: { role: "member", view_only: true, email: null, expires_in_days: 7 } });
    expect(inviteBody({ ...inviteDraft(), email: " sam@example.org ", days: 30 })).toEqual({ body: { role: "member", view_only: false, email: "sam@example.org", expires_in_days: 14 } });
    expect(inviteBody({ ...inviteDraft(), email: "sam@" })).toEqual({ error: PEOPLE_TEXT.email });
  });

  it("the mail carries the link; the server sends none", () => {
    const m = inviteMail({ code: "c", join_url: "https://fix.example/#join/AbC_1", role: "member", view_only: false, email: "sam@example.org", expires_at: "2026-11-03T00:00:00Z" }, "Demo home");
    expect(m.startsWith("mailto:sam%40example.org?subject=")).toBe(true);
    expect(decodeURIComponent(m)).toContain("https://fix.example/#join/AbC_1");
    expect(decodeURIComponent(m)).toContain("until 2026-11-03");
  });

  it("reads a join link from the fragment only", () => {
    expect(joinCode("#join/AbC_1-xyz")).toBe("AbC_1-xyz");
    expect(joinCode("#join/<x>")).toBeNull();
    expect(joinCode("#return/AbC_1")).toBeNull();
  });

  it("a refused join or write says why in plain words", () => {
    expect(joinError(403)).toMatch(/^This invite doesn't work any more/);
    expect(joinError(409, "name_taken")).toBe(PEOPLE_TEXT.nameTaken);
    expect(joinError(500)).toMatch(/^Couldn't join/);
    expect(peopleError(409, "last_admin")).toBe(PEOPLE_TEXT.lastAdmin);
    expect(peopleError(403)).toBe(PEOPLE_TEXT.notAdmin);
    expect(peopleError(422, "follows: not in this home")).toBe(PEOPLE_TEXT.notInHome);
  });
});

describe("alerts", () => {
  it("quiet hours need two different times; chosen needs an appliance; every appliance sends no list", () => {
    expect(alertsBody({ ...alerts, quiet: { from: "22:00", to: "22:00" } })).toEqual({ error: PEOPLE_TEXT.quiet });
    expect(alertsBody({ ...alerts, items: "chosen", follows: [] })).toEqual({ error: PEOPLE_TEXT.follows });
    expect(alertsBody({ ...alerts, follows: ["a1"] })).toEqual({ body: { ...alerts, follows: [] } });
    expect(alertsBody({ ...alerts, items: "chosen", follows: ["a1"], quiet: { from: "22:00", to: "07:00" } })).toEqual({
      body: { ...alerts, items: "chosen", follows: ["a1"], quiet: { from: "22:00", to: "07:00" } },
    });
  });
});

describe("the screen", () => {
  const apps = [{ id: "a1", name: "Washing machine" }];
  const text = (nodes: Node[]) => nodes.map((n) => n.textContent).join(" | ");
  const acts = (nodes: Node[]) => nodes.flatMap((n) => [...(n as HTMLElement).querySelectorAll?.("[data-act]") ?? [], n]).map((b) => (b as HTMLElement).dataset?.act).filter(Boolean);

  it("an admin sees everyone, can change roles and remove, invite; the last admin is not offered Leave", () => {
    const st = { ...peopleInitial(), people: [sam, me, kid], draft: alerts };
    const nodes = peopleNodes(document, st, apps, "2026-10-27");
    const t = text(nodes);
    expect(t.indexOf("Ana")).toBeLessThan(t.indexOf("Sam")); // you first
    expect(t).toContain("Can only view");
    expect(acts(nodes)).toContain("invite-open");
    expect(acts(nodes)).toContain("people-remove");
    expect(acts(nodes)).not.toContain("people-leave");
    const selects = nodes.flatMap((n) => [...((n as HTMLElement).querySelectorAll?.("select") ?? [])]);
    expect(selects.map((s) => s.dataset.person)).toEqual(["p3", "p2"]); // admins, then by name
  });

  it("a view-only person reads the list, may leave, sets their own alerts, and is offered no invite or role", () => {
    const st = { ...peopleInitial(), people: [{ ...me, me: false }, { ...kid, me: true }], draft: alerts };
    const nodes = peopleNodes(document, st, apps, "2026-10-27");
    expect(text(nodes)).toContain(PEOPLE_TEXT.viewer);
    expect(acts(nodes)).not.toContain("invite-open");
    expect(acts(nodes)).not.toContain("people-remove");
    expect(acts(nodes)).toContain("people-leave");
    expect(acts(nodes)).toContain("alerts-save");
    expect(nodes.flatMap((n) => [...((n as HTMLElement).querySelectorAll?.("select") ?? [])])).toEqual([]);
  });

  it("a made invite shows its link once, with share, copy and the mail when an email was given", () => {
    const made = { code: "c", join_url: "https://fix.example/#join/c", role: "member" as const, view_only: false, email: "sam@example.org", expires_at: "2026-11-03T00:00:00Z" };
    const st = { ...peopleInitial(), people: [me], invite: { ...inviteDraft(), made } };
    const nodes = peopleNodes(document, st, apps, "2026-10-27");
    expect(text(nodes)).toContain("https://fix.example/#join/c");
    expect(acts(nodes)).toEqual(expect.arrayContaining(["invite-share", "invite-copy", "invite-mail"]));
  });

  it("chosen alerts list the home's appliances to follow; quiet hours show their times", () => {
    const st = { ...peopleInitial(), people: [me], draft: { ...alerts, items: "chosen" as const, follows: ["a1"], quiet: { from: "22:00", to: "07:00" } } };
    const nodes = peopleNodes(document, st, apps, "2026-10-27");
    const inputs = nodes.flatMap((n) => [...((n as HTMLElement).querySelectorAll?.("input") ?? [])]);
    expect(inputs.find((i) => i.dataset.pf === "follow")?.checked).toBe(true);
    expect(inputs.filter((i) => i.type === "time").map((i) => i.value)).toEqual(["22:00", "07:00"]);
  });
});

// @vitest-environment jsdom
// People, joining, alerts and Connections in Dutch: each screen reads Dutch, keeps people's names and labels as given,
// and an invite's mail is in the sender's language.
import { describe, expect, it } from "vitest";
import { connDraft, connInitial, connKey, connNodes, type Connection, type ConnState } from "../src/connections.ts";
import {
  inviteDraft,
  inviteMail,
  inviteShareTitle,
  joinNodes,
  PEOPLE_TEXT,
  peopleInitial,
  peopleKey,
  peopleNodes,
  type Alerts,
  type Invite,
  type JoinState,
  type PeopleState,
  type Person,
} from "../src/people.ts";

const ENGLISH = /\b(the|your|you|and|with|this|Add|Remove|Close|Save|Cancel|Delete|Done|Edit|Back)\b/;
// The fixture's own data, which stays as given.
const DATA = ["Sam", "Ana", "Bosch dishwasher", "Kitchen hub", "https://fix.example/#join/AbC_1", "sam@example.org"];

function text(nodes: Node[]): string {
  const box = document.createElement("div");
  box.append(...nodes);
  const parts: string[] = [box.textContent ?? ""];
  for (const e of box.querySelectorAll<HTMLElement>("[aria-label],[placeholder],[title]")) parts.push(e.getAttribute("aria-label") ?? "", e.getAttribute("placeholder") ?? "", e.getAttribute("title") ?? "");
  let s = parts.join(" ");
  for (const d of DATA) s = s.split(d).join(" ");
  return s;
}

const me: Person = { id: "p1", name: "Sam", role: "admin", view_only: false, me: true };
const other: Person = { id: "p2", name: "Ana", role: "member", view_only: true, me: false };
const alerts: Alerts = { push: true, calendar: false, hub: true, items: "chosen", follows: ["a1"], quiet: { from: "22:00", to: "07:00" } };
const apps = [{ id: "a1", name: "Bosch dishwasher" }];
const invite: Invite = { code: "c", join_url: "https://fix.example/#join/AbC_1", role: "member", view_only: false, email: "sam@example.org", expires_at: "2026-11-03T00:00:00Z" };

describe("People and alerts in Dutch", () => {
  it("names the screen, the roles and the alerts in Dutch, with the people's names as given", () => {
    const st: PeopleState = { ...peopleInitial(), people: [me, other], alerts, draft: alerts, invite: { ...inviteDraft(), role: "viewer" }, error: PEOPLE_TEXT.lastAdmin };
    const nodes = peopleNodes(document, st, apps, "2026-10-27", "nl");
    const all = text(nodes);
    expect((nodes[0] as HTMLElement).textContent).toBe("Huishouden");
    const box = document.createElement("div");
    box.append(...peopleNodes(document, st, apps, "2026-10-27", "nl"));
    expect(box.querySelector(".tag")?.textContent).toBe("Beheerder · jij");
    expect(box.textContent).toContain("Ana");
    expect([...box.querySelectorAll("option")].map((o) => o.textContent)).toContain("Kan alleen kijken");
    expect(all).toContain("Een huis houdt minstens één beheerder.");
    expect(all).toContain("Stille uren");
    expect(all).toContain("Sla je meldingen op");
    expect(all).toContain("Ziet de apparaten en klussen van het huis");
    expect(all).not.toMatch(ENGLISH);
  });

  it("words a made invite and a removal in Dutch, with a Dutch date", () => {
    const st: PeopleState = { ...peopleInitial(), people: [me, other], invite: { ...inviteDraft(), made: invite }, confirm: { id: "p2", kind: "remove" } };
    const all = text(peopleNodes(document, st, apps, "2026-10-27", "nl"));
    expect(all).toContain("Een link voor één lid, tot 3 november 2026. Hij werkt één keer.");
    expect(all).toContain(" verwijderen? Hun telefoons openen dit huis dan niet meer");
    expect(all).toContain("Mail hem naar");
    expect(all).not.toMatch(ENGLISH);
    const today = text(peopleNodes(document, { ...st, invite: { ...inviteDraft(), made: { ...invite, view_only: true } } }, apps, "2026-11-03", "nl"));
    expect(today).toContain("Een link voor één lid dat alleen kan kijken, tot het eind van vandaag.");
  });

  it("a viewer and the loading screen read Dutch, and an English phone reads as before", () => {
    const viewer = text(peopleNodes(document, { ...peopleInitial(), people: [{ ...me, role: "member", view_only: true }] }, apps, "2026-10-27", "nl"));
    expect(viewer).toContain("Je kunt dit huis bekijken, maar niet veranderen.");
    expect(viewer).not.toMatch(ENGLISH);
    expect(text(peopleNodes(document, peopleInitial(), apps, "2026-10-27", "nl"))).toContain("De mensen in dit huis ophalen…");
    expect((peopleNodes(document, peopleInitial(), apps, "2026-10-27")[0] as HTMLElement).textContent).toBe("Household");
  });

  it("redraws when the language changes", () => {
    const st = peopleInitial();
    expect(peopleKey(st, apps, "nl")).not.toBe(peopleKey(st, apps, "en"));
  });
});

describe("an invite's share and mail in Dutch", () => {
  it("the subject, the body and the date are Dutch; the link is as made", () => {
    const m = decodeURIComponent(inviteMail(invite, null, "nl"));
    expect(m).toContain("subject=Doe mee in ons huis op Fix-It Lens");
    expect(m).toContain("Open deze link op je telefoon om mee te doen in ons huis op Fix-It Lens:\n\nhttps://fix.example/#join/AbC_1\n\nHij werkt één keer, tot 3 november 2026.");
    expect(decodeURIComponent(inviteMail(invite, "Demo home", "nl"))).toContain("Doe mee in Demo home op Fix-It Lens");
    expect(inviteShareTitle("nl")).toBe("Doe mee in ons huis op Fix-It Lens");
    expect(inviteShareTitle()).toBe("Join our home on Fix-It Lens");
    expect(decodeURIComponent(inviteMail(invite, null))).toContain("Open this link on your phone to join our home on Fix-It Lens:\n\nhttps://fix.example/#join/AbC_1\n\nIt works once, until 2026-11-03.");
  });
});

describe("joining in Dutch", () => {
  const join: JoinState = { kind: "join", code: "AbC_1", name: "", busy: false, error: PEOPLE_TEXT.nameTaken, switching: true };

  it("the invite's screen", () => {
    const nodes = joinNodes(document, join, "nl");
    const all = text(nodes);
    expect((nodes[0] as HTMLElement).textContent).toBe("Meedoen in een huis");
    expect(all).toContain("Je naam");
    expect(all).toContain("Iemand in dit huis heeft die naam al.");
    expect(all).toContain("Deze telefoon opent al een huis.");
    expect(all).toContain("Meedoen");
    expect(all).not.toMatch(ENGLISH);
  });

  it("the first invite asks the inviter's own name", () => {
    const named = { ...me, name: "Admin" };
    const st: PeopleState = { ...peopleInitial(), people: [named], invite: inviteDraft(named) };
    const all = text(peopleNodes(document, st, apps, "2026-10-27", "nl"));
    expect(all).toContain("Je naam, zoals de mensen die je uitnodigt hem zien");
    expect(all).not.toMatch(ENGLISH);
    expect(inviteDraft(me).myName).toBeNull();
  });
});

describe("Connections in Dutch", () => {
  const ha: Connection = { id: "t1", by_kind: "ha", scope: "write", label: "Kitchen hub", created_at: "2026-10-20T08:00:00Z", last_used_at: null };
  const loaded: ConnState = { ...connInitial(), loaded: true, feed: "https://fix.example/feed.ics", feedShown: false, tokens: [ha] };

  it("lists the keys, with their labels as given", () => {
    const all = text(connNodes(document, { ...loaded, error: "Couldn't do that. Try again." }, "2026-10-27", "nl"));
    expect(all).toContain("Koppelingen");
    expect(all).toContain("Je agenda");
    expect(all).toContain("Toon het adres");
    expect(all).toContain("Home Assistant · kan kijken en het schema veranderen · toegevoegd");
    expect(all).toContain("nog niet gebruikt");
    expect(all).toContain("Dat lukte niet. Probeer het opnieuw.");
    expect(all).toContain("Voeg een koppeling toe");
    expect(all).not.toMatch(ENGLISH);
  });

  it("a new key, its form, and a removal", () => {
    const made = text(connNodes(document, { ...loaded, made: { kind: "agent", label: "Kitchen hub", token: "k", address: "https://fix.example/mcp" } }, "2026-10-27", "nl"));
    expect(made).toContain("De sleutel voor");
    expect(made).toContain("Zet deze in de MCP-instellingen van je agent.");
    expect(made).toContain("Kopieer de sleutel");
    expect(made).not.toMatch(ENGLISH);
    const form = text(connNodes(document, { ...loaded, draft: connDraft("agent", "nl"), confirm: "t1" }, "2026-10-27", "nl"));
    expect(form).toContain("Een AI-agent (MCP)");
    expect(form).toContain("Kijken en het schema veranderen");
    expect(form).toContain("verwijderen? Het werkt dan meteen niet meer.");
    expect(form).not.toMatch(ENGLISH);
  });

  it("a new agent's name starts in the person's language, and the screen redraws on a language change", () => {
    expect(connDraft("agent", "nl").label).toBe("AI-agent");
    expect(connDraft("agent").label).toBe("AI agent");
    expect(connDraft("ha", "nl").label).toBe("Home Assistant");
    expect(connKey(loaded, "nl")).not.toBe(connKey(loaded));
    expect(text(connNodes(document, connInitial(), "2026-10-27", "nl"))).toContain("Je koppelingen ophalen…");
  });
});

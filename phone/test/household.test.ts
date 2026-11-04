import { describe, expect, it } from "vitest";
import { authed, capOf, capText, APP_CONSENT_PLACEHOLDER, consentLine, doorCappedText, doorFromUrl, doorState, joinCapText } from "../src/household.ts";

describe("consent line", () => {
  const f = { en: "EN text", nl: "NL tekst", form_version: 1, signed: false };
  it("is the placeholder with no file, the text with its draft tag until approved, then the text alone", () => {
    expect(consentLine(null, "en")).toEqual({ text: APP_CONSENT_PLACEHOLDER, status: null });
    const g = { ...f, status: { en: "Draft wording", nl: "Concepttekst" } };
    expect(consentLine(g, "nl")).toEqual({ text: "NL tekst", status: "Concepttekst", heading: null, body: [] });
    expect(consentLine(g, "de")).toEqual({ text: "EN text", status: "Draft wording", heading: null, body: [] });
    expect(consentLine({ ...g, signed: true }, "en")).toEqual({ text: "EN text", status: null, heading: null, body: [] });
    expect(consentLine({ ...g, signed: true }, "de").text).toBe("EN text");
  });

  it("splits a whole consent screen: the first paragraph heads it, the last sits beside the box", () => {
    const screen = { ...f, en: "Before you start\n\nPictures go to Meta.\n\nYou can delete it all.\n\nI am 18 or older and agree to this." };
    expect(consentLine(screen, "en")).toEqual({
      heading: "Before you start",
      body: ["Pictures go to Meta.", "You can delete it all."],
      text: "I am 18 or older and agree to this.",
      status: "Draft wording",
    });
  });
});

describe("authed", () => {
  it("adds the bearer only when there is a token, keeping other headers", () => {
    expect(authed({ method: "GET" }, null)).toEqual({ method: "GET" });
    const h = new Headers(authed({ headers: { "content-type": "application/json" } }, "t").headers);
    expect(h.get("authorization")).toBe("Bearer t");
    expect(h.get("content-type")).toBe("application/json");
  });
});

describe("the doors in the address", () => {
  it("reads a return link, a judges' link on /try, an invite, and the site's Start; nothing else is a door", () => {
    expect(doorFromUrl("/", "#return/abc_9-Z", null)).toEqual({ kind: "return", code: "abc_9-Z" });
    expect(doorFromUrl("/try", "#k=K1", null)).toEqual({ kind: "try", code: "K1" });
    expect(doorFromUrl("/try/", "", null)).toEqual({ kind: "open" }); // a printed QR, with no code
    expect(doorFromUrl("/", "#k=K1", null)).toBeNull();
    expect(doorFromUrl("/", "", "INV")).toEqual({ kind: "invite", code: "INV" });
    expect(doorFromUrl("/start", "", null)).toEqual({ kind: "open" });
    expect(doorFromUrl("/", "", null, true)).toEqual({ kind: "open" });
    expect(doorFromUrl("/", "#job/a1/x", null)).toBeNull();
    expect(doorFromUrl("/", "#return/<script>", null)).toBeNull();
  });
});

describe("spend caps", () => {
  const now = new Date("2026-10-27T15:00:00Z");
  it("reads a CapError from a 429 body or a 4429 close reason, and nothing else", () => {
    expect(capOf({ error: "home_cap", reopens_at: "2026-10-28T00:00:00Z" })).toEqual({ error: "home_cap", reopensAt: "2026-10-28T00:00:00Z" });
    expect(capOf('{"error":"global_cap","reopens_at":null}')).toEqual({ error: "global_cap", reopensAt: null });
    expect(capOf({ error: "too_many_reports" })).toBeNull();
    expect(capOf("not json")).toBeNull();
  });

  it("a judges' link's spend never reopens; the door's own limits name tomorrow's opening", () => {
    const t = capText({ error: "judges_cap", reopensAt: null }, now);
    expect(t).toMatch(/^This link has used all the guided help it had, and it doesn't reopen\./);
    expect(t).not.toMatch(/opens again|tomorrow/);
    expect(capOf('{"error":"door_cap","reopens_at":"2026-10-28T00:00:00Z"}')?.error).toBe("door_cap");
    expect(joinCapText({ error: "door_cap", reopensAt: "2026-10-28T00:00:00Z" }, now, "en-GB", "Europe/Amsterdam")).toBe(
      "Today's new homes are all taken. New homes open again tomorrow at 01:00. You can still watch the demo replay.",
    );
    expect(joinCapText({ error: "address_cap", reopensAt: "2026-10-28T00:00:00Z" }, now, "en-GB", "Europe/Amsterdam")).toBe(
      "A new home was already opened from this connection today. Another can open tomorrow at 01:00. You can still watch the demo replay.",
    );
  });

  it("reads the door's state before consent; a capped door with no time says none is known", async () => {
    const reply = (status: number, body: unknown) => (async () => new Response(JSON.stringify(body), { status })) as typeof fetch;
    expect(await doorState(reply(200, { state: "shut", reopens_at: null }), "/api/v1")).toEqual({ state: "shut", reopensAt: null });
    expect(await doorState(reply(200, { state: "capped", reopens_at: "2026-10-28T00:00:00Z" }), "/api/v1")).toEqual({ state: "capped", reopensAt: "2026-10-28T00:00:00Z" });
    expect(await doorState(reply(404, {}), "/api/v1")).toBeNull();
    expect(await doorState(reply(200, { state: "ajar" }), "/api/v1")).toBeNull();
    expect(doorCappedText(null, now)).toBe(
      "Fix-It Lens isn't opening new homes just now. No time is known yet for when it opens again. You can still watch the demo replay.",
    );
    expect(doorCappedText("2026-10-28T00:00:00Z", now, "en-GB", "Europe/Amsterdam")).toMatch(/It opens again tomorrow at 01:00\./);
  });

  it("says which limit, when it opens again in the phone's time, and what still works", () => {
    expect(capText({ error: "home_cap", reopensAt: "2026-10-28T00:00:00Z" }, now, "en-GB", "Europe/Amsterdam")).toBe(
      "Your home has used today's guided help. It opens again tomorrow at 01:00. Your reminders, your appliances and the demo replay still work.",
    );
    expect(capText({ error: "judges_full", reopensAt: null }, now)).toBe("This link has opened all the homes it can. Ask whoever sent it for a new one, or watch the demo replay.");
    expect(capText({ error: "global_cap", reopensAt: null }, now)).toMatch(/^Fix-It Lens has reached today's limit for everyone\. Guided help opens again later\./);
  });

  it("says the same in Dutch, with the time in Dutch words, for a Dutch phone", () => {
    const tz = "Europe/Amsterdam";
    expect(doorCappedText("2026-10-28T00:00:00Z", now, "nl-NL", tz)).toBe(
      "Fix-It Lens laat nu even geen nieuwe huizen toe. Het gaat morgen om 01:00 weer open. Je kunt nog wel de demo-opname bekijken.",
    );
    expect(doorCappedText(null, now, "nl")).toMatch(/Er is nog niet bekend wanneer het weer opengaat\./);
    expect(joinCapText({ error: "door_cap", reopensAt: "2026-10-27T20:00:00Z" }, now, "nl", tz)).toBe(
      "De nieuwe huizen van vandaag zijn allemaal vergeven. Nieuwe huizen gaan om 21:00 weer open. Je kunt nog wel de demo-opname bekijken.",
    );
    expect(capText({ error: "home_cap", reopensAt: "2026-10-30T08:00:00Z" }, now, "nl", tz)).toBe(
      "Je huis heeft de begeleide hulp van vandaag gebruikt. Die gaat op 30 oktober om 09:00 weer open. Je herinneringen, je apparaten en de demo-opname werken nog gewoon.",
    );
    for (const error of ["home_cap", "judges_cap", "door_cap", "address_cap", "global_cap", "judges_full"] as const) {
      const t = joinCapText({ error, reopensAt: null }, now, "nl");
      expect(t, error).not.toMatch(/\b(the|you|your|home|link has)\b/i);
      expect(joinCapText({ error, reopensAt: null }, now, "en")).not.toBe(t);
    }
  });
});

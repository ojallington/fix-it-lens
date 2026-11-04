// @vitest-environment jsdom
// Connections: the home's keys parsed without secrets, what a new one sends, where it goes, and the screen.
import { describe, expect, it } from "vitest";
import { connAddress, connBody, connDraft, connInitial, connNodes, parseConnections, parseMade, type Connection } from "../src/connections.ts";

const ha: Connection = { id: "t1", by_kind: "ha", scope: "write", label: "Home Assistant", created_at: "2026-10-20T09:00:00Z", last_used_at: null };

describe("connections", () => {
  it("parses only well-formed replies", () => {
    expect(parseConnections([ha])).toEqual([ha]);
    expect(parseConnections([{ ...ha, by_kind: "app" }])).toBeNull();
    expect(parseConnections({})).toBeNull();
    expect(parseMade({ id: "t2", token: "k" })).toEqual({ id: "t2", token: "k" });
    expect(parseMade({ id: "t2", token: "" })).toBeNull();
  });

  it("Home Assistant starts able to write, an agent only to look; a name is 1 to 60 characters", () => {
    expect(connDraft("ha")).toMatchObject({ scope: "write", label: "Home Assistant" });
    expect(connDraft("agent")).toMatchObject({ scope: "read", label: "AI agent" });
    expect(connBody({ ...connDraft("agent"), label: " Kitchen bot " })).toEqual({ body: { by_kind: "agent", scope: "read", label: "Kitchen bot" } });
    expect(connBody({ ...connDraft(), label: "x".repeat(61) })).toHaveProperty("error");
  });

  it("an agent's key goes to /mcp, Home Assistant's to the app's address", () => {
    expect(connAddress("agent", "https://fix.example/")).toBe("https://fix.example/mcp");
    expect(connAddress("ha", "https://fix.example")).toBe("https://fix.example");
  });

  it("a home with nothing set up says so; a list never carries a key", () => {
    const empty = connNodes(document, { ...connInitial(), loaded: true }, "2026-10-27");
    expect(empty.map((n) => n.textContent).join(" ")).toContain("Nothing to set up here");
    const listed = connNodes(document, { ...connInitial(), loaded: true, tokens: [ha] }, "2026-10-27");
    const t = listed.map((n) => n.textContent).join(" ");
    expect(t).toContain("not used yet");
    expect(t).toContain("Add a connection");
  });
});

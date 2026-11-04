import { describe, expect, it } from "vitest";
import { FB_TEXT, fbBody, fbError, fbInitial, isPilot, MAX_WRITTEN } from "../src/feedback.ts";

describe("feedback form", () => {
  it("counts only a home the server calls a pilot home", () => {
    expect(isPilot({ pilot: true })).toBe(true);
    for (const x of [{ pilot: false }, { pilot: "true" }, {}, null, "x"]) expect(isPilot(x)).toBe(false);
  });

  it("refuses an answer the server would refuse, rather than cutting it", () => {
    const st = fbInitial("en");
    st.written.hard = "x".repeat(MAX_WRITTEN + 1);
    expect(fbBody(st)).toEqual({ error: FB_TEXT.en.tooLong });
    st.written.hard = "x".repeat(MAX_WRITTEN);
    expect("body" in fbBody(st)).toBe(true);
  });

  it("drops a name typed for another choice", () => {
    const st = fbInitial("nl-NL");
    st.quoteName = "Ana";
    st.quote = "anonymous";
    expect(fbBody(st)).toMatchObject({ body: { lang: "nl", quote: "anonymous", quote_name: null } });
  });

  it("says the server's no in the form's language", () => {
    expect(fbError(403, "nl")).toBe("Dit formulier is alleen voor huizen in de pilot.");
    expect(fbError(429, "en")).toBe("This home has sent all the forms it can.");
    expect(fbError(0, "en")).toBe(FB_TEXT.en.failed);
  });

  it("has every line in both languages", () => {
    const keys = (o: object): string[] => Object.entries(o).flatMap(([k, v]) => (typeof v === "object" ? keys(v).map((x) => `${k}.${x}`) : [k]));
    expect(keys(FB_TEXT.nl)).toEqual(keys(FB_TEXT.en));
  });
});

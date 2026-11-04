import { describe, expect, it } from "vitest";
import type { PlateRead } from "../../contracts/ts/src/ws.ts";
import { confirmArg, initialChoices, question, resolved } from "../src/plate.ts";

const P: PlateRead = {
  type: "plate_read",
  seq: 1,
  t_ms: 0,
  photo_id: "p1",
  brand: "acme",
  model: "WM-0000",
  fields: [{ name: "model", value: "WM-OOOO", uncertain: [{ index: 3, options: ["O", "0"] }] }],
};

describe("plate card", () => {
  it("asks each unsure character in words", () => {
    expect(question(P.fields[0]!.uncertain[0]!)).toBe("Letter O or zero?");
    // A letter the table lacks is still named as a letter, and anything else is quoted.
    expect(question({ index: 4, options: ["H", "M"] })).toBe("Letter H or letter M?");
    expect(question({ index: 4, options: ["H", "M"] }, "nl")).toBe("Letter H of letter M?");
    expect(question({ index: 0, options: ["-", "/"] })).toBe("“-” or “/”?");
  });
  it("puts the chosen characters into the value and the tap arg", () => {
    const c = initialChoices(P);
    c.model = ["0"];
    expect(resolved(P.fields[0]!, c.model)).toBe("WM-0OOO");
    expect(JSON.parse(confirmArg(c))).toEqual({ model: ["0"] });
  });
});

describe("the unsure character's cells", () => {
  it("name each reading under its glyph, a digit by its word and a letter as LETTER", async () => {
    const { glyphWord } = await import("../src/ui.ts");
    expect([glyphWord("1", "en"), glyphWord("I", "en"), glyphWord("0", "en"), glyphWord("o", "en"), glyphWord("/", "en")]).toEqual(["ONE", "LETTER I", "ZERO", "LETTER O", ""]);
    expect(glyphWord("1", "nl-NL")).toBe("EEN");
  });
});

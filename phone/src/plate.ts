// The plate card: what the plate read says, with each unsure character asked once in amber ("Letter O or zero?"),
// and one tap to confirm. Serial numbers never come to the phone's screen: the server sends only the public fields.

import type { PlateField, PlateRead, PlateUncertain } from "../../contracts/ts/src/ws.ts";
import { langOf } from "./words.ts";

const NAMES: Record<string, string> = {
  O: "letter O",
  "0": "zero",
  I: "letter I",
  l: "small L",
  "1": "one",
  B: "letter B",
  "8": "eight",
  S: "letter S",
  "5": "five",
  Z: "letter Z",
  "2": "two",
  G: "letter G",
  "6": "six",
  D: "letter D",
};

const NAMES_NL: Record<string, string> = {
  O: "letter O",
  "0": "nul",
  I: "letter I",
  l: "kleine L",
  "1": "één",
  B: "letter B",
  "8": "acht",
  S: "letter S",
  "5": "vijf",
  Z: "letter Z",
  "2": "twee",
  G: "letter G",
  "6": "zes",
  D: "letter D",
};

export function question(u: PlateUncertain, lang = "en"): string {
  const nl = langOf(lang) === "nl";
  // A letter missing from the table is still named as one ("letter H", the same in Dutch), as under its cell.
  const opts = u.options.map((c) => (nl ? NAMES_NL : NAMES)[c] ?? (/^\p{L}$/u.test(c) ? `letter ${c}` : `“${c}”`));
  const or = nl ? "of" : "or";
  const text = opts.length === 2 ? `${opts[0]} ${or} ${opts[1]}?` : `${opts.slice(0, -1).join(", ")} ${or} ${opts.at(-1)}?`;
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Choices per field: one chosen option per uncertain index, in order. Starts at each first option. */
export type Choices = Record<string, string[]>;

export function initialChoices(p: PlateRead): Choices {
  const c: Choices = {};
  for (const f of p.fields) c[f.name] = f.uncertain.map((u) => u.options[0]);
  return c;
}

/** The field's value with the chosen characters put in. */
export function resolved(f: PlateField, chosen: string[]): string {
  const chars = [...f.value];
  f.uncertain.forEach((u, i) => {
    if (chosen[i] !== undefined) chars[u.index] = chosen[i]!;
  });
  return chars.join("");
}

/** The Tap arg for confirm_plate (contracts 1.1): JSON {field: [chosen option per uncertain index]}. */
export function confirmArg(c: Choices): string {
  return JSON.stringify(c);
}

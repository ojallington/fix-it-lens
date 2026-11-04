// The fixed disclaimer and AI disclosure from rules/disclaimer.json (generated from rules/disclaimer.yaml): the
// first-run screen (18+ and the AI line, EU AI Act Art. 50(1)), the step card's citation, and the terms, all drawn as
// fixed UI text, never through G0 and never model text; a language it lacks falls back to English. The spoken line is
// not the phone's: the session says it first, as a G0-passed sentence.

export type DisclaimerKey = "first_run" | "spoken" | "step_citation" | "terms";

export type Disclaimers = { version: string } & Record<DisclaimerKey, Record<string, string>>;

/** Text that came from the disclaimer file and nowhere else. */
export type FixedText = string & { readonly __fixed: true };

export function disclaimer(d: Disclaimers | null, key: DisclaimerKey, lang: string, page?: number): FixedText | null {
  const table = d?.[key];
  const text = table?.[lang.slice(0, 2).toLowerCase()] ?? table?.en;
  if (text === undefined) return null;
  return (page === undefined ? text : text.replace("{page}", String(page))) as FixedText;
}

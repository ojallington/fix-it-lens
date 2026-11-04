// Every example in contracts/examples/ validates in TypeScript; every invalid/ example is rejected.
import { readdirSync, readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { SCHEMAS, validate, type ContractName } from "../src/validate.ts";
import type { ws, safety, card } from "../src/index.ts";

const examples = join(dirname(fileURLToPath(import.meta.url)), "..", "..", "examples");

function cases(invalid: boolean): [string, ContractName, string | undefined, unknown][] {
  const out: [string, ContractName, string | undefined, unknown][] = [];
  for (const name of readdirSync(examples)) {
    const dir = invalid ? join(examples, name, "invalid") : join(examples, name);
    if (!existsSync(dir)) continue;
    for (const f of readdirSync(dir).filter((f) => f.endsWith(".json"))) {
      const stem = f.replace(/\.json$/, "");
      const def = stem.includes("--") ? stem.split("--")[0] : undefined;
      out.push([`${name}/${f}`, name as ContractName, def, JSON.parse(readFileSync(join(dir, f), "utf8"))]);
    }
  }
  return out;
}

describe("contracts examples", () => {
  it("covers every schema", () => {
    expect(new Set(cases(false).map((c) => c[1]))).toEqual(new Set(Object.keys(SCHEMAS)));
  });
  it.each(cases(false))("valid %s", (_label, name, def, value) => {
    expect(() => validate(name, value, def)).not.toThrow();
  });
  it.each(cases(true))("invalid %s", (_label, name, def, value) => {
    expect(() => validate(name, value, def)).toThrow();
  });
});

describe("generated types", () => {
  it("type the protocol", () => {
    const held: ws.Held = { type: "held", seq: 1, t_ms: 0, rule_id: "nl.mains.casing", line: "x", words: 3 };
    const stop: safety.Stop = { state: "STOP", rule_id: "nl.gas.tap", evidence: "e", script: "s" };
    const msg: ws.S2C = held;
    const part: card.Part = { id: "f", names: { en: "filter" }, locate: "appearance", cue: "", sam_text: "filter", kind: "allowed" };
    expect(() => validate("ws", msg, "S2C")).not.toThrow();
    expect(() => validate("safety", stop, "SafetyState")).not.toThrow();
    expect(part.kind).toBe("allowed");
  });
});

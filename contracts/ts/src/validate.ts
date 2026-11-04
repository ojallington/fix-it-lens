// Hand-written: validates a value against a contract schema (or one of its $defs) with Ajv 2020-12.
import Ajv2020 from "ajv/dist/2020.js";
import addFormats from "ajv-formats";
import type { ValidateFunction } from "ajv";
import card from "../../card.schema.json" with { type: "json" };
import rules from "../../rules.schema.json" with { type: "json" };
import g1 from "../../g1.schema.json" with { type: "json" };
import safety from "../../safety.schema.json" with { type: "json" };
import tools from "../../tools.schema.json" with { type: "json" };
import judge from "../../judge.schema.json" with { type: "json" };
import ws from "../../ws.schema.json" with { type: "json" };
import session from "../../session.schema.json" with { type: "json" };
import ledger from "../../ledger.schema.json" with { type: "json" };
import feedback from "../../feedback.schema.json" with { type: "json" };
import claim from "../../claim.schema.json" with { type: "json" };

export const SCHEMAS = { card, rules, g1, safety, tools, judge, ws, session, ledger, claim, feedback } as const;
export type ContractName = keyof typeof SCHEMAS;

const ajv = new Ajv2020({ strict: false, allErrors: true });
addFormats.default(ajv);
for (const s of Object.values(SCHEMAS)) ajv.addSchema(s);

const cache = new Map<string, ValidateFunction>();

export function validator(name: ContractName, definition?: string): ValidateFunction {
  const id = SCHEMAS[name].$id;
  const ref = definition ? `${id}#/$defs/${definition}` : id;
  let fn = cache.get(ref);
  if (!fn) {
    fn = ajv.getSchema(ref) ?? ajv.compile({ $ref: ref });
    cache.set(ref, fn);
  }
  return fn;
}

// Invariants the JSON Schema cannot state; contracts/__init__.py has the same ones.
export function cardInvariants(card: { jobs?: { steps?: { id?: unknown }[] }[] }): string[] {
  const seen = new Map<string, number>();
  for (const job of card.jobs ?? []) for (const s of job.steps ?? []) seen.set(String(s.id), (seen.get(String(s.id)) ?? 0) + 1);
  return [...seen].filter(([, n]) => n > 1).sort().map(([id, n]) => `step id '${id}' is used ${n} times; step ids are unique per card`);
}

export function citeArgsInvariants(a: { step_id?: unknown; page?: unknown; quote?: unknown }): string[] {
  const step = a.step_id != null;
  const manual = [a.page, a.quote].map((v) => v != null);
  if (step ? !manual.some(Boolean) : manual.every(Boolean)) return [];
  return ["cite takes step_id alone, or page and quote together"];
}

// keyed "<schema>" for a whole document, "<schema>#<definition>" for one definition
const INVARIANTS: Record<string, (v: never) => string[]> = { card: cardInvariants, "tools#CiteArgs": citeArgsInvariants };

export function validate(name: ContractName, value: unknown, definition?: string): void {
  const fn = validator(name, definition);
  if (!fn(value)) throw new Error(`${name}${definition ? "#" + definition : ""}: ${ajv.errorsText(fn.errors)}`);
  const key = definition ? `${name}#${definition}` : name;
  const problems = INVARIANTS[key]?.(value as never) ?? [];
  if (problems.length) throw new Error(`${key}: ${problems.join("; ")}`);
}

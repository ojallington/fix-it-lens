// Generates src/<name>.ts from ../<name>.schema.json. Run by `make contracts`; `make contracts-check` diffs.
import { compile } from "json-schema-to-typescript";
import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { dirname, join, basename, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const out = resolve(here, process.argv[2] ?? "src");
export const NAMES = ["card", "rules", "g1", "safety", "tools", "judge", "ws", "session", "ledger", "claim", "feedback"];

const local = {
  order: 1,
  canRead: /fixit\.invalid\/contracts\//,
  read: (file) => readFileSync(join(root, basename(new URL(file.url).pathname)), "utf8"),
};

mkdirSync(out, { recursive: true });
const banner = "/* Generated from contracts/*.schema.json by `make contracts`. Do not edit. */";
for (const name of NAMES) {
  const schema = JSON.parse(readFileSync(join(root, `${name}.schema.json`), "utf8"));
  const ts = await compile(schema, schema.title ?? name, {
    bannerComment: banner,
    unreachableDefinitions: true,
    declareExternallyReferenced: true,
    additionalProperties: false,
    strictIndexSignatures: true,
    format: true,
    cwd: root,
    $refOptions: { resolve: { fixit: local, http: false, https: false } },
  });
  writeFileSync(join(out, `${name}.ts`), ts);
}
const index = [banner, ...NAMES.map((n) => `export * as ${n} from "./${n}.ts";`), ""].join("\n");
writeFileSync(join(out, "index.ts"), index);

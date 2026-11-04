import { defineConfig } from "vitest/config";

// The G1 constants and vectors come from rules/g1.json and the contracts from contracts/; both are read from the repo at build time.
export default defineConfig({
  build: { target: "es2022", outDir: "dist" },
  worker: { format: "es" },
  server: { host: true, port: 5173, fs: { allow: [".."] }, proxy: { "/api": process.env.FIXIT_API ?? "http://127.0.0.1:8000", "/ws": { target: process.env.FIXIT_API ?? "http://127.0.0.1:8000", ws: true } } },
  test: { environment: "node" },
});

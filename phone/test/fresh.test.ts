// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { bundleOf, newBuild, typing } from "../src/fresh.ts";

const page = (js: string) => `<!doctype html><script type="module" crossorigin src="/assets/${js}"></script>`;
const serve = (body: string, status = 200) => (async () => new Response(body, { status })) as unknown as typeof fetch;

describe("a new build on the server", () => {
  it("reads the bundle a page loads, and none from the dev server", () => {
    expect(bundleOf(page("index-Zl9cTrWu.js"))).toBe("assets/index-Zl9cTrWu.js");
    expect(bundleOf('<script type="module" src="./src/main.ts"></script>')).toBeNull();
  });

  it("is new only when the server's page loads another bundle; a failure or the dev server is no news", async () => {
    expect(await newBuild(serve(page("index-B.js")), "assets/index-A.js")).toBe(true);
    expect(await newBuild(serve(page("index-A.js")), "assets/index-A.js")).toBe(false);
    expect(await newBuild(serve("down", 502), "assets/index-A.js")).toBe(false);
    expect(await newBuild((async () => Promise.reject(new Error("offline"))) as unknown as typeof fetch, "assets/index-A.js")).toBe(false);
    expect(await newBuild(serve(page("index-B.js")), null)).toBe(false);
  });

  it("counts typed text not yet sent, not ticks or untouched fields", () => {
    const root = document.createElement("div");
    root.innerHTML = '<input type="checkbox"><input type="text" value="kept"><textarea></textarea>';
    expect(typing(root)).toBe(false);
    (root.querySelector("input[type=checkbox]") as HTMLInputElement).checked = true;
    expect(typing(root)).toBe(false);
    (root.querySelector("textarea") as HTMLTextAreaElement).value = "the flap is stuck";
    expect(typing(root)).toBe(true);
  });
});

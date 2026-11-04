// A new build on the server: an installed app kept in memory never reloads by itself (the service worker is network
// first, so any reload gets the new build). When the app comes back to the front, it asks for the page again and, if
// the page names another bundle, reloads, but only when nothing is under way: no session, no replay, nothing typed.

/** The bundle a page's HTML loads (/assets/index-<hash>.js), or null (the dev server has none). */
export function bundleOf(html: string): string | null {
  return /assets\/index-[\w-]+\.js/.exec(html)?.[0] ?? null;
}

/** True when the server's page loads another bundle than the running one. Any failure reads as no news. */
export async function newBuild(fetchFn: typeof fetch, running: string | null): Promise<boolean> {
  if (running === null) return false;
  const r = await fetchFn("/", { cache: "no-store" }).catch(() => null);
  if (!r?.ok) return false;
  const next = bundleOf(await r.text().catch(() => ""));
  return next !== null && next !== running;
}

/** Text typed into the page and not yet sent: a reload would lose it. */
export function typing(root: ParentNode): boolean {
  for (const f of root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("textarea, input")) {
    const kind = f instanceof HTMLInputElement ? f.type : "textarea";
    if (["checkbox", "radio", "button", "submit", "hidden", "range", "file"].includes(kind)) continue;
    if (f.value !== f.defaultValue && f.value.trim() !== "") return true;
  }
  return false;
}

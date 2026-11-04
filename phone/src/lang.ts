// The app's language, first that applies: ?lang in the address; the language picked in Settings on this device; the
// home's own language (a Dutch phone in an English home reads English, never a Dutch page a browser then translates);
// the last ?lang this device opened, so the installed app (which opens at ./) keeps it; the phone's own language.

/** Where each remembered language lives. */
export const LANG_KEYS = { link: "fixit.lang.link.v2", pick: "fixit.lang.pick.v1", home: "fixit.home_lang.v1" } as const;
/** The key that once held every choice at once (a ?lang link and a Settings pick alike): dropped, not read, so an old
 * Dutch choice can't outrank the home's language on a phone that never picked one since. */
export const OLD_LANG_KEY = "fixit.lang.v1";
export type LangSlot = keyof typeof LANG_KEYS;

/** The languages the settings switch offers, in their own names. */
export const LANGS: readonly (readonly [string, string])[] = [
  ["en", "English"],
  ["nl", "Nederlands"],
];

const VALID = /^[a-z]{2}(-[a-z]{2})?$/i;

export interface LangStore {
  get(slot: LangSlot): string | null;
  set(slot: LangSlot, lang: string): void;
}

/** The device's remembered language in localStorage; a private window that refuses storage remembers nothing. */
export function localLangStore(): LangStore {
  try {
    localStorage.removeItem(OLD_LANG_KEY);
  } catch {
    // Nothing kept, nothing to drop.
  }
  return {
    get(slot) {
      try {
        return localStorage.getItem(LANG_KEYS[slot]);
      } catch {
        return null;
      }
    },
    set(slot, lang) {
      try {
        localStorage.setItem(LANG_KEYS[slot], lang);
      } catch {
        // Not remembered: the address or the phone's language decides next time.
      }
    },
  };
}

/** The language to use now, remembering the address's when it names one. */
export function chooseLang(search: string, store: LangStore, phone: string): string {
  const asked = new URLSearchParams(search).get("lang");
  if (asked && VALID.test(asked)) {
    store.set("link", asked);
    return asked;
  }
  for (const slot of ["pick", "home", "link"] as const) {
    const kept = store.get(slot);
    if (kept && VALID.test(kept)) return kept;
  }
  return phone.slice(0, 2).toLowerCase() || "en";
}

/** The address without ?lang, so a choice made in settings is not overruled by the old one on reload. */
export function withoutLang(search: string): string {
  const p = new URLSearchParams(search);
  p.delete("lang");
  const s = p.toString();
  return s ? `?${s}` : "";
}

/** Remembers the home's language from GET /household and gives the language that now applies when it differs from the
 * one showing, so the page can be reloaded once in it; null when nothing changes. */
export function adoptHomeLang(search: string, store: LangStore, phone: string, home: unknown, showing: string): string | null {
  if (typeof home !== "string" || !VALID.test(home)) return null;
  store.set("home", home);
  const now = chooseLang(search, store, phone);
  return now === showing ? null : now;
}

/** A pick in Settings applies at once: it is remembered above the home's, ?lang leaves the address so it can't
 * overrule the pick, and the page reloads in the new language. A reload, not a navigation to the same address: from
 * #settings that would only move to the fragment and leave the screen in the old language. */
export function pickLang(store: LangStore, lang: string, page: { search: string; replaceSearch(search: string): void; reload(): void }): void {
  if (!VALID.test(lang)) return;
  store.set("pick", lang);
  if (new URLSearchParams(page.search).has("lang")) page.replaceSearch(withoutLang(page.search));
  page.reload();
}

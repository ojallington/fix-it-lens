// The app's language: the address's ?lang is remembered, so the installed app (opened at ./) keeps it.

import { describe, expect, it } from "vitest";
import { adoptHomeLang, chooseLang, LANG_KEYS, localLangStore, OLD_LANG_KEY, pickLang, withoutLang, type LangSlot, type LangStore } from "../src/lang.ts";

function store(kept: string | null = null, more: Partial<Record<LangSlot, string>> = {}): LangStore & { kept: string | null; slots: Partial<Record<LangSlot, string>> } {
  const s = {
    kept,
    slots: more,
    get: (k: LangSlot) => (k === "link" ? s.kept : (s.slots[k] ?? null)),
    set: (k: LangSlot, v: string) => void (k === "link" ? (s.kept = v) : (s.slots[k] = v)),
  };
  return s;
}

describe("chooseLang", () => {
  it("takes ?lang and remembers it", () => {
    const s = store();
    expect(chooseLang("?lang=en", s, "nl-NL")).toBe("en");
    expect(s.kept).toBe("en");
  });

  it("uses the remembered language when the address has none, as the installed app opens", () => {
    expect(chooseLang("", store("en"), "nl-NL")).toBe("en");
  });

  it("lets a new ?lang replace the remembered one", () => {
    const s = store("en");
    expect(chooseLang("?lang=nl", s, "en-GB")).toBe("nl");
    expect(s.kept).toBe("nl");
  });

  it("falls back to the phone's language, then English, and keeps nothing it can't use", () => {
    const s = store();
    expect(chooseLang("", s, "nl-NL")).toBe("nl");
    expect(chooseLang("?lang=<b>", s, "")).toBe("en");
    expect(chooseLang("", store("not a language"), "de-DE")).toBe("de");
    expect(s.kept).toBeNull();
  });
});

describe("the home's language", () => {
  it("wins over the phone's language and a remembered link, so a Dutch phone in an English home reads English", () => {
    expect(chooseLang("", store("nl", { home: "en" }), "nl-NL")).toBe("en");
  });

  it("gives way to a pick in Settings and to ?lang in the address", () => {
    expect(chooseLang("", store(null, { home: "en", pick: "nl" }), "en-GB")).toBe("nl");
    expect(chooseLang("?lang=nl", store(null, { home: "en" }), "en-GB")).toBe("nl");
  });

  it("is remembered from GET /household and asks for one reload only when the page shows another language", () => {
    const s = store("nl");
    expect(adoptHomeLang("", s, "nl-NL", "en", "nl")).toBe("en");
    expect(s.slots.home).toBe("en");
    expect(adoptHomeLang("", s, "nl-NL", "en", "en")).toBeNull();
    expect(adoptHomeLang("?lang=nl", s, "nl-NL", "en", "nl")).toBeNull();
    expect(adoptHomeLang("", store(), "nl-NL", undefined, "nl")).toBeNull();
  });
});

describe("a pick in Settings", () => {
  function page(search: string) {
    const p = { search, replaced: null as string | null, reloads: 0, replaceSearch: (s: string) => void (p.replaced = s), reload: () => void p.reloads++ };
    return p;
  }

  it("reloads the screen at once in the picked language, both ways, even from #settings", () => {
    const s = store(null, { home: "en" });
    const p = page("");
    pickLang(s, "nl", p);
    expect(p.reloads).toBe(1);
    expect(chooseLang(p.search, s, "en-GB")).toBe("nl");
    pickLang(s, "en", p);
    expect(p.reloads).toBe(2);
    expect(chooseLang(p.search, s, "nl-NL")).toBe("en");
  });

  it("takes ?lang out of the address so the old link can't overrule the pick on reload", () => {
    const p = page("?replay=abc&lang=en");
    pickLang(store(), "nl", p);
    expect(p.replaced).toBe("?replay=abc");
    expect(p.reloads).toBe(1);
  });

  it("does nothing with a value that is no language", () => {
    const s = store();
    const p = page("");
    pickLang(s, "<b>", p);
    expect(p.reloads).toBe(0);
    expect(s.slots.pick).toBeUndefined();
  });
});

describe("withoutLang", () => {
  it("drops only ?lang", () => {
    expect(withoutLang("?lang=en")).toBe("");
    expect(withoutLang("?replay=abc&lang=en")).toBe("?replay=abc");
    expect(withoutLang("")).toBe("");
  });
});

describe("the old remembered language", () => {
  it("is dropped once, so an old Dutch choice no longer outranks the home's language", () => {
    const kept = new Map<string, string>([[OLD_LANG_KEY, "nl"], [LANG_KEYS.home, "en"]]);
    const was = (globalThis as { localStorage?: unknown }).localStorage;
    (globalThis as { localStorage?: unknown }).localStorage = {
      getItem: (k: string) => kept.get(k) ?? null,
      setItem: (k: string, v: string) => void kept.set(k, v),
      removeItem: (k: string) => void kept.delete(k),
    };
    try {
      const store = localLangStore();
      expect(kept.has(OLD_LANG_KEY)).toBe(false);
      expect(chooseLang("", store, "nl-NL")).toBe("en");
    } finally {
      (globalThis as { localStorage?: unknown }).localStorage = was;
    }
  });
});

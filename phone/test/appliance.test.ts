import { describe, expect, it } from "vitest";
import { etaText, manualLine, MAX_PDF_BYTES, pdfProblem, quotesLine, refusedLines, uploadError, wantsManual, PDF_TEXT } from "../src/appliance.ts";

const PDF = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d]);

describe("the manual's PDF, checked on the phone", () => {
  it("takes up to 50 MB of bytes that start %PDF", () => {
    expect(pdfProblem(MAX_PDF_BYTES, PDF)).toBeNull();
    expect(pdfProblem(MAX_PDF_BYTES + 1, PDF)).toBe("too_large");
    expect(pdfProblem(10, new TextEncoder().encode("<htm"))).toBe("not_a_pdf");
    expect(pdfProblem(2, new Uint8Array([0x25, 0x50]))).toBe("not_a_pdf");
  });

  it("words the server's answers by status first, then by error code", () => {
    expect(uploadError(413, undefined)).toBe(PDF_TEXT.too_large);
    expect(uploadError(415, "not_a_pdf")).toBe(PDF_TEXT.not_a_pdf);
    expect(uploadError(403, "forbidden")).toBe(PDF_TEXT.view_only);
    expect(uploadError(404, undefined)).toBe(PDF_TEXT.gone);
    expect(uploadError(0, undefined)).toBe(PDF_TEXT.failed);
  });

  it("offers the manual's controls only for a notice a new manual can fix", () => {
    const n = (kind: "lookup_failed" | "ingest_failed" | "plate_unreadable", reason?: never) => ({ kind, text: {}, reason });
    expect(wantsManual(n("lookup_failed"))).toBe(true);
    expect(wantsManual({ ...n("ingest_failed"), reason: "scanned_unreadable" })).toBe(true);
    expect(wantsManual({ ...n("ingest_failed"), reason: "site_blocked" })).toBe(true);
    expect(wantsManual({ ...n("ingest_failed"), reason: "timeout" })).toBe(false);
    expect(wantsManual({ ...n("ingest_failed"), reason: "cap_reached" })).toBe(false);
    expect(wantsManual(n("ingest_failed"))).toBe(false);
    expect(wantsManual(n("plate_unreadable"))).toBe(false);
    expect(wantsManual(null)).toBe(false);
  });
});

describe("the wait's lines", () => {
  it("rounds the server's estimate, and says nothing near the end or without one", () => {
    expect(etaText(null)).toBeNull();
    expect(etaText(5)).toBeNull();
    expect(etaText(45)).toBe("Less than a minute left");
    expect(etaText(140)).toBe("About 2 min left");
  });

  it("names the manual's language only when it isn't the household's", () => {
    expect(manualLine({ pages: 1, lang: "en", user_lang: true })).toBe("Manual found: 1 page.");
    expect(manualLine({ pages: 48, lang: "de", user_lang: false })).toBe("Manual found: 48 pages, in German.");
    expect(manualLine({ pages: 9, lang: "pl", user_lang: false })).toBe("Manual found: 9 pages, not in your language.");
    expect(manualLine(undefined)).toBeNull();
  });

  it("counts quotes and groups refused jobs by reason, never by title", () => {
    expect(quotesLine({ checked: 0, passed: 0 })).toBeNull();
    expect(quotesLine({ checked: 3, passed: 3 })).toBe("Quotes checked against the pages: 3 of 3 found.");
    expect(refusedLines([{ id: "a", reason: "other" }, { id: "b", reason: "needs_professional" }, { id: "c", reason: "other" }])).toEqual([
      "2 jobs left out: the safety rules don't allow it.",
      "1 job left out: it's a job for a professional.",
    ]);
  });
});

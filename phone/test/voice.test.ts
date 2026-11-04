// Speech picks a voice that runs on the device when the language has one: a network voice sends the text away.
import { describe, expect, it, vi } from "vitest";
import { browserSynth, pickVoice, Speech } from "../src/speech.ts";

const net = (lang: string, name: string) => ({ lang, name, localService: false });
const dev = (lang: string, name: string) => ({ lang, name, localService: true });

describe("pickVoice", () => {
  it("takes the on-device voice over a network one listed before it", () => {
    const voices = [net("en-US", "Google US English"), dev("de-DE", "Anna"), dev("en-GB", "Daniel")];
    expect(pickVoice(voices, "en")?.name).toBe("Daniel");
  });

  it("falls back to the first voice for the language when none runs on the device", () => {
    expect(pickVoice([dev("de-DE", "Anna"), net("nl-NL", "Google Nederlands"), net("nl-BE", "Google Vlaams")], "nl")?.name).toBe("Google Nederlands");
  });

  it("never takes another language's voice, local or not; none for the language is null", () => {
    expect(pickVoice([dev("de-DE", "Anna")], "nl")).toBeNull();
    expect(pickVoice([], "en")).toBeNull();
  });

  it("matches the language without regard to case", () => {
    expect(pickVoice([net("NL-nl", "A"), dev("nl-NL", "B")], "nl")?.name).toBe("B");
  });

  it("when only an on-device voice will do (local mode), a network voice is never the answer", () => {
    const voices = [net("en-US", "Google US English"), dev("en-GB", "Daniel")];
    expect(pickVoice(voices, "en", true)?.name).toBe("Daniel");
    expect(pickVoice([net("nl-NL", "Google Nederlands")], "nl", true)).toBeNull();
    expect(pickVoice([net("nl-NL", "Google Nederlands")], "nl", false)?.name).toBe("Google Nederlands");
  });
});

describe("Speech", () => {
  it("a sentence the synthesiser could not say is not counted as spoken, and marks the missing voice", () => {
    const speech = new Speech({ speak: () => false, cancel: () => undefined }, "nl");
    speech.onDeviceOnly = true;
    speech.say({ type: "sentence", seq: 1, t_ms: 1, id: "s1", text: "Til het filter eruit." });
    expect(speech.spoken).toEqual([]);
    expect(speech.noVoice).toBe(true);
  });
});

// A speechSynthesis whose voice list arrives late, as Chrome's does on a first load.
class LateSynth {
  voices: SpeechSynthesisVoice[] = [];
  said: { text: string; voice: string | null }[] = [];
  cancels = 0;
  private fns: (() => void)[] = [];
  getVoices = () => this.voices;
  speak = (u: SpeechSynthesisUtterance) => void this.said.push({ text: u.text, voice: u.voice?.name ?? null });
  cancel = () => void this.cancels++;
  addEventListener = (_: "voiceschanged", fn: () => void) => void this.fns.push(fn);
  removeEventListener = (_: "voiceschanged", fn: () => void) => void (this.fns = this.fns.filter((f) => f !== fn));
  arrive(voices: { lang: string; name: string; localService: boolean }[]): void {
    this.voices = voices as SpeechSynthesisVoice[];
    for (const f of [...this.fns]) f();
  }
}
const utter = (text: string) => ({ text, lang: "", voice: null }) as unknown as SpeechSynthesisUtterance;
const flush = () => new Promise((r) => setTimeout(r, 0));

describe("a voice list that arrives late", () => {
  it("local mode waits for it, so the first sentences are spoken, in order, with the on-device voice", async () => {
    const ss = new LateSynth();
    const speech = new Speech(browserSynth(ss, utter), "en");
    speech.onDeviceOnly = true;
    speech.say({ type: "sentence", seq: 1, t_ms: 1, id: "s1", text: "One." });
    speech.say({ type: "sentence", seq: 2, t_ms: 2, id: "s2", text: "Two." });
    expect(ss.said).toEqual([]);
    ss.arrive([net("en-US", "Google US English"), dev("en-GB", "Daniel")]);
    await flush();
    expect(ss.said).toEqual([{ text: "One.", voice: "Daniel" }, { text: "Two.", voice: "Daniel" }]);
    expect(speech.spoken).toEqual(["One.", "Two."]);
    expect(speech.noVoice).toBe(false);
  });

  it("gives up after the wait: local mode stays silent and says so, other modes speak with the default voice", async () => {
    vi.useFakeTimers();
    try {
      for (const local of [true, false]) {
        const ss = new LateSynth();
        const speech = new Speech(browserSynth(ss, utter, 700), "en");
        speech.onDeviceOnly = local;
        speech.say({ type: "sentence", seq: 1, t_ms: 1, id: "s1", text: "One." });
        await vi.advanceTimersByTimeAsync(699);
        expect(ss.said).toEqual([]);
        await vi.advanceTimersByTimeAsync(1);
        expect(ss.said).toEqual(local ? [] : [{ text: "One.", voice: null }]);
        expect(speech.noVoice).toBe(local);
      }
    } finally {
      vi.useRealTimers();
    }
  });

  it("a stop while it waits drops the sentence", async () => {
    const ss = new LateSynth();
    const speech = new Speech(browserSynth(ss, utter), "en");
    speech.onDeviceOnly = true;
    speech.say({ type: "sentence", seq: 1, t_ms: 1, id: "s1", text: "One." });
    speech.silence();
    ss.arrive([dev("en-GB", "Daniel")]);
    await flush();
    expect(ss.said).toEqual([]);
    expect(speech.spoken).toEqual([]);
  });
});

describe("the missing-voice notice", () => {
  it("clears once an ordinary sentence is spoken on the device; a STOP line in the fallback voice does not clear it", () => {
    let local = false;
    const synth = { speak: (u: { onDeviceOnly: boolean }) => !u.onDeviceOnly || local, cancel: () => undefined };
    const speech = new Speech(synth, "en");
    speech.onDeviceOnly = true;
    const line = (text: string) => ({ type: "sentence" as const, seq: 1, t_ms: 1, id: text, text });
    speech.say(line("One."));
    expect(speech.noVoice).toBe(true);
    speech.say(line("Leave the house now."), true);
    expect(speech.noVoice).toBe(true);
    local = true; // the device's voice has loaded
    speech.say(line("Two."));
    expect(speech.noVoice).toBe(false);
    expect(speech.spoken).toEqual(["Leave the house now.", "Two."]);
  });

  it("a list that comes after the wait ran out is used from the next sentence on", async () => {
    vi.useFakeTimers();
    try {
      const ss = new LateSynth();
      const speech = new Speech(browserSynth(ss, utter, 700), "en");
      speech.onDeviceOnly = true;
      speech.say({ type: "sentence", seq: 1, t_ms: 1, id: "s1", text: "One." });
      await vi.advanceTimersByTimeAsync(700);
      expect(speech.noVoice).toBe(true);
      ss.arrive([dev("en-GB", "Daniel")]);
      speech.say({ type: "sentence", seq: 2, t_ms: 2, id: "s2", text: "Two." });
      await vi.advanceTimersByTimeAsync(0);
      expect(ss.said).toEqual([{ text: "Two.", voice: "Daniel" }]);
      expect(speech.noVoice).toBe(false);
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("a voice list that stays empty", () => {
  const line = (text: string) => ({ type: "sentence" as const, seq: 1, t_ms: 1, id: text, text });

  it("is waited for once; the next sentence is not held again", async () => {
    vi.useFakeTimers();
    try {
      const ss = new LateSynth();
      const speech = new Speech(browserSynth(ss, utter, 700), "en");
      speech.say(line("One."));
      await vi.advanceTimersByTimeAsync(700);
      expect(ss.said).toEqual([{ text: "One.", voice: null }]);
      speech.say(line("Two."));
      await vi.advanceTimersByTimeAsync(0);
      expect(ss.said.map((x) => x.text)).toEqual(["One.", "Two."]);
      // Once the browser says the list changed, a still-empty list is worth one more wait.
      ss.arrive([]);
      speech.say(line("Three."));
      await vi.advanceTimersByTimeAsync(0);
      expect(ss.said.map((x) => x.text)).toEqual(["One.", "Two."]);
      await vi.advanceTimersByTimeAsync(700);
      expect(ss.said.map((x) => x.text)).toEqual(["One.", "Two.", "Three."]);
    } finally {
      vi.useRealTimers();
    }
  });

  it("never holds a STOP line: it is said at once, with the fallback in local mode", () => {
    const ss = new LateSynth();
    const speech = new Speech(browserSynth(ss, utter, 700), "en");
    speech.onDeviceOnly = true;
    speech.say(line("Leave the house now."), true);
    expect(ss.said).toEqual([{ text: "Leave the house now.", voice: null }]);
    expect(speech.spoken).toEqual(["Leave the house now."]);
  });
});

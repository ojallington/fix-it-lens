// Speech out is the browser's speechSynthesis. It speaks only `sentence` messages, which G0 has already passed, one
// whole sentence at a time; the type of `say` admits nothing else. The spoken AI line at session start is the
// session's first sentence (the server sends it through G0), so the phone never says it on its own. A stop silences it.

import type { Sentence } from "../../contracts/ts/src/ws.ts";

export interface SynthLike {
  /** false: nothing was said (local mode and no on-device voice for the language). A promise while the browser's
   * voice list is still on its way. */
  speak(u: { text: string; lang: string; onDeviceOnly: boolean; urgent?: boolean }): boolean | void | Promise<boolean>;
  cancel(): void;
  /** The phone is saying something now, or has a line queued (the open microphone takes nothing then). */
  speaking?(): boolean;
}

export class Speech {
  /** Every text handed to the synthesiser, in order (the render-path test and the trace read it). */
  readonly spoken: string[] = [];
  private muted = false;
  /** Local mode: speak only with a voice that runs on this device, else stay silent (the text stays on screen). */
  onDeviceOnly = false;
  /** Set once a sentence went unspoken for want of an on-device voice; the operator view says so. */
  noVoice = false;

  constructor(
    private readonly synth: SynthLike | null,
    private readonly lang: string,
  ) {}

  /** `stopLine`: a line of the rules' fixed STOP script. It must be heard, so it may use any voice, local mode too. */
  say(m: Sentence, stopLine = false): void {
    if (this.muted || !this.synth) return;
    const said = this.synth.speak({ text: m.text, lang: this.lang, onDeviceOnly: this.onDeviceOnly && !stopLine, urgent: stopLine });
    if (said instanceof Promise) void said.then((ok) => this.heard(m.text, ok, stopLine));
    else this.heard(m.text, said !== false, stopLine);
  }

  private heard(text: string, ok: boolean, stopLine: boolean): void {
    if (this.muted) return; // stopped while the voice list loaded: the synthesiser dropped it
    if (!ok) {
      this.noVoice = true;
      return;
    }
    this.spoken.push(text);
    // A voice was found for an ordinary sentence, so the notice no longer holds. A STOP line may have used the
    // fallback voice, which says nothing about an on-device one.
    if (!stopLine) this.noVoice = false;
  }

  /** The stop: silence now, and nothing more until resumed. */
  silence(): void {
    this.muted = true;
    this.synth?.cancel();
  }

  resume(): void {
    this.muted = false;
  }
}

export interface VoiceLike {
  lang: string;
  localService: boolean;
}

/** The voice for the language: one that runs on this device when there is one (a network voice, such as desktop
 * Chrome's "Google …" voices, sends the text away to be spoken), else the first for the language unless only an
 * on-device voice will do; null for none. */
export function pickVoice<V extends VoiceLike>(voices: readonly V[], lang: string, onDeviceOnly = false): V | null {
  const forLang = voices.filter((x) => x.lang.toLowerCase().startsWith(lang.toLowerCase()));
  return forLang.find((x) => x.localService) ?? (onDeviceOnly ? null : (forLang[0] ?? null));
}

/** How long a first sentence waits for a voice list the browser has not loaded yet (Chrome fills it late). */
export const VOICES_WAIT_MS = 700;

/** The parts of window.speechSynthesis used here. */
export interface SpeechSynthesisLike {
  getVoices(): SpeechSynthesisVoice[];
  speak(u: SpeechSynthesisUtterance): void;
  cancel(): void;
  speaking?: boolean;
  pending?: boolean;
  addEventListener(type: "voiceschanged", fn: () => void): void;
  removeEventListener(type: "voiceschanged", fn: () => void): void;
}

/** Wraps window.speechSynthesis, preferring an on-device voice for the language. While the voice list is still empty
 * it waits for voiceschanged (at most VOICES_WAIT_MS) before choosing; a stop meanwhile drops what was waiting. */
export function browserSynth(
  ss: SpeechSynthesisLike | undefined = typeof speechSynthesis === "undefined" ? undefined : speechSynthesis,
  utterance: (text: string) => SpeechSynthesisUtterance = (t) => new SpeechSynthesisUtterance(t),
  waitMs = VOICES_WAIT_MS,
): SynthLike | null {
  if (!ss) return null;
  let stops = 0;
  // Sentences that arrive while the list loads wait in turn, so they are said in order. A wait that ends (list in, or
  // the cap) is spent; an empty list later starts a new one.
  let loading: Promise<void> | null = null;
  // A wait that ran out on a still-empty list is not repeated for every sentence: the list is taken as it is until
  // the browser says it changed.
  let gaveUp = false;
  ss.addEventListener("voiceschanged", () => void (gaveUp = false));
  const voicesLoaded = (): Promise<void> =>
    (loading ??= new Promise<void>((done) => {
      const finish = () => {
        ss.removeEventListener("voiceschanged", finish);
        clearTimeout(timer);
        loading = null;
        gaveUp = ss.getVoices().length === 0;
        done();
      };
      const timer = setTimeout(finish, waitMs);
      ss.addEventListener("voiceschanged", finish);
    }));
  // Asking once at the start makes Chrome begin loading its list, so it is usually there by the first sentence.
  ss.getVoices();
  const say = (text: string, lang: string, onDeviceOnly: boolean): boolean => {
    const v = pickVoice(ss.getVoices(), lang, onDeviceOnly);
    // Left to itself the browser would pick its default voice, which may be a network one.
    if (onDeviceOnly && !v) return false;
    const u = utterance(text);
    u.lang = lang;
    if (v) u.voice = v;
    ss.speak(u);
    return true;
  };
  return {
    speak({ text, lang, onDeviceOnly, urgent = false }) {
      // A STOP line never waits for the list: it is said now with what there is.
      if (urgent || gaveUp || (ss.getVoices().length > 0 && !loading)) return say(text, lang, onDeviceOnly);
      const stop = stops;
      return voicesLoaded().then(() => (stop === stops ? say(text, lang, onDeviceOnly) : true));
    },
    cancel() {
      stops++;
      ss.cancel();
    },
    speaking: () => !!ss.speaking || !!ss.pending || loading !== null,
  };
}

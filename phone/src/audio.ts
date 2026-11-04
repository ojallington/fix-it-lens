import { tr } from "./words.ts";
// Hold-to-talk: record while the button is held, send one WAV (mono, 16-bit, 16 kHz) as an utterance. The server
// transcribes it through calls/; a failed transcription comes back as a notice asking to repeat or tap.

export const RATE = 16000;

export function encodeWav(samples: Float32Array, rate = RATE): Uint8Array {
  const out = new Uint8Array(44 + samples.length * 2);
  const v = new DataView(out.buffer);
  const str = (o: number, s: string): void => {
    for (let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i));
  };
  str(0, "RIFF");
  v.setUint32(4, 36 + samples.length * 2, true);
  str(8, "WAVE");
  str(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true); // PCM
  v.setUint16(22, 1, true); // mono
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true);
  v.setUint16(32, 2, true);
  v.setUint16(34, 16, true);
  str(36, "data");
  v.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const x = Math.max(-1, Math.min(1, samples[i]!));
    v.setInt16(44 + i * 2, x < 0 ? x * 0x8000 : x * 0x7fff, true);
  }
  return out;
}

/** Linear resample to 16 kHz, for browsers that ignore the AudioContext's requested rate. */
export function resample(x: Float32Array, from: number, to = RATE): Float32Array {
  if (from === to) return x;
  const n = Math.floor((x.length * to) / from);
  const y = new Float32Array(n);
  for (let i = 0; i < n; i++) {
    const t = (i * from) / to;
    const a = Math.floor(t);
    const f = t - a;
    y[i] = (x[a] ?? 0) * (1 - f) + (x[a + 1] ?? x[a] ?? 0) * f;
  }
  return y;
}

export function toBase64(bytes: Uint8Array): string {
  let s = "";
  for (let i = 0; i < bytes.length; i += 0x8000) s += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(s);
}

export interface RecorderLike {
  start(): Promise<void>;
  /** Stops (also when start is still waiting on the permission prompt) and returns the WAV as base64, or null. */
  stop(): Promise<string | null>;
}

/** Why the microphone didn't start, in the phone's words. */
export function micErrorText(e: unknown, lang = "en"): string {
  const name = (e as { name?: string } | null)?.name;
  if (name === "NotAllowedError" || name === "SecurityError") return tr(lang, "The microphone is blocked. Allow it in the browser's site settings.");
  if (name === "NotFoundError") return tr(lang, "No microphone found.");
  if (name === "NoSecureContext") return tr(lang, "The microphone needs the https address.");
  return tr(lang, "The microphone didn't start. Try again.");
}

/**
 * Records at the device's own rate and resamples to 16 kHz (Firefox refuses a stream into a context at another rate).
 * The AudioContext is made in the constructor, inside the press, because iOS Safari leaves one made after an await
 * suspended and silent.
 */
export class Recorder implements RecorderLike {
  private readonly ctx: AudioContext;
  private stream: MediaStream | null = null;
  private node: ScriptProcessorNode | null = null;
  private chunks: Float32Array[] = [];
  private started: Promise<void> | null = null;
  private stopped = false;

  constructor() {
    const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    this.ctx = new AC();
    void this.ctx.resume().catch(() => undefined);
  }

  start(): Promise<void> {
    this.started ??= this.open();
    return this.started;
  }

  private async open(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) throw Object.assign(new Error("no secure context"), { name: "NoSecureContext" });
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true } });
    if (this.stopped) {
      stream.getTracks().forEach((t) => t.stop()); // released before the prompt was answered
      return;
    }
    this.stream = stream;
    const src = this.ctx.createMediaStreamSource(stream);
    this.node = this.ctx.createScriptProcessor(4096, 1, 1);
    this.node.onaudioprocess = (e) => this.chunks.push(new Float32Array(e.inputBuffer.getChannelData(0)));
    src.connect(this.node);
    this.node.connect(this.ctx.destination);
  }

  async stop(): Promise<string | null> {
    this.stopped = true;
    await this.started?.catch(() => undefined);
    const rate = this.ctx.sampleRate;
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    await this.ctx.close().catch(() => undefined);
    const len = this.chunks.reduce((n, c) => n + c.length, 0);
    if (len < rate / 4) return null; // under 250 ms: a tap, not speech
    const all = new Float32Array(len);
    let o = 0;
    for (const c of this.chunks) {
      all.set(c, o);
      o += c.length;
    }
    return toBase64(encodeWav(resample(all, rate)));
  }
}

/** Hands-free turns: speech starts a turn after START_MS, silence ends it after END_MS, a turn is cut at MAX_MS, and
 * one with under MIN_MS of speech is dropped (a cough, a clatter). */
export const TURN = { startMs: 300, endMs: 800, maxMs: 15000, minMs: 400, leadMs: 400 };

/**
 * Splits a running microphone into spoken turns by loudness over the room's own floor. The floor follows the quiet
 * chunks only, so a long turn does not raise it.
 */
export class Turns {
  private floor = 0.004;
  private lead: Float32Array[] = [];
  private turn: Float32Array[] = [];
  private turnMs = 0;
  private loudMs = 0;
  private quietMs = 0;
  private on = false;

  constructor(private readonly rate: number) {}

  /** Drops whatever was building (the phone itself is speaking). */
  reset(): void {
    this.lead = [];
    this.turn = [];
    this.turnMs = 0;
    this.loudMs = 0;
    this.quietMs = 0;
    this.on = false;
  }

  /** One chunk of samples in; a finished turn's samples out, else null. */
  feed(x: Float32Array): Float32Array | null {
    const ms = (x.length / this.rate) * 1000;
    let sum = 0;
    for (let i = 0; i < x.length; i++) sum += x[i]! * x[i]!;
    const rms = Math.sqrt(sum / Math.max(1, x.length));
    const loud = rms > Math.max(0.01, this.floor * 3);
    if (!loud) this.floor = this.floor * 0.9 + rms * 0.1;
    if (!this.on) {
      // The start of a word comes before the turn is sure: a short lead-in is kept and goes with it.
      this.lead.push(x);
      while (this.lead.length > 1 && ((this.lead.length - 1) * x.length * 1000) / this.rate > TURN.leadMs + TURN.startMs) this.lead.shift();
      this.loudMs = loud ? this.loudMs + ms : Math.max(0, this.loudMs - ms);
      if (this.loudMs < TURN.startMs) return null;
      this.on = true;
      this.turn = this.lead;
      this.turnMs = this.turn.reduce((n, c) => n + (c.length / this.rate) * 1000, 0);
      this.lead = [];
      this.quietMs = 0;
      return null;
    }
    this.turn.push(x);
    this.turnMs += ms;
    if (loud) {
      this.loudMs += ms;
      this.quietMs = 0;
    } else this.quietMs += ms;
    if (this.quietMs < TURN.endMs && this.turnMs < TURN.maxMs) return null;
    const out = this.loudMs >= TURN.minMs ? join(this.turn) : null;
    this.reset();
    return out;
  }
}

function join(parts: Float32Array[]): Float32Array {
  const all = new Float32Array(parts.reduce((n, c) => n + c.length, 0));
  let o = 0;
  for (const c of parts) {
    all.set(c, o);
    o += c.length;
  }
  return all;
}

export interface ListenerLike {
  /** Opens the microphone; each finished turn comes to onTurn as a 16 kHz WAV in base64. While quiet() is true (the
   * phone is speaking) and for QUIET_AFTER_MS after, nothing is taken. */
  start(onTurn: (wav: string) => void, quiet: () => boolean): Promise<void>;
  stop(): void;
}

/** After the phone stops speaking, the room's echo of it is not taken for the person. */
export const QUIET_AFTER_MS = 300;

/** The microphone left open for hands-free talk. Like Recorder, the AudioContext is made inside the tap. */
export class Listener implements ListenerLike {
  private readonly ctx: AudioContext;
  private stream: MediaStream | null = null;
  private node: ScriptProcessorNode | null = null;
  private stopped = false;

  constructor() {
    const AC = window.AudioContext ?? (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    this.ctx = new AC();
    void this.ctx.resume().catch(() => undefined);
  }

  async start(onTurn: (wav: string) => void, quiet: () => boolean): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) throw Object.assign(new Error("no secure context"), { name: "NoSecureContext" });
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
    if (this.stopped) {
      stream.getTracks().forEach((t) => t.stop());
      return;
    }
    this.stream = stream;
    const rate = this.ctx.sampleRate;
    const turns = new Turns(rate);
    let quietUntil = 0;
    const src = this.ctx.createMediaStreamSource(stream);
    this.node = this.ctx.createScriptProcessor(4096, 1, 1);
    this.node.onaudioprocess = (e) => {
      const now = performance.now();
      if (quiet()) quietUntil = now + QUIET_AFTER_MS;
      if (now < quietUntil) return turns.reset();
      const out = turns.feed(new Float32Array(e.inputBuffer.getChannelData(0)));
      if (out) onTurn(toBase64(encodeWav(resample(out, rate))));
    };
    src.connect(this.node);
    this.node.connect(this.ctx.destination);
  }

  stop(): void {
    this.stopped = true;
    this.node?.disconnect();
    this.stream?.getTracks().forEach((t) => t.stop());
    void this.ctx.close().catch(() => undefined);
  }
}

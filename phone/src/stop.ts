// The stop's sound and touch: a tone and, on Android, a vibration. iOS browsers cannot vibrate, and this does not
// pretend they can. The red screen itself is ui.ts; the decision is g1.ts or the server's safety state.

export interface Alarm {
  fire(): void;
  /** Call from a tap: browsers start audio only after a gesture. */
  unlock?(): void;
  /** True where navigator.vibrate exists (Android Chrome); false on iOS. */
  readonly canVibrate: boolean;
}

const TONE_HZ = 880;
const TONE_MS = 600;
const VIBRATE_MS = [300, 100, 300];

export function browserAlarm(): Alarm {
  let ctx: AudioContext | null = null;
  const canVibrate = typeof navigator !== "undefined" && typeof navigator.vibrate === "function";
  return {
    canVibrate,
    unlock() {
      try {
        ctx ??= new AudioContext();
        void ctx.resume().catch(() => undefined);
      } catch {
        /* no audio on this device: the red screen and the vibration still fire */
      }
    },
    fire() {
      try {
        ctx ??= new AudioContext();
        if (ctx.state === "suspended") void ctx.resume().catch(() => undefined);
        const o = ctx.createOscillator();
        const g = ctx.createGain();
        o.frequency.value = TONE_HZ;
        o.type = "square";
        g.gain.setValueAtTime(0.4, ctx.currentTime);
        g.gain.setValueAtTime(0, ctx.currentTime + TONE_MS / 1000);
        o.connect(g).connect(ctx.destination);
        o.start();
        o.stop(ctx.currentTime + TONE_MS / 1000);
      } catch {
        // No audio (no user gesture yet): the red screen and the vibration still fire.
      }
      if (canVibrate) navigator.vibrate(VIBRATE_MS);
    },
  };
}

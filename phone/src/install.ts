// Keeping the app on the home screen, offered once a job is done and never before: Android's own install prompt, two
// steps on an iPhone in Safari, and "open it in Safari" in another iPhone browser. Never in a replay or a recorded
// run, never in the installed app itself, and not again once added or turned down.

export type InstallKind = "prompt" | "ios-safari" | "ios-other";

export const INSTALL_KEY = "fixit.install.v1";

/** What the device's browser allows: its own prompt (Android and desktop Chromium), Safari's two steps, or neither. */
export function installKind(ua: string, touchPoints: number, hasPrompt: boolean): InstallKind | null {
  if (hasPrompt) return "prompt";
  // iPadOS reports itself as a Mac; a Mac has no touch screen.
  const ios = /iPhone|iPad|iPod/.test(ua) || (/Macintosh/.test(ua) && touchPoints > 1);
  if (!ios) return null;
  // Chrome, Firefox, Edge, Opera and the apps' own in-app browsers on iOS: Safari is the sure way.
  const other = /CriOS|FxiOS|EdgiOS|OPiOS|OPT\/|GSA\/|FBAN|FBAV|Instagram|Twitter|LinkedInApp|Line\//.test(ua);
  return other ? "ios-other" : "ios-safari";
}

/** The browser side, injected so the app's logic stays testable. */
export interface InstallHost {
  /** What can be offered now; null when the app is already installed, the offer was turned down, or nothing works. */
  kind(): InstallKind | null;
  /** Android's prompt; true when the person added it. */
  prompt(): Promise<boolean>;
  /** Not now, or added: either way the offer doesn't come back. */
  done(): void;
}

/** The real host: catches beforeinstallprompt, and remembers an answer on the device. */
export function browserInstall(win: Window): InstallHost {
  let deferred: (Event & { prompt(): Promise<void>; userChoice: Promise<{ outcome: string }> }) | null = null;
  win.addEventListener("beforeinstallprompt", (e) => {
    // Held for the moment after a job is done, instead of the browser's own banner at a random time.
    e.preventDefault();
    deferred = e as typeof deferred;
  });
  const remember = (): void => {
    try {
      win.localStorage.setItem(INSTALL_KEY, "1");
    } catch {
      // A private window: it may be offered again after the next job.
    }
  };
  win.addEventListener("appinstalled", remember);
  const answered = (): boolean => {
    try {
      return win.localStorage.getItem(INSTALL_KEY) === "1";
    } catch {
      return false;
    }
  };
  const standalone = (): boolean =>
    win.matchMedia?.("(display-mode: standalone)").matches || (win.navigator as Navigator & { standalone?: boolean }).standalone === true;
  return {
    kind: () => (standalone() || answered() ? null : installKind(win.navigator.userAgent, win.navigator.maxTouchPoints ?? 0, deferred !== null)),
    async prompt() {
      const e = deferred;
      if (!e) return false;
      deferred = null;
      await e.prompt();
      return (await e.userChoice).outcome === "accepted";
    },
    done: remember,
  };
}

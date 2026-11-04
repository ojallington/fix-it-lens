// What a phone's browser can offer for keeping the app on the home screen.

import { describe, expect, it } from "vitest";
import { installKind } from "../src/install.ts";

const IPHONE_SAFARI = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1";
const IPHONE_CHROME = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/130.0 Mobile/15E148 Safari/604.1";
const IPHONE_INSTAGRAM = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Instagram 350.0";
const IPAD = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15";
const ANDROID = "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36";

describe("installKind", () => {
  it("uses the browser's own prompt whenever it has offered one", () => {
    expect(installKind(ANDROID, 5, true)).toBe("prompt");
  });

  it("has nothing to offer on Android without a prompt (an in-app browser, or already installed)", () => {
    expect(installKind(ANDROID, 5, false)).toBeNull();
  });

  it("shows Safari's steps on an iPhone or iPad in Safari", () => {
    expect(installKind(IPHONE_SAFARI, 5, false)).toBe("ios-safari");
    expect(installKind(IPAD, 5, false)).toBe("ios-safari");
  });

  it("sends other iPhone browsers and in-app browsers to Safari", () => {
    expect(installKind(IPHONE_CHROME, 5, false)).toBe("ios-other");
    expect(installKind(IPHONE_INSTAGRAM, 5, false)).toBe("ios-other");
  });

  it("offers nothing on a Mac", () => {
    expect(installKind(IPAD, 0, false)).toBeNull();
  });
});

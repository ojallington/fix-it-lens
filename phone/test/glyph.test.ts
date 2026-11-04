// @vitest-environment jsdom
// One drawing per appliance kind, from the kind in English or Dutch, inside the brand's two crop corners.

import { describe, expect, it } from "vitest";
import { glyphKind, kindGlyph } from "../src/home.ts";

describe("an appliance's drawing", () => {
  it("is chosen from its kind, in English or Dutch", () => {
    const of = (kinds: string[]) => kinds.map(glyphKind);
    expect(of(["Dishwasher", "vaatwasser"])).toEqual(["dishwasher", "dishwasher"]);
    expect(of(["Washing machine", "wasmachine", "washer-dryer", "wasdroger"])).toEqual(["washer", "washer", "washer", "washer"]);
    expect(of(["Dryer", "tumble dryer", "droger", "warmtepompdroger"])).toEqual(["dryer", "dryer", "dryer", "dryer"]);
    expect(of(["Boiler", "cv-ketel", "combi boiler"])).toEqual(["boiler", "boiler", "boiler"]);
    expect(of(["heat pump", "warmtepomp", "airco"])).toEqual(["heatpump", "heatpump", "heatpump"]);
    expect(of(["Fridge", "koelkast", "freezer", "vriezer"])).toEqual(["fridge", "fridge", "freezer", "freezer"]);
    expect(of(["Oven", "magnetron", "microwave", "kookplaat", "induction hob", "cooker hood", "afzuigkap"])).toEqual([
      "oven", "microwave", "microwave", "hob", "hob", "hood", "hood",
    ]);
    expect(of(["", "garage door"])).toEqual(["other", "other"]);
  });

  it("the washer and the dryer are drawn differently, each inside the lock-on", () => {
    const draw = (k: string) => kindGlyph(document, k).innerHTML;
    expect(draw("washer")).not.toBe(draw("dryer"));
    const g = kindGlyph(document, "dryer");
    expect(g.getAttribute("aria-hidden")).toBe("true");
    expect(g.querySelectorAll(".lock").length).toBe(1);
  });
});

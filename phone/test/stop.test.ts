// @vitest-environment jsdom
// The stop screen: a script that names a number leads with its rule's one line, the call buttons next, the script last.
import { describe, expect, it } from "vitest";
import { initial, reduce } from "../src/session.ts";
import { localInitial, mount, render, stopRows, stopScript } from "../src/ui.ts";

// As in rules/stops.json and rules/numbers.json (the phone reads those at build time; tests/l4 checks them).
const GAS =
  "If you smell gas: open windows and doors, close the main gas valve at the meter if you can reach it, don't use flames or switches, and get everyone outside. Then call 0800-9009 from outside. If there is fire or someone is unwell, call 112.";
const stopLeads = { [GAS]: "Smell gas? Go outside and call 0800-9009." };
const numbers = [
  { display: "0800-9009", tel: "08009009" },
  { display: "112", tel: "112" },
];

function show(script: string) {
  document.body.innerHTML = "<main id=app></main>";
  const els = mount(document.getElementById("app")!);
  const s = initial();
  reduce(s, { type: "safety", seq: 1, t_ms: 10, state: { state: "STOP", rule_id: "nl.gas.smell", evidence: "user", script } });
  const l = localInitial();
  l.numbers = numbers;
  l.stopLeads = stopLeads;
  render(els, s, l, "words");
  return els;
}

describe("stop layout", () => {
  it("the gas stop leads with one line, then the call buttons, then the whole script", () => {
    const els = show(GAS);
    expect(els.stopText.textContent).toBe("Smell gas? Go outside and call 0800-9009.");
    expect([...els.stopCalls.querySelectorAll("a")].map((a) => a.getAttribute("href"))).toEqual(["tel:08009009", "tel:112"]);
    expect(els.stopMore.hidden).toBe(false);
    expect(els.stopMore.textContent).toBe(GAS);
    const order = [...els.stop.children].map((c) => c.id || c.className);
    expect(order.indexOf("stop-text")).toBeLessThan(order.indexOf("stop-calls"));
    expect(order.indexOf("stop-calls")).toBeLessThan(order.indexOf("stop-more"));
  });

  it("a script with no lead stands alone, as before", () => {
    const els = show("Hands back. Close the tap on the water hose.");
    expect(els.stopText.textContent).toBe("Hands back. Close the tap on the water hose.");
    expect(els.stopMore.hidden).toBe(true);
    expect(els.stopMore.textContent).toBe("");
  });
});

describe("stop rows", () => {
  it("every script in the rules splits only at its own sentence ends, and the rows rejoin to the script", () => {
    const stops = Object.values(import.meta.glob<Record<string, string>>("../../rules/stops.json", { eager: true, import: "default" }))[0] ?? {};
    expect(Object.keys(stops).length).toBeGreaterThan(10);
    for (const script of Object.keys(stops)) {
      const rows = stopRows(stopScript(script));
      expect(rows.join(" ")).toBe(stopScript(script));
      for (const r of rows) expect(r).toMatch(/[.!?)]$/);
    }
  });

  it("only a lone leading Stop is dropped: a sentence that begins with the word is shown whole", () => {
    expect(stopScript("Stop. Close the tap.")).toBe("Close the tap.");
    expect(stopScript("Stop using it. Don't restart it.")).toBe("Stop using it. Don't restart it.");
    expect(stopScript("Stop met het apparaat. Zet het niet weer aan.")).toBe("Stop met het apparaat. Zet het niet weer aan.");
    const els = show("Stop using it. Don't restart it.");
    expect(els.stopText.textContent).toBe("Stop using it. Don't restart it.");
  });

  it("a question stays with its answer, and the screen shows the script as numbered rows in its order", () => {
    expect(stopRows("Is a seal broken? Call 0800-9009.")).toEqual(["Is a seal broken? Call 0800-9009."]);
    const els = show(GAS);
    expect([...els.stopMore.querySelectorAll("li")].map((li) => li.textContent)).toEqual([
      "If you smell gas: open windows and doors, close the main gas valve at the meter if you can reach it, don't use flames or switches, and get everyone outside.",
      "Then call 0800-9009 from outside.",
      "If there is fire or someone is unwell, call 112.",
    ]);
    expect(els.stop.querySelector("#stop-icon")!.getAttribute("aria-hidden")).toBe("true");
    expect(els.stopIcon.dataset.icon).toBe("gas");
  });
});

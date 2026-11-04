// What a pilot home thought (#feedback): the feedback form (eval/consent/feedback.md, form version 1), offered on the
// Job done screen after a live session, and only to a home the server counts in the pilot (GET /household's pilot). The
// form's own words are word for word, in the phone's language; every question may be skipped, each tick-box question
// takes one answer, and nobody is quoted unless they choose it. Written answers go as typed: never trimmed or cut.

import { button, el, errorLine } from "./people.ts";
import { langOf, type Lang } from "./words.ts";

export type Choice = "would_use" | "practical" | "unsafe";
export type Written = "would_use_why" | "hard" | "unsafe_which" | "monthly" | "friend";
export type Quote = "none" | "anonymous" | "named";

export const CHOICES: Record<Choice, readonly string[]> = {
  would_use: ["yes", "maybe", "no"],
  practical: ["yes", "partly", "no"],
  unsafe: ["no", "yes"],
};

/** The form's order: each written answer follows the question it explains. */
const ORDER: readonly (Choice | Written)[] = ["would_use", "would_use_why", "practical", "hard", "unsafe", "unsafe_which", "monthly", "friend"];

/** The server refuses a written answer longer than this (422), and never cuts one. */
export const MAX_WRITTEN = 2000;

interface FormText {
  title: string;
  questions: Record<Choice | Written, string>;
  boxes: Record<Choice, Record<string, string>>;
  quoteAsk: string;
  quoteWhy: string;
  quoteLabel: string;
  quotes: Record<Quote, string>;
  quoteName: string;
  withdraw: string;
  read: string;
  // The phone's own lines around the form.
  offer: string;
  offerLead: string;
  open: string;
  notNow: string;
  send: string;
  sending: string;
  close: string;
  thanks: string;
  thanksLead: string;
  thanksQuoted: string;
  needName: string;
  tooLong: string;
  notPilot: string;
  tooMany: string;
  gone: string;
  failed: string;
}

const MAIL = "pilot@craftycast.com";

export const FB_TEXT: Record<Lang, FormText> = {
  en: {
    title: "What did you think?",
    questions: {
      would_use: "Would you use Fix-It Lens for your appliances?",
      would_use_why: "Why?",
      practical: "Was it practical, in your own home?",
      hard: "What was hard or confusing?",
      unsafe: "Did any step feel unsafe or wrong?",
      unsafe_which: "If so: which step, and why?",
      monthly: "What would make you use it every month?",
      friend: "One sentence you would tell a friend about it:",
    },
    boxes: {
      would_use: { yes: "Yes", maybe: "Maybe", no: "No" },
      practical: { yes: "Yes", partly: "Partly", no: "No" },
      unsafe: { no: "No", yes: "Yes" },
    },
    quoteAsk: "May we quote you?",
    quoteWhy:
      'We would like to use your answers word for word on the Fix-It Lens website and in our video, critical ones too. We never change your words, and we never quote you unless you tick a box below. Whatever you choose, your answers to the tick-box questions count in the totals we publish (for example "3 of 4 would use it"), never with your name.',
    quoteLabel: "Quoting",
    quotes: { none: "Do not quote me (default)", anonymous: "Quote me without my name", named: "Quote me as:" },
    quoteName: "The name to quote you by",
    withdraw: `You can withdraw your permission to quote until the submission on Tuesday 3 November 2026; we then do not use your words. After that we take them off the website when you ask, but we cannot recall the submitted video. If you delete your home in the app, your quotes come off the website at its next update. A video already submitted cannot be recalled. Email ${MAIL}.`,
    read: "I have read this; my answers and my choice about quoting apply as described above.",
    offer: "What did you think?",
    offerLead: "About two minutes. Every question may be skipped.",
    open: "Answer a few questions",
    notNow: "Not now",
    send: "Send my answers",
    sending: "Sending…",
    close: "Close",
    thanks: "Thank you",
    thanksLead: "Your answers are in. They count in the pilot's totals, never with your name.",
    thanksQuoted: `To withdraw your permission to quote, email ${MAIL}.`,
    needName: "Write the name to quote you by, or choose another option.",
    tooLong: "Keep each written answer under 2,000 characters.",
    notPilot: "This form is only for homes in the pilot.",
    tooMany: "This home has sent all the forms it can.",
    gone: "This home is being deleted, so nothing was sent.",
    failed: "Couldn't send just now. Your answers are still here; try again.",
  },
  nl: {
    title: "Wat vond je ervan?",
    questions: {
      would_use: "Zou je Fix-It Lens gebruiken voor je apparaten?",
      would_use_why: "Waarom?",
      practical: "Was het praktisch, in je eigen huis?",
      hard: "Wat was moeilijk of verwarrend?",
      unsafe: "Voelde een stap onveilig of verkeerd?",
      unsafe_which: "Zo ja: welke stap, en waarom?",
      monthly: "Wat zou ervoor zorgen dat je het elke maand gebruikt?",
      friend: "Eén zin die je een vriend erover zou vertellen:",
    },
    boxes: {
      would_use: { yes: "Ja", maybe: "Misschien", no: "Nee" },
      practical: { yes: "Ja", partly: "Deels", no: "Nee" },
      unsafe: { no: "Nee", yes: "Ja" },
    },
    quoteAsk: "Mogen we je citeren?",
    quoteWhy:
      'Wij willen je antwoorden graag letterlijk gebruiken op de website van Fix-It Lens en in onze video, ook kritische. We veranderen nooit je woorden en citeren je nooit zonder dat je hieronder iets aankruist. Wat je ook kiest: je antwoorden op de vragen met hokjes tellen mee in de totalen die we publiceren (bijvoorbeeld "3 van de 4 zouden het gebruiken"), nooit met je naam.',
    quoteLabel: "Citeren",
    quotes: { none: "Citeer mij niet (standaard)", anonymous: "Citeer mij zonder mijn naam", named: "Citeer mij als:" },
    quoteName: "De naam waarmee we je citeren",
    withdraw: `Je kunt je toestemming om te citeren intrekken tot de inzending op dinsdag 3 november 2026; dan gebruiken we je woorden niet. Daarna halen we ze van de website zodra je dat vraagt, maar de ingezonden video kunnen we niet terughalen. Als je je huishouden in de app wist, verdwijnen je citaten bij de volgende update van de website. Een al ingezonden video kunnen we niet terughalen. Mail naar ${MAIL}.`,
    read: "Ik heb dit gelezen; mijn antwoorden en mijn keuze over citeren gelden zoals hierboven beschreven.",
    offer: "Wat vond je ervan?",
    offerLead: "Ongeveer twee minuten. Je kunt elke vraag overslaan.",
    open: "Beantwoord een paar vragen",
    notNow: "Niet nu",
    send: "Verstuur mijn antwoorden",
    sending: "Versturen…",
    close: "Sluiten",
    thanks: "Dank je wel",
    thanksLead: "Je antwoorden zijn binnen. Ze tellen mee in de totalen van de pilot, nooit met je naam.",
    thanksQuoted: `Wil je je toestemming om te citeren intrekken? Mail naar ${MAIL}.`,
    needName: "Schrijf de naam waarmee we je citeren, of kies een andere optie.",
    tooLong: "Houd elk geschreven antwoord onder de 2000 tekens.",
    notPilot: "Dit formulier is alleen voor huizen in de pilot.",
    tooMany: "Dit huis heeft alle formulieren gestuurd die het kan.",
    gone: "Dit huis wordt gewist, dus er is niets verstuurd.",
    failed: "Versturen lukt nu niet. Je antwoorden staan er nog; probeer het opnieuw.",
  },
};

export function fbText(lang: string): FormText {
  return FB_TEXT[langOf(lang)];
}

export interface FbState {
  lang: Lang;
  choices: Record<Choice, string | null>;
  written: Record<Written, string>;
  quote: Quote;
  quoteName: string;
  busy: boolean;
  error: string | null;
  /** The server took it. */
  sent: boolean;
}

/** #feedback */
export const FB_HASH = "#feedback";

export function fbInitial(lang: string): FbState {
  return {
    lang: langOf(lang),
    choices: { would_use: null, practical: null, unsafe: null },
    written: { would_use_why: "", hard: "", unsafe_which: "", monthly: "", friend: "" },
    quote: "none",
    quoteName: "",
    busy: false,
    error: null,
    sent: false,
  };
}

/** GET /household's answer: whether this home is in the pilot. Anything but true is no. */
export function isPilot(x: unknown): boolean {
  return (x as { pilot?: unknown } | null)?.pilot === true;
}

export interface FbBody {
  lang: Lang;
  answers: Record<Choice | Written, string | null>;
  quote: Quote;
  quote_name: string | null;
}

/** The body for POST /household/feedback, or what to fix first. A blank written answer is no answer; any other is
 * sent exactly as typed. A name is sent only when quoted by name. */
export function fbBody(st: FbState): { body: FbBody } | { error: string } {
  const t = FB_TEXT[st.lang];
  const written = Object.values(st.written);
  if (written.some((w) => w.length > MAX_WRITTEN) || st.quoteName.length > MAX_WRITTEN) return { error: t.tooLong };
  if (st.quote === "named" && st.quoteName.trim() === "") return { error: t.needName };
  const answers = { ...st.choices } as FbBody["answers"];
  for (const [k, v] of Object.entries(st.written) as [Written, string][]) answers[k] = v.trim() === "" ? null : v;
  return { body: { lang: st.lang, answers, quote: st.quote, quote_name: st.quote === "named" ? st.quoteName : null } };
}

/** The server's no in the form's language. */
export function fbError(status: number, lang: Lang): string {
  const t = FB_TEXT[lang];
  if (status === 403) return t.notPilot;
  if (status === 422) return t.tooLong;
  if (status === 429) return t.tooMany;
  if (status === 410) return t.gone;
  return t.failed;
}

function choiceRow(doc: Document, st: FbState, q: Choice): HTMLElement {
  const t = FB_TEXT[st.lang];
  const box = el(doc, "fieldset", "fb-q");
  box.append(el(doc, "legend", undefined, t.questions[q]));
  const row = el(doc, "div", "fb-choices");
  for (const v of CHOICES[q]) {
    // One answer at a time; tapping the chosen one again takes it back.
    const b = button(doc, "fb-pick", t.boxes[q][v]!);
    b.dataset.q = q;
    b.dataset.v = v;
    b.setAttribute("aria-pressed", String(st.choices[q] === v));
    row.append(b);
  }
  box.append(row);
  return box;
}

function writtenField(doc: Document, st: FbState, q: Written): HTMLElement {
  const lab = el(doc, "label", "report-field fb-q", FB_TEXT[st.lang].questions[q]);
  const a = el(doc, "textarea");
  a.rows = q === "friend" ? 2 : 3;
  a.value = st.written[q];
  a.dataset.fb = q;
  lab.append(a);
  return lab;
}

function quoteNodes(doc: Document, st: FbState): HTMLElement {
  const t = FB_TEXT[st.lang];
  const box = el(doc, "section", "fb-quote");
  box.append(el(doc, "h2", undefined, t.quoteAsk), el(doc, "p", "sub", t.quoteWhy));
  const set = el(doc, "fieldset");
  set.append(el(doc, "legend", undefined, t.quoteLabel));
  for (const q of ["none", "anonymous", "named"] as const) {
    const l = el(doc, "label", "check");
    const i = el(doc, "input");
    i.type = "radio";
    i.name = "fb-quote";
    i.value = q;
    i.dataset.fb = "quote";
    i.checked = st.quote === q;
    l.append(i, el(doc, "span", undefined, t.quotes[q]));
    set.append(l);
  }
  if (st.quote === "named") {
    const lab = el(doc, "label", "report-field", t.quoteName);
    const i = el(doc, "input");
    i.value = st.quoteName;
    i.autocomplete = "name";
    i.dataset.fb = "quote_name";
    lab.append(i);
    set.append(lab);
  }
  box.append(set, el(doc, "p", "sub", t.withdraw));
  return box;
}

/** The feedback screen: the form, or its thanks once sent. */
export function fbNodes(doc: Document, st: FbState): Node[] {
  const t = FB_TEXT[st.lang];
  if (st.sent) {
    const out: Node[] = [el(doc, "h1", undefined, t.thanks), el(doc, "p", undefined, t.thanksLead)];
    if (st.quote !== "none") out.push(el(doc, "p", "sub", t.thanksQuoted));
    out.push(button(doc, "fb-close", t.close, "ok big"));
    return out;
  }
  const out: Node[] = [el(doc, "h1", undefined, t.title), el(doc, "p", "sub", t.offerLead)];
  for (const q of ORDER) out.push(q in CHOICES ? choiceRow(doc, st, q as Choice) : writtenField(doc, st, q as Written));
  out.push(quoteNodes(doc, st), el(doc, "p", "fb-read", t.read));
  if (st.error) out.push(errorLine(doc, st.error));
  const go = button(doc, "fb-send", st.busy ? t.sending : t.send, "ok big");
  go.disabled = st.busy;
  out.push(go, button(doc, "fb-close", t.close, "dismiss"));
  return out;
}

/** What makes the screen differ: typing never redraws it. */
export function fbKey(st: FbState): string {
  return JSON.stringify([st.lang, st.choices, st.quote, st.busy, st.error, st.sent]);
}

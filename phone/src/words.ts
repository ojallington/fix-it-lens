// The phone's own words during a session, in Dutch. The English line is the key, so the code reads in English and a
// line with no Dutch stays English; a test holds every line the session shows to having one. The model's prose, the
// step text and the stop scripts are the server's, already in the household's language, and never pass through here.

import { NL_DEVICES } from "./words-devices.ts";
import { NL_HOME } from "./words-home.ts";
import { NL_PEOPLE } from "./words-people.ts";
import { NL_SHELL } from "./words-shell.ts";

export type Lang = "en" | "nl";

export function langOf(lang: string): Lang {
  return lang.slice(0, 2).toLowerCase() === "nl" ? "nl" : "en";
}

export const NL: Record<string, string> = {
  // The step card and the check.
  "Show page": "Toon de pagina",
  "Open the page": "Open de pagina",
  "Checking…": "Controleren…",
  Answer: "Antwoord",
  Done: "Klaar",
  "Not done yet. Have another look at the step.": "Nog niet klaar. Kijk nog eens naar de stap.",
  "I can't see it.": "Ik zie het niet.",
  "I did it — continue": "Ik heb het gedaan — verder",
  "Tap the picture to show only the camera": "Tik op het beeld om alleen de camera te zien",
  "Show the text": "Toon de tekst",
  "Next step": "Volgende stap",
  "New from the guide": "Nieuw van de gids",
  "Camera only": "Alleen camera",
  "Hands-free": "Handsfree",
  "What you need": "Wat je nodig hebt",
  "Nothing to get ready.": "Je hoeft niets klaar te leggen.",
  "Tick what you have ready.": "Vink aan wat je klaar hebt liggen.",
  "What you're going to do": "Wat je gaat doen",
  "Are you ready?": "Ben je er klaar voor?",
  "I'm ready": "Ik ben er klaar voor",
  "The camera checks it": "De camera controleert het",
  "The camera shows where": "De camera wijst aan waar",
  "No camera: you say when it's done": "Geen camera: jij zegt wanneer het klaar is",
  "Listening — tap to stop": "Ik luister — tik om te stoppen",
  "I still can't see it. If you've done it, tap I did it.": "Ik zie het nog steeds niet. Heb je het gedaan? Tik op Ik heb het gedaan.",
  "That tap was for the step before. You're on this step now.": "Die tik was voor de vorige stap. Je bent nu bij deze stap.",
  "Move closer.": "Kom dichterbij.",
  "Show it from the side.": "Laat het van opzij zien.",
  "Add more light.": "Zorg voor meer licht.",
  "View OK.": "Beeld goed.",
  Close: "Sluiten",
  "Previous page": "Vorige pagina",
  "Next page": "Volgende pagina",
  "The manual page": "De pagina uit de handleiding",

  // Pauses and holds.
  "Hands back.": "Handen weg.",
  "I can't see your hand, so I've paused.": "Ik zie je hand niet, dus ik heb gepauzeerd.",
  "The picture is late, so I've paused.": "Het beeld loopt achter, dus ik heb gepauzeerd.",
  "Hold still: finding the part again.": "Stil houden: ik zoek het onderdeel weer.",
  "Paused.": "Gepauzeerd.",
  "Prop the phone side-on, beside the part.": "Zet de telefoon op zijn kant, naast het onderdeel.",
  "Show me the plug out of the socket.": "Laat me zien dat de stekker uit het stopcontact is.",
  "Hold the camera still.": "Houd de camera stil.",
  "Move so both outlines are in view.": "Ga zo staan dat beide omlijningen in beeld zijn.",
  "Is this the gas tap?": "Is dit de gaskraan?",
  "Is the red outline on the gas tap?": "Zit de rode lijn om de gaskraan?",
  "Don't touch it.": "Raak hem niet aan.",
  "Yes, that's it": "Ja, dat is hem",
  No: "Nee",
  Yes: "Ja",
  Resume: "Verder",

  // The side view.
  "Prop the phone side-on, beside the part, so I can see your hand cross.":
    "Zet de telefoon op zijn kant, naast het onderdeel, zodat ik je hand erlangs zie gaan.",
  "Point the phone at the front first, then move it to the side.": "Richt de telefoon eerst op de voorkant en zet hem dan opzij.",
  "Move so the red part is fully in view.": "Ga zo staan dat het rode onderdeel helemaal in beeld is.",
  "Move so both parts are fully in view.": "Ga zo staan dat beide onderdelen helemaal in beeld zijn.",
  "Move further to the side so the two parts stand apart.": "Ga verder opzij staan, zodat de twee onderdelen los van elkaar staan.",
  "Hold still while I measure the view.": "Houd stil terwijl ik het beeld meet.",

  // Notices.
  "Slow connection…": "Trage verbinding…",
  "Carrying on.": "We gaan verder.",
  "Reconnected.": "Weer verbonden.",
  "Local mode: running on this network.": "Lokale modus: draait op dit netwerk.",
  "Today's limit is reached.": "De limiet voor vandaag is bereikt.",
  "I couldn't find the manual online.": "Ik kon de handleiding online niet vinden.",
  "I couldn't find the manual online. Tap Type the model to check the code, or add a link to its PDF.":
    "Ik kon de handleiding online niet vinden. Tik op Typ het model om de code na te kijken, of voeg een link naar de pdf toe.",
  "I kept the manual as plain pages, with no card.": "Ik heb de handleiding als losse pagina's bewaard, zonder kaart.",
  "The safety rules don't allow this job yet. The other jobs on this appliance are fine.":
    "De veiligheidsregels staan deze klus nog niet toe. De andere klussen aan dit apparaat kunnen wel.",
  "This part wasn't recorded.": "Dit deel is niet opgenomen.",
  "I didn't catch that. Say it again, or tap.": "Dat verstond ik niet. Zeg het nog eens, of tik.",
  "No safety rules for your country yet, so I won't guide repairs.": "Nog geen veiligheidsregels voor jouw land, dus ik begeleid geen reparaties.",
  "Offline — reconnecting…": "Offline — opnieuw verbinden…",
  "Can't open the camera. Allow camera access in your browser's site settings, or type the model instead.":
    "Kan de camera niet openen. Sta de camera toe in de site-instellingen van je browser, of typ het model.",
  "Hand tracking didn't start on this phone, so I can't watch your hands near a red outline. Keep clear of red outlines.":
    "Handvolgen is niet gestart op deze telefoon, dus ik kan je handen bij een rode omlijning niet volgen. Blijf weg bij rode omlijningen.",
  "This session couldn't start. Go back and pick the appliance again.": "Deze sessie kon niet starten. Ga terug en kies het apparaat opnieuw.",
  "You can see this home but not run a repair here. Ask an admin to let you change things.":
    "Je kunt dit huis bekijken, maar hier geen reparatie doen. Vraag een beheerder of je dingen mag veranderen.",
  "The microphone is blocked. Allow it in the browser's site settings.": "De microfoon is geblokkeerd. Sta hem toe in de site-instellingen van de browser.",
  "No microphone found.": "Geen microfoon gevonden.",
  "The microphone needs the https address.": "De microfoon werkt alleen op het https-adres.",
  "The microphone didn't start. Try again.": "De microfoon is niet gestart. Probeer het opnieuw.",
  "✋ Hand tracking off": "✋ Handvolgen uit",
  "Hand tracking is off. Tap for details.": "Handvolgen staat uit. Tik voor uitleg.",

  // The controls.
  "Hold to talk": "Houd vast om te praten",
  "Photo of the label": "Foto van de sticker",
  "Type the model": "Typ het model",
  "Send a note to a technician": "Stuur een notitie naar een monteur",
  "Note for a technician": "Notitie voor een monteur",
  "Getting the first step from the manual…": "De eerste stap uit de handleiding ophalen…",
  "Point the camera at the label with the model number (the rating plate; on a dishwasher, it's on the inside edge of the door), then tap Photo of the label.":
    "Richt de camera op het typeplaatje (de sticker met het modelnummer; bij een vaatwasser zit die op de binnenrand van de deur) en tik dan op Foto van de sticker.",
  "From the model label": "Van de sticker met het modelnummer",
  "✓ That's right": "✓ Klopt",
  "Not this one": "Niet deze",

  // The first screen on a new phone.
  "Before you start": "Voordat je begint",
  "I'm 18 or older: continue": "Ik ben 18 of ouder: verder",
  "Watch a recorded repair": "Bekijk een opgenomen klus",

  // Job done and the home screen.
  "Job done": "Klus geklaard",
  Finish: "Afronden",
  Share: "Delen",
  "Share what was done": "Deel wat er gedaan is",
  "Next time": "Volgende keer",
  you: "jij",
  "Keep it on your home screen": "Zet het op je beginscherm",
  "Add to home screen": "Zet op beginscherm",
  // iOS names its buttons Deel, Zet op beginscherm and Voeg toe in Dutch.
  "Tap <b>Share</b> in Safari. On newer iPhones, tap <b>⋯</b> first.": "Tik in Safari op <b>Deel</b>. Op nieuwere iPhones tik je eerst op <b>⋯</b>.",
  "Tap <b>Add to Home Screen</b>, then <b>Add</b>.": "Tik op <b>Zet op beginscherm</b> en dan op <b>Voeg toe</b>.",
  "To add it to your Home Screen, open this page in Safari.": "Open deze pagina in Safari om hem op je beginscherm te zetten.",
  "Copy the link": "Kopieer de link",
  "Link copied": "Link gekopieerd",
  "Added. Fix-It Lens is on your home screen.": "Toegevoegd. Fix-It Lens staat op je beginscherm.",
  "Not now": "Niet nu",
  "Next time, open Fix-It Lens from your home screen like an app. Nothing to download from a store.":
    "Open Fix-It Lens de volgende keer vanaf je beginscherm, als een app. Je hoeft niets uit een appwinkel te halen.",
  "On an iPhone, reminders reach you only when Fix-It Lens is on your home screen. Add it there, then open it from the home screen like an app. Nothing to download from a store.":
    "Op een iPhone krijg je herinneringen alleen als Fix-It Lens op je beginscherm staat. Zet het daar en open het vanaf het beginscherm, als een app. Je hoeft niets uit een appwinkel te halen.",
  // The door: shut, a link that no longer works, and the in-session cap with no reason given. The capped lines, which
  // carry a time, are built in household.ts.
  "Fix-It Lens is open by invitation only for now. You can still watch the demo replay.":
    "Fix-It Lens is voorlopig alleen op uitnodiging open. Je kunt nog wel de demo-opname bekijken.",
  "That invite was already used, or is wrong. Ask for a new link.": "Die uitnodiging is al gebruikt, of klopt niet. Vraag om een nieuwe link.",
  "Too many tries. Wait a while, then open the link again.": "Te veel pogingen. Wacht even en open de link dan opnieuw.",
  "Couldn't join just now. Check the connection and try again.": "Meedoen lukt nu niet. Controleer de verbinding en probeer het opnieuw.",
  "That link was already used or has expired. On your other phone, open Settings at the top, then Open on another phone, for a new one.":
    "Die link is al gebruikt of verlopen. Open op je andere telefoon Instellingen bovenaan en dan Open op een andere telefoon voor een nieuwe.",
  "That link doesn't work any more. Ask whoever sent it for a new one.": "Die link werkt niet meer. Vraag degene die hem stuurde om een nieuwe.",
  "This phone isn't signed in any more. On a phone that still is, open Settings at the top, then Open on another phone, for a link. Or start a new household here.":
    "Deze telefoon is niet meer aangemeld. Open op een telefoon die dat nog wel is Instellingen bovenaan en dan Open op een andere telefoon voor een link. Of begin hier een nieuw huishouden.",
  "This phone isn't signed in to a home any more. To come back, ask someone in the home for a new invite. You can still watch the demo replay.":
    "Deze telefoon is niet meer aangemeld bij een huis. Vraag iemand uit dat huis om een nieuwe uitnodiging om terug te komen. Je kunt nog wel de demo-opname bekijken.",
  "Your household and its data are deleted from this service.": "Je huishouden en de gegevens ervan zijn van deze dienst verwijderd.",
  "Tick the consent box first.": "Vink eerst het toestemmingsvakje aan.",
  "Today's limit for guided help is reached. Your reminders, your appliances and the demo replay still work.":
    "De limiet voor begeleide hulp van vandaag is bereikt. Je herinneringen, je apparaten en de demo-opname werken nog gewoon.",
};

/** Every Dutch line the phone has: the session's (above), and each part of the app's own, kept beside it by area. */
export const ALL_NL: Record<string, string> = { ...NL, ...NL_SHELL, ...NL_HOME, ...NL_DEVICES, ...NL_PEOPLE };

/** The line in the session's language: its Dutch when there is one, else the English it was asked with. */
export function tr(lang: string, en: string): string {
  return langOf(lang) === "nl" ? (ALL_NL[en] ?? en) : en;
}

/** A line with a name, a number or a date in it: the table holds it with {placeholders}, filled in after it is
 * translated, so the Dutch can put them where Dutch puts them. */
export function trf(lang: string, en: string, vars: Record<string, string | number>): string {
  return tr(lang, en).replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m));
}

/** Sets every marked element of the skeleton in the language: data-t is the element's markup (the phone's own, from
 * the skeleton, never the server's), data-t-aria its label, data-t-ph its placeholder, data-t-lang a part whose lang
 * follows. Runs once per language. */
export function translateSkeleton(root: HTMLElement, lang: string): void {
  const lg = langOf(lang);
  if (root.dataset.lang === lg) return;
  root.dataset.lang = lg;
  for (const el of root.querySelectorAll<HTMLElement>("[data-t]")) {
    el.dataset.t ||= el.innerHTML;
    el.innerHTML = tr(lg, el.dataset.t);
  }
  for (const el of root.querySelectorAll<HTMLElement>("[data-t-aria]")) {
    el.dataset.tAria ||= el.getAttribute("aria-label") ?? el.getAttribute("alt") ?? "";
    el.setAttribute(el.hasAttribute("alt") ? "alt" : "aria-label", tr(lg, el.dataset.tAria));
  }
  for (const el of root.querySelectorAll<HTMLElement>("[data-t-ph]")) {
    el.dataset.tPh ||= el.getAttribute("placeholder") ?? "";
    el.setAttribute("placeholder", tr(lg, el.dataset.tPh));
  }
  // The page is in the language now, so the screen reader's voice follows it; the parts marked carry it too.
  root.ownerDocument.documentElement.lang = lg;
  for (const el of root.querySelectorAll<HTMLElement>("[data-t-lang]")) el.lang = lg;
}

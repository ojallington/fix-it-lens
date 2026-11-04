// The app's own Dutch, with "je", for Devices, the appliance page, the manual viewer, the card check and the report form (devices.ts). The English line is the key, as in words.ts; a
// line with a name, number or date in it holds {placeholders} and is drawn with trf.

export const NL_DEVICES: Record<string, string> = {
  // Your appliances.
  "Your appliances": "Je apparaten",
  "Which appliance? Its page looks the code up in its own manual.": "Welk apparaat? Op zijn pagina zoek ik de code op in zijn eigen handleiding.",
  "No appliances yet. Add the first one above.": "Nog geen apparaten. Voeg hierboven het eerste toe.",
  "Add an appliance": "Apparaat toevoegen",
  "Point the camera at the label with the model number (the rating plate), or type the model code from it.": "Richt de camera op het typeplaatje (de sticker met het modelnummer), of typ de modelcode ervan over.",
  "Use the camera": "Gebruik de camera",
  "Type the model": "Typ het model",
  "Next: {job}, {when}": "Volgende: {job}, {when}",
  "Next: {job}": "Volgende: {job}",
  Ready: "Klaar voor gebruik",
  "Guided from the manual's pages": "Begeleid vanuit de pagina's van de handleiding",
  "The maker doesn't allow it yet": "De fabrikant staat het nog niet toe",
  "Needs its manual": "Heeft zijn handleiding nodig",
  "Couldn't set it up": "Instellen lukte niet",

  // One appliance's page.
  "I couldn't set it up.": "Ik kon het niet instellen.",
  "Add the manual": "Voeg de handleiding toe",
  "When did you last do it? Tap to say": "Wanneer deed je het voor het laatst? Tik om het te zeggen",
  "No schedule yet · the manual gives no interval": "Nog geen schema · de handleiding geeft geen interval",
  "Fixes in the manual": "Reparaties in de handleiding",
  "1 step": "1 stap",
  "{n} steps": "{n} stappen",
  "starts on p. {page}": "begint op p. {page}",
  "Left to a professional": "Voor een vakman",
  "A job in the manual": "Een klus in de handleiding",
  "Also in the manual": "Ook in de handleiding",
  "The manual also covers {heading} on {page}; we couldn't make it into steps.":
    "De handleiding behandelt ook {heading} op {page}; we konden er geen stappen van maken.",

  // The manual.
  Manual: "Handleiding",
  "Open the manual": "Open de handleiding",
  "1 page": "1 pagina",
  "{n} pages": "{n} pagina's",
  "in {language}": "in het {language}",
  English: "Engels",
  Dutch: "Nederlands",
  German: "Duits",
  French: "Frans",
  Italian: "Italiaans",
  Spanish: "Spaans",
  "Your own copy of the PDF. Only your home can open it.": "Je eigen kopie van de pdf. Alleen jouw huis kan hem openen.",
  "The maker's own PDF, on {host}. It opens on the maker's site.": "De eigen pdf van de fabrikant, op {host}. Hij opent op de site van de fabrikant.",
  "The maker's own PDF, from the maker's site.": "De eigen pdf van de fabrikant, van de site van de fabrikant.",
  "{text}: opens the maker's PDF at page {page}": "{text}: opent de pdf van de fabrikant op pagina {page}",

  // Something's wrong: a code looked up in the card.
  "Code on the display": "Code op het display",
  "Type it as the display shows it, for example E24. I look it up in this appliance's manual only.":
    "Typ hem zoals het display hem toont, bijvoorbeeld E24. Ik zoek hem alleen op in de handleiding van dit apparaat.",
  "Look it up": "Opzoeken",
  "Looking…": "Zoeken…",
  "Type the code first.": "Typ eerst de code.",
  "That's longer than any code. Type it as the display shows it.": "Dat is langer dan welke code ook. Typ hem zoals het display hem toont.",
  "This appliance has no checked card yet, so I can't look codes up.": "Dit apparaat heeft nog geen gecontroleerde kaart, dus ik kan geen codes opzoeken.",
  "Couldn't look that up. Try again.": "Opzoeken lukte niet. Probeer het opnieuw.",
  "Did you mean": "Bedoelde je",
  "The manual's fault table": "De storingstabel in de handleiding",
  "You rent: the rules say this one is for your landlord. Ask them before you start.":
    "Je huurt: volgens de regels is dit iets voor je verhuurder. Vraag het je verhuurder voordat je begint.",
  "The manual leaves this to a technician. Don't open the appliance; call one.":
    "De handleiding laat dit aan een monteur over. Maak het apparaat niet open; bel een monteur.",
  "The manual leaves this to the installer. Don't open the appliance; call them.":
    "De handleiding laat dit aan de installateur over. Maak het apparaat niet open; bel de installateur.",
  "Start the fix": "Start de reparatie",
  "Start the fix: {job}": "Start de reparatie: {job}",
  "Read from a drawing in the manual. No person has confirmed this code yet: check it against the display.":
    "Gelezen uit een tekening in de handleiding. Nog geen mens heeft deze code bevestigd: vergelijk hem met het display.",
  "In English": "In het Engels",
  "The manual, {page}": "De handleiding, {page}",
  Cause: "Oorzaak",
  "What to do": "Wat te doen",

  // How this card was checked.
  "How this card was checked": "Zo is deze kaart gecontroleerd",
  "Built from “{title}”{facts}. {source}": "Gemaakt van “{title}”{facts}. {source}",
  "Built from the manual{facts}. {source}": "Gemaakt van de handleiding{facts}. {source}",
  "Built {date}.": "Gemaakt op {date}.",
  "Built {date}, version {version}.": "Gemaakt op {date}, versie {version}.",
  "Code checked every quote against the text on its page: {passed} of {checked} matched, and the card keeps only those.":
    "Code heeft elk citaat vergeleken met de tekst op zijn pagina: bij {passed} van de {checked} klopte het, en de kaart houdt alleen die.",
  "Code checked every quote against the text on its page.": "Code heeft elk citaat vergeleken met de tekst op zijn pagina.",
  "Some quotes didn't match the text on their page, and were taken out.":
    "Sommige citaten kwamen niet overeen met de tekst op hun pagina en zijn eruit gehaald.",
  "The quotes have not been checked against the pages yet.": "De citaten zijn nog niet met de pagina's vergeleken.",
  "Every step passed the safety rules.": "Elke stap is door de veiligheidsregels gekomen.",
  "The safety rules refused part of this card.": "De veiligheidsregels hebben een deel van deze kaart geweigerd.",
  "The safety rules have not checked this card yet.": "De veiligheidsregels hebben deze kaart nog niet gecontroleerd.",
  "1 fault code was read from a drawing in the manual, not its text; no person has confirmed it yet.":
    "1 foutcode is uit een tekening in de handleiding gelezen, niet uit de tekst; nog geen mens heeft hem bevestigd.",
  "{n} fault codes were read from a drawing in the manual, not its text; no person has confirmed them yet.":
    "{n} foutcodes zijn uit een tekening in de handleiding gelezen, niet uit de tekst; nog geen mens heeft ze bevestigd.",
  "1 step is ours, not from the manual: added by our code to put the appliance back in use, and no person has accepted it yet.":
    "1 stap is van ons, niet uit de handleiding: onze code heeft hem toegevoegd om het apparaat weer in gebruik te nemen, en nog geen mens heeft hem geaccepteerd.",
  "{n} steps are ours, not from the manual: added by our code to put the appliance back in use, and no person has accepted them yet.":
    "{n} stappen zijn van ons, niet uit de handleiding: onze code heeft ze toegevoegd om het apparaat weer in gebruik te nemen, en nog geen mens heeft ze geaccepteerd.",
  "1 job left out: {reason}.": "1 klus weggelaten: {reason}.",
  "{n} jobs left out: {reason}.": "{n} klussen weggelaten: {reason}.",
  "1 procedure in the manual couldn't be made into steps; this page points at its page.":
    "1 procedure uit de handleiding kon niet in stappen worden omgezet; hier staat naar welke pagina.",
  "{n} procedures in the manual couldn't be made into steps; this page points at their pages.":
    "{n} procedures uit de handleiding konden niet in stappen worden omgezet; hier staat naar welke pagina's.",
  "No jobs left out.": "Geen klussen weggelaten.",
  "A person read this card against the manual, {date}.": "Een mens heeft deze kaart met de handleiding vergeleken, op {date}.",
  "No person has reviewed this card yet; the checks above are code.": "Nog geen mens heeft deze kaart nagekeken; de controles hierboven zijn code.",
  "Shared with every home that has this model: {n} homes use it.": "Gedeeld met elk huis met dit model: {n} huizen gebruiken hem.",
  "Shared with every home that has this model. Only your home uses it so far.":
    "Gedeeld met elk huis met dit model. Tot nu toe gebruikt alleen jouw huis hem.",
  "Kept to your home.": "Blijft bij je huis.",
  "Kept to your home: it was built from your own copy of the manual.": "Blijft bij je huis: hij is gemaakt van je eigen kopie van de handleiding.",
  "Kept to your home: the manual doesn't list the models it covers, so it can't be matched to the same model in another home.":
    "Blijft bij je huis: de handleiding noemt niet voor welke modellen ze is, dus hij kan niet aan hetzelfde model in een ander huis gekoppeld worden.",
  "Kept to your home: your model isn't in the manual's own list of models.": "Blijft bij je huis: je model staat niet in de modellenlijst van de handleiding.",
  "Kept to your home: the maker's terms don't allow sharing it.": "Blijft bij je huis: de voorwaarden van de fabrikant staan delen niet toe.",
  "Kept to your home: this copy of the manual differs from the one other homes use, so it waits for review.":
    "Blijft bij je huis: deze kopie van de handleiding verschilt van die van andere huizen, dus hij wacht op controle.",
  "Kept to your home until a person confirms the fault codes read from the manual's drawings.":
    "Blijft bij je huis tot een mens de foutcodes bevestigt die uit de tekeningen in de handleiding zijn gelezen.",
  "Kept to your home until a person accepts the steps we added to it.": "Blijft bij je huis tot een mens de stappen accepteert die we eraan hebben toegevoegd.",
  "No open reports.": "Geen openstaande meldingen.",
  "1 open report. A report never changes the card by itself.": "1 openstaande melding. Een melding verandert de kaart nooit vanzelf.",
  "1 open report, {safety} about safety. A report never changes the card by itself.":
    "1 openstaande melding, {safety} over veiligheid. Een melding verandert de kaart nooit vanzelf.",
  "{n} open reports. A report never changes the card by itself.": "{n} openstaande meldingen. Een melding verandert de kaart nooit vanzelf.",
  "{n} open reports, {safety} about safety. A report never changes the card by itself.":
    "{n} openstaande meldingen, {safety} over veiligheid. Een melding verandert de kaart nooit vanzelf.",
  "The rest of the check record isn't available for this card.": "De rest van het controleverslag is voor deze kaart niet beschikbaar.",
  "A home has reported something unsafe on this card. It is waiting for a person to check it against the manual.":
    "Een huis heeft iets onveiligs op deze kaart gemeld. Hij wacht tot een mens hem met de handleiding vergelijkt.",
  "{n} homes have reported something unsafe on this card. It is waiting for a person to check it against the manual.":
    "{n} huizen hebben iets onveiligs op deze kaart gemeld. Hij wacht tot een mens hem met de handleiding vergelijkt.",

  // Report a problem.
  "Report a problem": "Meld een probleem",
  "A report never changes the card. A person checks it against the manual first.":
    "Een melding verandert de kaart nooit. Een mens vergelijkt hem eerst met de handleiding.",
  "A step is wrong": "Een stap klopt niet",
  "A page number is wrong": "Een paginanummer klopt niet",
  "This isn't my appliance": "Dit is niet mijn apparaat",
  "Something is unsafe": "Er is iets onveilig",
  "Something else": "Iets anders",
  "Which step": "Welke stap",
  "Which step (if it's one step)": "Welke stap (als het om één stap gaat)",
  "Pick a step": "Kies een stap",
  "The card as a whole": "De hele kaart",
  "our step": "onze stap",
  "The right page in the manual": "De juiste pagina in de handleiding",
  "What's wrong": "Wat er mis is",
  "A note (optional)": "Een notitie (niet verplicht)",
  "Your note stays with your home and the reviewer. It is never sent to an AI model.":
    "Je notitie blijft bij je huis en de controleur. Hij gaat nooit naar een AI-model.",
  "Stop using this card": "Stop met deze kaart",
  "Send the report": "Verstuur de melding",
  Cancel: "Annuleren",
  "Sent. Thank you. A person checks it against the manual; the card doesn't change until then.":
    "Verstuurd. Dank je. Een mens vergelijkt het met de handleiding; tot dan verandert de kaart niet.",
  "Done. Your home no longer uses that card; other homes keep theirs.": "Klaar. Je huis gebruikt die kaart niet meer; andere huizen houden de hunne.",
  "Your home stops using this card, and its jobs leave your list. Other homes keep it. Then you can add your own manual.":
    "Je huis stopt met deze kaart, en de klussen ervan verdwijnen uit je lijst. Andere huizen houden hem. Daarna kun je je eigen handleiding toevoegen.",
  "Pick what's wrong.": "Kies wat er mis is.",
  "Pick the step that's wrong.": "Kies de stap die niet klopt.",
  "Type the page the manual should show.": "Typ de pagina die de handleiding zou moeten tonen.",
  "Type the page the manual should show, 1 to {pages}.": "Typ de pagina die de handleiding zou moeten tonen, 1 tot en met {pages}.",
  "Say what's wrong in a few words.": "Zeg in een paar woorden wat er mis is.",
  "Your home already has 20 reports waiting for review. Try again once some are answered.":
    "Je huis heeft al 20 meldingen die op controle wachten. Probeer het opnieuw als er een paar beantwoord zijn.",
  "This phone can only view this home, so it can't send that.": "Deze telefoon kan in dit huis alleen kijken, dus hij kan dat niet versturen.",
  "That appliance is no longer in this home.": "Dat apparaat staat niet meer in dit huis.",
  "There's no card for this appliance yet, so nothing to report.": "Er is nog geen kaart voor dit apparaat, dus er is niets te melden.",
  "That can't be sent. Pick the step again, or check the page number.": "Dat kan niet worden verstuurd. Kies de stap opnieuw, of controleer het paginanummer.",
  "Couldn't send that. Check the connection and try again.": "Versturen lukte niet. Controleer de verbinding en probeer het opnieuw.",
  "Remove this appliance": "Dit apparaat verwijderen",
  "Remove {name} from this home? Its jobs and reminders go; what you did with it stays in the history.": "{name} uit dit huis verwijderen? De klussen en herinneringen verdwijnen; wat je ermee deed blijft in de geschiedenis.",
  "Remove": "Verwijderen",
  "Removing…": "Verwijderen…",
  "Keep it": "Houden",
  "Couldn't remove it. Check the connection and try again.": "Verwijderen lukte niet. Controleer de verbinding en probeer het opnieuw.",
};

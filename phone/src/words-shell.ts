// The app's own Dutch, with "je", for the frame around the screens: the home screen's skeleton, the add flow, a
// reminder's landing, the plate card's labels, Settings and About (ui.ts, app.ts, household.ts, appliance.ts, plate.ts).
// The English line is the key, as in words.ts; a line with a name, number or date in it holds {placeholders} and is
// drawn with trf. A line that names a button names it as the Dutch screen shows it.

export const NL_SHELL: Record<string, string> = {
  // The header and the replay.
  "Recorded demo": "Opgenomen demo",
  "Demo": "Demo",
  "End the job": "Klus beëindigen",
  "End": "Afsluiten",
  "Tick the box to continue": "Vink het vakje aan om verder te gaan",
  "From the manual": "Uit de handleiding",
  "The manual's own words": "De woorden van de handleiding",
  "TEST ZONE": "TESTZONE",
  "Replay not found": "Opname niet gevonden",
  "Go to the start": "Naar het begin",
  "This replay isn't available. The link may be mistyped or the recording removed.":
    "Deze opname is er niet. De link is misschien verkeerd getypt, of de opname is weggehaald.",
  "Save the timings": "Bewaar de tijden",

  // A STOP's way on.
  "Who to call: the maker's service, or a qualified technician.": "Wie je belt: de service van de fabrikant, of een erkende monteur.",
  "Back to my jobs": "Terug naar mijn klussen",
  "Watch again": "Nog een keer bekijken",

  // The first run.
  "Reads your appliance's own manual": "Leest de handleiding van je eigen apparaat",
  "Shows you the part, one step at a time": "Wijst je het onderdeel aan, stap voor stap",
  "Stops at gas, mains and anything risky, and says who to call": "Stopt bij gas, elektra en alles wat riskant is, en zegt wie je kunt bellen",
  "The full terms": "De volledige voorwaarden",

  // A reminder's landing.
  Reminder: "Herinnering",
  "Your reminder": "Je herinnering",
  Start: "Beginnen",
  "About {n} min": "Ongeveer {n} min",
  "Remind me tomorrow": "Herinner me morgen",
  "Schedule and history": "Schema en geschiedenis",
  "I'll remind you again tomorrow.": "Ik herinner je er morgen weer aan.",
  "Couldn't move the reminder. It will come again as planned.": "De herinnering verzetten lukte niet. Hij komt gewoon zoals gepland.",

  // The home screen.
  "Point your phone at an appliance. Fix‑It Lens finds its manual, shows you the part, checks each step, and stops you before anything risky.":
    "Richt je telefoon op een apparaat. Fix‑It Lens zoekt de handleiding, wijst je het onderdeel aan, controleert elke stap en houdt je tegen voordat je iets riskants doet.",
  "Point at an appliance": "Richt op een apparaat",
  "or type the model code": "of typ de modelcode",
  "Type the model code instead": "Typ liever de modelcode",
  "Point it at an appliance": "Richt je telefoon op een apparaat",
  "How a repair goes": "Zo gaat een klus",
  "Each step, with its page in your manual": "Elke stap, met de pagina in je handleiding",
  "The part, outlined on your camera": "Het onderdeel, omlijnd op je camera",
  "Checked before you go on": "Gecontroleerd voordat je verdergaat",
  "Add another appliance": "Nog een apparaat toevoegen",
  "Your appliances and manuals": "Je apparaten en handleidingen",
  "Your appliances": "Je apparaten",
  you: "jij",
  "Getting your appliances…": "Je apparaten ophalen…",
  "A PDF shared with Fix-It Lens": "Een pdf gedeeld met Fix-It Lens",
  "Which appliance is this manual for?": "Bij welk apparaat hoort deze handleiding?",
  "Add the appliance it is for, and I'll read it.": "Voeg het apparaat toe waar hij bij hoort, dan lees ik hem.",
  "A new appliance": "Een nieuw apparaat",

  // Adding an appliance.
  "Add this appliance": "Voeg dit apparaat toe",
  "The {name}'s manual": "Handleiding: {name}",
  "Model code": "Modelcode",
  "e.g. SMV4HAX40N": "bijv. SMV4HAX40N",
  Brand: "Merk",
  "as on the model label": "zoals op de sticker",
  "Name it (optional)": "Geef het een naam (niet verplicht)",
  dishwasher: "vaatwasser",
  Add: "Toevoegen",
  "Adding…": "Toevoegen…",
  "Try again": "Opnieuw proberen",
  "Sending the PDF…": "De pdf versturen…",
  Progress: "Voortgang",
  "The manual's first page": "De eerste pagina van de handleiding",
  "Jobs ready so far": "Klussen die al klaarstaan",
  "Manual link": "Link naar de handleiding",
  "Use this link": "Gebruik deze link",
  "Add the PDF": "Voeg de pdf toe",
  "Open the maker's page": "Open de pagina van de fabrikant",
  "Reading the model label…": "De sticker met het modelnummer lezen…",
  "Finding the manual…": "De handleiding zoeken…",
  "Read the model label": "De sticker met het modelnummer lezen",
  "Find the manual": "De handleiding zoeken",
  "Download it": "Downloaden",
  "Read it and check each quote": "Lezen en elk citaat controleren",
  "Downloading the manual…": "De handleiding downloaden…",
  "Reading the manual…": "De handleiding lezen…",
  "Nearly ready…": "Bijna klaar…",
  "Ready: {name}.": "Klaar: {name}.",
  "Ready: {name}. I'll guide from the manual's pages.": "Klaar: {name}. Ik begeleid je met de pagina's uit de handleiding.",
  "Less than a minute left": "Nog minder dan een minuut",
  "About {n} min left": "Nog ongeveer {n} min",
  "Manual found: {pages}.": "Handleiding gevonden: {pages}.",
  "Manual found: {pages}, in {language}.": "Handleiding gevonden: {pages}, in {language}.",
  "Manual found: {pages}, not in your language.": "Handleiding gevonden: {pages}, niet in jouw taal.",
  "Quotes checked against the pages: {passed} of {checked} found.": "Citaten nagekeken in de pagina's: {passed} van {checked} gevonden.",
  "page {n}": "pagina {n}",
  "about {n} min": "ongeveer {n} min",
  "{jobs} left out: {why}.": "{jobs} weggelaten: {why}.",
  "it works on the gas supply": "het gaat om de gastoevoer",
  "it means opening the mains wiring": "dan moet de elektrische bedrading open",
  "it's a job for a professional": "het is een klus voor een vakman",
  "the safety rules don't allow it": "de veiligheidsregels staan het niet toe",
  "That name is already used. Pick another.": "Die naam is al in gebruik. Kies een andere.",
  "Which brand is it? Type the brand from the model label, then tap Add.": "Welk merk is het? Typ het merk van de sticker met het modelnummer en tik dan op Toevoegen.",
  "That didn't work. Check the model code or the link.": "Dat lukte niet. Controleer de modelcode of de link.",
  "Open your invite link on this phone to add appliances.": "Open je uitnodigingslink op deze telefoon om apparaten toe te voegen.",
  "Couldn't add it just now. Try again.": "Toevoegen lukt nu niet. Probeer het opnieuw.",
  "That file is over 50 MB, too big for me to read. Paste a link to the manual under Manual link instead.":
    "Dat bestand is groter dan 50 MB, te groot voor mij om te lezen. Plak in plaats daarvan een link naar de handleiding onder Link naar de handleiding.",
  "That file isn't a PDF manual. Tap Add the PDF and pick the manual, or paste a link to it under Manual link.":
    "Dat bestand is geen pdf-handleiding. Tik op Voeg de pdf toe en kies de handleiding, of plak een link ernaar onder Link naar de handleiding.",
  "You can see this home but not change it, so you can't add a manual.":
    "Je kunt dit huis bekijken maar niet veranderen, dus je kunt geen handleiding toevoegen.",
  "That appliance is no longer in this home. Close this and add it again.": "Dat apparaat staat niet meer in dit huis. Sluit dit en voeg het opnieuw toe.",
  "Couldn't send the PDF. Check the connection and try again.": "De pdf versturen lukte niet. Controleer de verbinding en probeer het opnieuw.",

  // The plate card's labels, and a part named in a hold.
  Model: "Model",
  "E-Nr (product number)": "E-Nr (productnummer)",
  "FD (production date)": "FD (productiedatum)",
  "Hold still: finding the {part} again.": "Stil houden: ik zoek het onderdeel weer ({part}).",

  // The manual page.
  "Couldn't load that page. Check the connection and try again.": "Die pagina laden lukt niet. Controleer de verbinding en probeer het opnieuw.",

  // The tabs, Settings and About.
  Home: "Start",
  Appliances: "Apparaten",
  Household: "Huishouden",
  Back: "Terug",
  Settings: "Instellingen",
  About: "Over",
  "Fix-It Lens is a prototype.": "Fix-It Lens is een prototype.",
  "Not affiliated with Bosch, Samsung, the boiler makers or Meta.": "Niet verbonden aan Bosch, Samsung, de ketelfabrikanten of Meta.",
  Language: "Taal",
  "People and alerts": "Mensen en meldingen",
  Connections: "Koppelingen",
  "Open on another phone": "Open op een andere telefoon",
  "Make a link, then open it on the other phone. It works once, within 15 minutes, and opens this household there too.":
    "Maak een link en open hem op de andere telefoon. Hij werkt één keer, binnen 15 minuten, en opent dit huishouden daar ook.",
  "Make a link": "Maak een link",
  "Make a new link": "Maak een nieuwe link",
  "Copied.": "Gekopieerd.",
  "Works once, until {time}.": "Werkt één keer, tot {time}.",
  "Couldn't make a link just now. Check the connection and try again.": "Een link maken lukt nu niet. Controleer de verbinding en probeer het opnieuw.",
  "Only the household's own phones can make this link. Ask whoever runs your household.":
    "Alleen de eigen telefoons van het huishouden kunnen deze link maken. Vraag het aan wie je huishouden beheert.",
  "Delete my household": "Verwijder mijn huishouden",
  "Tap again to delete everything": "Tik nog eens om alles te verwijderen",
};

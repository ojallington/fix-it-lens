// The app's own Dutch, with "je", for People, joining, alerts and Connections (people.ts, connections.ts). The English line is the key, as in words.ts; a
// line with a name, number or date in it holds {placeholders} and is drawn with trf.

export const NL_PEOPLE: Record<string, string> = {
  // Roles, and what each may do.
  Admin: "Beheerder",
  Member: "Lid",
  "Can only view": "Kan alleen kijken",
  "Everything, including people, invites and deleting the home.": "Alles, ook mensen, uitnodigingen en het huis verwijderen.",
  "Sees everything, adds appliances, runs repairs and changes the schedule.": "Ziet alles, voegt apparaten toe, doet reparaties en verandert het schema.",
  "Sees the home's appliances and jobs, and sets their own alerts. Changes nothing.":
    "Ziet de apparaten en klussen van het huis en stelt eigen meldingen in. Verandert niets.",
  Role: "Rol",

  // PEOPLE_TEXT: refusals and notes, stored in English and drawn in the language. Its viewerSession is in words.ts.
  "Only an admin can do that.": "Alleen een beheerder kan dat.",
  "A home keeps at least one admin. Make someone else an admin first.": "Een huis houdt minstens één beheerder. Maak eerst iemand anders beheerder.",
  "Someone in this home already has that name. Pick another.": "Iemand in dit huis heeft die naam al. Kies een andere.",
  "Give a name of 1 to 40 characters.": "Geef een naam van 1 tot 40 tekens.",
  "That email address doesn't look right.": "Dat e-mailadres lijkt niet te kloppen.",
  "Quiet hours need a start and an end that differ.": "Stille uren hebben een begin en een eind nodig die verschillen.",
  "Choose at least one appliance, or pick every appliance.": "Kies minstens één apparaat, of kies alle apparaten.",
  "One of those appliances isn't in this home any more. Choose again.": "Een van die apparaten staat niet meer in dit huis. Kies opnieuw.",
  "Getting the people in this home…": "De mensen in dit huis ophalen…",
  "Getting your connections…": "Je koppelingen ophalen…",
  "Saved.": "Opgeslagen.",
  "Link copied.": "Link gekopieerd.",
  "You can see this home but not change it. Ask an admin if you need to.": "Je kunt dit huis bekijken, maar niet veranderen. Vraag een beheerder als dat nodig is.",

  // The People and alerts screen.
  "People and alerts": "Mensen en meldingen",
  "{role} · you": "{role} · jij",
  Name: "Naam",
  Save: "Opslaan",
  Cancel: "Annuleren",
  "Leave this home? This phone stops opening it, and your jobs go back to nobody.":
    "Dit huis verlaten? Deze telefoon opent het dan niet meer, en je klussen zijn van niemand meer.",
  "Remove {name}? Their phones stop opening this home, and their jobs go back to nobody.":
    "{name} verwijderen? Hun telefoons openen dit huis dan niet meer, en hun klussen zijn van niemand meer.",
  Leave: "Verlaten",
  Remove: "Verwijderen",
  Keep: "Houden",
  Rename: "Naam wijzigen",
  "More for {name}": "Meer voor {name}",
  "Leave this home": "Dit huis verlaten",

  // Inviting.
  "Invite someone": "Iemand uitnodigen",
  "Just you so far. Invite someone to share the jobs: each person gets their own reminders.": "Nog alleen jij. Nodig iemand uit om de klussen te delen: ieder krijgt eigen herinneringen.",
  "until the end of today": "tot het eind van vandaag",
  "until {date}": "tot {date}",
  "A link for one admin, {until}. It works once. Send it only to them.": "Een link voor één beheerder, {until}. Hij werkt één keer. Stuur hem alleen naar die persoon.",
  "A link for one member, {until}. It works once. Send it only to them.": "Een link voor één lid, {until}. Hij werkt één keer. Stuur hem alleen naar die persoon.",
  "A link for one member who can only view, {until}. It works once. Send it only to them.":
    "Een link voor één lid dat alleen kan kijken, {until}. Hij werkt één keer. Stuur hem alleen naar die persoon.",
  "Share the link": "Deel de link",
  "Email it to {email}": "Mail hem naar {email}",
  "Invite someone else": "Nodig iemand anders uit",
  "They join as": "Komt erbij als",
  "Their email (optional: your mail app sends it)": "Hun e-mailadres (mag leeg blijven: je mail-app verstuurt het)",
  "Make the invite link": "Maak de uitnodigingslink",
  // The share sheet and the mail: the person's own apps send them, in the person's language.
  "our home": "ons huis",
  "Join {home} on Fix-It Lens": "Doe mee in {home} op Fix-It Lens",
  "Open this link on your phone to join {home} on Fix-It Lens:": "Open deze link op je telefoon om mee te doen in {home} op Fix-It Lens:",
  "It works once, until {date}.": "Hij werkt één keer, tot {date}.",

  // Your alerts.
  "Your alerts": "Je meldingen",
  "Only for you, on your own phone. A job given to someone reminds only them; a job given to nobody reminds everyone who follows its appliance.":
    "Alleen voor jou, op je eigen telefoon. Een klus die aan iemand is gegeven, herinnert alleen die persoon; een klus van niemand herinnert iedereen die het apparaat volgt.",
  How: "Hoe",
  "Notifications on this phone": "Meldingen op deze telefoon",
  "In your calendar": "In je agenda",
  "Through Home Assistant and other hubs": "Via Home Assistant en andere hubs",
  For: "Voor",
  "Every appliance": "Alle apparaten",
  "Only the ones you choose": "Alleen de apparaten die je kiest",
  "No appliances in this home yet.": "Nog geen apparaten in dit huis.",
  When: "Wanneer",
  "Quiet hours": "Stille uren",
  From: "Van",
  To: "Tot",
  "In your home's time. A reminder due then waits until they end. A STOP is never held back.":
    "In de tijd van je huis. Een herinnering die dan valt, wacht tot ze voorbij zijn. Een STOP wordt nooit tegengehouden.",
  "Save your alerts": "Sla je meldingen op",

  // Joining, and naming the first person of a new home (JOIN_TEXT; its failed line is in words.ts).
  "Join a home": "Meedoen in een huis",
  "Someone invited you to their home on Fix-It Lens. Give the name the others will see.":
    "Je bent in Fix-It Lens uitgenodigd voor iemands huis. Geef de naam die de anderen zien.",
  "This invite doesn't work any more: it was used, or it ran out. Ask for a new one.":
    "Deze uitnodiging werkt niet meer: hij is gebruikt of verlopen. Vraag om een nieuwe.",
  "Your name": "Je naam",
  "Your name, as the people you invite will see it": "Je naam, zoals de mensen die je uitnodigt hem zien",
  "This phone already opens a home. Joining moves it to this one; the other home stays as it is for everyone in it.":
    "Deze telefoon opent al een huis. Meedoen zet hem over naar dit huis; het andere huis blijft zoals het is voor iedereen die erin zit.",
  Continue: "Verder",
  Join: "Meedoen",

  // Connections (CONN_TEXT, KIND_TEXT and SCOPE_TEXT too). Home Assistant and MCP are names.
  "Couldn't do that. Try again.": "Dat lukte niet. Probeer het opnieuw.",
  "Give it a name of 1 to 60 characters.": "Geef het een naam van 1 tot 60 tekens.",
  "Copied.": "Gekopieerd.",
  "AI agent": "AI-agent",
  Calendar: "Agenda",
  "Can only look": "Kan alleen kijken",
  "Can look and change the schedule": "Kan kijken en het schema veranderen",
  Connections: "Koppelingen",
  "Your calendar": "Je agenda",
  "Subscribe to this address in your calendar app to see your upkeep jobs there. Anyone with it can read them, so keep it to yourself.":
    "Abonneer je in je agenda-app op dit adres om je onderhoudsklussen daar te zien. Iedereen met dit adres kan ze lezen, dus houd het voor jezelf.",
  Address: "Adres",
  "Copy the address": "Kopieer het adres",
  "Show the address": "Toon het adres",
  Key: "Sleutel",
  "Copy the key": "Kopieer de sleutel",
  "The key for {label}": "De sleutel voor {label}",
  // Home Assistant's own Dutch names for these menus.
  "In Home Assistant, go to Settings, Devices & services, Add integration, Fix-It Lens, and paste these two.":
    "Ga in Home Assistant naar Instellingen, Apparaten & diensten, Integratie toevoegen, Fix-It Lens, en plak deze twee.",
  "Put these in your agent's MCP settings. Keep the key in a file only you can read, not in a chat or a command line.":
    "Zet deze in de MCP-instellingen van je agent. Bewaar de sleutel in een bestand dat alleen jij kunt lezen, niet in een chat of op een opdrachtregel.",
  "This is the only time the key is shown. If it's lost, remove this connection and add a new one.":
    "Dit is de enige keer dat de sleutel te zien is. Raak je hem kwijt, verwijder dan deze koppeling en voeg een nieuwe toe.",
  "Add a connection": "Voeg een koppeling toe",
  "An AI agent (MCP)": "Een AI-agent (MCP)",
  "It may": "Het mag",
  "Only look": "Alleen kijken",
  "What is due, and when each job was last done.": "Wat er aan de beurt is, en wanneer elke klus voor het laatst is gedaan.",
  "Look and change the schedule": "Kijken en het schema veranderen",
  "Mark jobs done, snooze them and set their dates. Every change shows under its name and can be undone.":
    "Klussen afvinken, uitstellen en hun datum zetten. Elke wijziging staat onder zijn naam en kan ongedaan worden gemaakt.",
  "Its name in this home": "Naam in dit huis",
  "Make the key": "Maak de sleutel",
  "last used {date}": "laatst gebruikt {date}",
  "not used yet": "nog niet gebruikt",
  "added {date}": "toegevoegd {date}",
  "Remove {label}? It stops working at once.": "{label} verwijderen? Het werkt dan meteen niet meer.",
  "Home Assistant and AI agents": "Home Assistant en AI-agents",
  "Each gets its own key. None of them can run a repair, clear a stop or change what a manual says.":
    "Elk krijgt een eigen sleutel. Geen ervan kan een reparatie doen, een stop opheffen of veranderen wat een handleiding zegt.",
  "Nothing is connected yet.": "Er is nog niets gekoppeld.",
  "Nothing to set up here on this home.": "Hier valt voor dit huis niets in te stellen.",
};

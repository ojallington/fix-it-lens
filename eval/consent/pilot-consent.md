# Toestemming: proef met Fix-It Lens / Consent: Fix-It Lens pilot

Fix-It Lens · pilot · form version 1 (27 Oct 2026)

One form per household, signed before the invite code is used. Keep the signed copy in `FIXIT_PRIVATE/consent/`;
it never enters the repository.

---

## Nederlands

**Wat de proef is.** U gebruikt de app Fix-It Lens op uw eigen telefoon, met hoogstens twee apparaten, van vrijdag 30
oktober 2026 tot en met 9 november 2026. De proef loopt in hoogstens vier huishoudens. U voegt een apparaat toe met een
foto van het typeplaatje of door het typenummer in te typen. De app vraagt één keer per onderhoudstaak wanneer u die het
laatst deed, en herinnert u wanneer de taak volgens de handleiding van uw apparaat weer moet. Wilt u de taak doen, dan
kijkt de app via de camera mee en controleert elke stap.

**Wat er naar buiten gaat.** Terwijl u een taak doet, gaan camerabeelden en korte clips naar de AI-dienst van Meta (de
Meta Model API, Standard tier), om het apparaat te zien en elke stap te controleren. Meta traint zijn AI niet met uw
beelden. Wij vragen de dienst geen kopie van zijn antwoorden te bewaren, maar Meta mag beelden wel bewaren voor zover
nodig om de dienst te leveren, om aan de wet te voldoen en om op misbruik te controleren; beelden die als misbruik
gemarkeerd worden, tot 2 jaar. Richt de camera alleen op het apparaat en uw handen.

**Wat wij bewaren.** Uw apparaten (merk en typenummer; het serienummer leggen wij niet vast), de datums van uw
onderhoudstaken, de herinneringen en wat ermee gebeurde (verstuurd, geopend, begonnen, klaar en gecontroleerd,
weggeklikt), en de opnames van uw sessies. Op de foto van het typeplaatje kan het serienummer staan; die foto bewaren
wij privé en wissen wij samen met de rest. Dit staat op de computer van Fix-It Lens (die van de maker), niet in de
openbare repository en niet bij derden. Er is een uitgavenlimiet per huishouden; u betaalt niets.

**Wat wij delen.** De app maakt uit de handleiding van de fabrikant een reparatiekaart voor uw typenummer. Die kaart
delen wij met andere huishoudens die hetzelfde typenummer hebben; er staat niets over uw huishouden op. Een handleiding
die u zelf uploadt, blijft privé.

**Wat wij publiceren.** Alleen **totalen** over alle deelnemers samen: het aantal huishoudens, apparaten, herinneringen
en taken die klaar en gecontroleerd zijn, de mediane tijd van herinnering tot klaar, en de merken. Nooit gegevens van
één huishouden, geen namen, adressen of beelden uit uw huis.

**Stoppen en wissen.** U kunt op elk moment stoppen. Op uw verzoek wissen we al uw gegevens en opnames binnen 7 dagen.
Totalen die al gepubliceerd zijn, worden niet teruggerekend. Na de proef worden uw gegevens gewist op 30 november 2026,
tenzij u vraagt om de app te blijven gebruiken.

**Veiligheid.** De app is gemaakt om te stoppen bij het gas- en netspanningswerk dat zijn regels herkennen, maar hij kan
dingen missen, maakt een reparatie niet veilig en vervangt geen vakman. Doe geen stap waar u
zich niet prettig bij voelt.

Ik heb dit gelezen en doe mee aan de proef zoals hierboven beschreven.

Naam: ______________________ Handtekening: ______________________ Datum: ____________

Uitnodigingscode: ____________

---

## English

**What the pilot is.** You use the Fix-It Lens app on your own phone, with at most two appliances, from Friday 30
October 2026 to 9 November 2026. The pilot runs in at most four households. You add an appliance by photographing its
type plate or typing its model number. The app asks once per upkeep job when you last did it, and reminds you when your
appliance's manual says the job is due again. If you do the job, the app watches through the camera and checks each
step.

**What leaves your phone.** While you do a job, camera pictures and short clips go to Meta's AI service (the Meta Model
API, Standard tier) to see the appliance and check each step. Meta does not train its AI on your pictures. We ask the
service not to keep a copy of its answers, but Meta may still keep pictures as needed to run the service, to obey the
law and to check for misuse; pictures flagged as misuse can be kept for up to 2 years. Point the camera at the appliance
and your hands only.

**What we keep.** Your appliances (brand and model number; we do not record the serial number), the dates of your upkeep
jobs, the reminders and what happened to them (sent, opened, started, done and checked, dismissed), and the recordings
of your sessions. The photo of the type plate may show the serial number; we keep that photo privately and delete it
with the rest. These are kept on the Fix-It Lens computer (the maker's own), not in the public repository and not with
third parties. Each household has a spending cap; you pay nothing.

**What we share.** The app makes a repair card for your model number from the maker's manual. We share that card with
other households that have the same model number; nothing about your household is on it. A manual you upload yourself
stays private.

**What we publish.** Only **totals** across all participants: the number of households, appliances, reminders and jobs
done and checked, the median time from reminder to done, and the brands. Never one household's data, no names, addresses
or images from your home.

**Stopping and deletion.** You can stop at any time. On request we delete all your data and recordings within 7 days.
Totals already published are not recalculated. After the pilot your data is deleted on 30 November 2026, unless you ask
to keep using the app.

**Safety.** The app is built to stop at the gas and mains work its rules catch, but it can miss things, it does not
make a repair safe, and it does not replace a professional. Do not do
any step you are not comfortable with.

I have read this and take part in the pilot as described above.

Name: ______________________ Signature: ______________________ Date: ____________

Invite code: ____________

---

Contact: pilot@craftycast.com

---

## Sources for "What leaves your phone" (read 27 Oct 2026; not part of the signed form)

- Not used for training. Meta Model API Terms of Service (last updated 9 Oct 2026), §5.1 Processor Terms,
  https://dev.meta.ai/legal/terms-of-service: "Meta … will not use Content submitted in a request to the Standard
  Services to train Meta Models." Also https://dev.meta.ai/docs/pricing-rate-limits#standard-mode: "In Standard mode,
  you pay standard prices and your content is not used to train Meta models." We call `muse-spark-1.3` and the other
  Standard models only; calls/ refuses any `-contributor` model ID.
- "We ask the service not to keep a copy of its answers" is `store: false`. https://dev.meta.ai/docs/protocols/responses: "Responses are stored when store: true (the default)."
  and "For stateless replay, pair it with store: false so the server keeps no copy of the conversation". The docs say
  this about the stored response; they do not say the content is never kept.
- What Meta may still keep. Terms of Service §4.4 Retention: "Meta will retain your Content and Usage Data as needed:
  (i) to provide the Services under these Terms; (ii) to the extent necessary for compliance with applicable laws;
  (iii) in connection with the exercise of Meta's rights to use your Content and Usage Data under these Terms; (iv)
  when our systems flag data for a potential policy violation; and (v) for security, safety, abuse, and policy review
  and improvements." §4.1: "You instruct Meta to retain and process Content as necessary to detect, prevent, and
  respond to violations of our Model API AUP … or these Terms and to develop, evaluate, and improve systems for the
  safety and the security of Meta products and services."
- Up to 2 years. https://dev.meta.ai/help/policies-and-privacy/zero-data-retention (stated as what Meta keeps even with
  Zero Data Retention, which we do not have): "If a request is flagged for an Acceptable Use Policy violation, Meta may
  retain the associated inputs and outputs for up to 2 years for abuse detection, integrity and content-safety
  enforcement, and to meet legal obligations."
- Not stated anywhere we found: a default retention period for content sent with `store: false` outside those cases.

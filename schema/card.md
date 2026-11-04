# The card

A card is what Fix-It Lens knows about one appliance model: its parts, its red zones, its upkeep and fix jobs, and
the steps of each job, every one cited to a page of the maker's manual. It lives at `cards/<brand>/<model>.yaml`.
The machine-checked form is `contracts/card.schema.json`; this page says the same thing in words. Where they
differ, the schema wins, and that is a bug in this page.

An example for the fake test appliance is `contracts/examples/card/acme-wm-0000.json`.

## What a card never holds

- **The manual's text.** A card cites pages and carries short verbatim quotes; it holds a hash of each page's text,
  never the text. The PDF never enters the repository.
- **A serial number, or anything else from your own plate** beyond the public model code.
- **A way to turn a safety rule off.** Red zones come from the rules files (`schema/rules.md`); a card can only add
  zones, never remove or shrink them below 3 cm.

## Quotes

Every quote is copied **verbatim** from the manual, in the manual's own language: at least 20 characters and at most
two sentences, with its page number. The citation check compares each quote with the page's extracted text, and a
card with a failing quote does not load. A quote has `text`, `lang` (ISO 639-1, e.g. `nl`) and `page`. It may also have `en`, an English translation shown under it: it is never
citation-checked and never replaces `text`, and it passes the sentence filter like step text.

Every quote is the maker's, not ours: it is quoted with its source (the maker, the model and the page), and the
card data's licence (`LICENSE-DATA`) does not cover it.

Quotes that several fields refer to live in the card's `quotes` list, each with an `id`; a field that names one
(`quote_ref`) holds that `id`.

## The fields

| Field | What it is |
|---|---|
| `schema_version` | `1` |
| `brand` | The maker, as a lower-case slug: `bosch`, `samsung`, `miele` |
| `model` | The public model code as printed on the plate (for Bosch, the E-Nr) |
| `aliases` | Other spellings of the model code |
| `appliance_kind` | `dishwasher`, `washer`, `dryer`, `boiler`, `fridge`, `oven`, `hob`, `hood` or `other` |
| `appliance_file_model` | The model ID that built the card from the manual, e.g. `muse-wonder-1.0` |
| `manual` | Where the manual came from (below) |
| `parts` | The parts the app may name or outline (below) |
| `red_zones` | The parts nobody touches (below) |
| `quotes` | Quotes referred to by `quote_ref` |
| `jobs` | Upkeep and fix jobs (below); at least one |
| `troubleshooting` | Optional: the manual's fault table, one row per code, light or symptom (below) |
| `refused` | Optional: jobs the manual describes that the rule engine refused at build, each `{id, title, reason, page}` with `reason` `gas`, `mains`, `needs_professional` or `other`. A name and a page so the app can say who to call; never steps. `reason: not_built` is a procedure the build could not turn into steps: `{reason, heading, page}`, with the manual's own heading and no id or title, so the app can say the manual also covers it on that page |
| `keywords` | Words that help speech recognition: part names, fault codes |
| `checks` | Filled in by the card loader when the app starts (`citation` and `rules`, each `pass`, `fail` or `unchecked`, with the failures). Leave it out of the file |

### The manual

| Field | What it is |
|---|---|
| `source` | `maker_site` (fetched from the maker's own support site, on a user's request) or `household_upload` (a household's own PDF; such a card is never shared). Absent on cards built before contracts 1.23, which were all fetched |
| `source_url` | For `maker_site`, the PDF's `https://` URL on the maker's site. For an upload, `urn:fixit:upload:<pdf_sha256>`: never a local path |
| `pdf_sha256` | The PDF file's digest. A different file for the same model is a new card version, never a silent swap |
| `models`, `models_page` | The model codes the manual itself prints, as printed (with its own patterns such as `SMV4HA*`), and the page that lists them. A plate's model links to the card only when it matches one of them; empty means the manual prints none, and the card stays private to the home that built it |
| `lang`, `page_count`, `page_text_sha256` | The manual's language, its page count, and one hash per page's text, in page order |
| `title`, `doc_id` | Optional: the manual's title and the maker's document number |

With `source` set, `pdf_sha256` and `models` are required.

### A part

| Field | What it is |
|---|---|
| `id` | A slug, e.g. `debris_filter` |
| `names` | The part's name per language: `{nl: pompfilter, en: debris filter}` |
| `locate` | How the model is asked to find it: `function` (by what it does) or `appearance` (by how it looks) |
| `cue` | What to look for: "round knob behind a small flap, bottom right of the front panel" |
| `sam_text` | A short noun phrase for the segmenter: one concept, no verbs, no "and", at most 40 characters |
| `kind` | `allowed`, or `red_zone` for a part that only ever appears as a zone |

### A red zone

A part the user must not touch, enforced on every camera frame (G1) and in every sentence (G0).

| Field | What it is |
|---|---|
| `id` | A slug, e.g. `gas_tap` |
| `part_id` | The part it is |
| `rule_id` | The rule that governs it, `<country>.<family>.<zone>` (`schema/rules.md`) |
| `page`, `quote` | Where the manual says so |
| `ring_cm` | How far around the part the zone reaches; **at least 3**, a floor that code enforces again |

### A job

| Field | What it is |
|---|---|
| `id` | A slug, e.g. `clean_filter` |
| `title` | Per language |
| `kind` | `upkeep` (on a schedule) or `fix` (after a fault). The app labels them **Upkeep** and **Fix**, listed under **Keep it running** and **Something's wrong** |
| `interval` | For upkeep: `every` (a whole number), `unit` (`day`, `week`, `month`, `year` or `cycle`) and `quote_ref`, the quote that states it. `null` for a fix. The app computes due dates from this and nothing else |
| `fault_codes` | The codes that start this job, e.g. `E24` (1 to 12 characters, as the manual prints them) |
| `landlord` | Whether a renter should leave it to the landlord (the country file can override) |
| `duration_min` | How long it takes, with its `source`: `manual_quote` (with `ref`, a quote id) or `session_log` (with `ref`, a session id). `null` if unknown. A duration on screen needs a source |
| `steps` | At least one |
| `due_name` | Optional: the short noun a reminder names, per language (`filter`), so it reads "Your dishwasher's filter is due". Absent: taken from the title |
| `after` | Optional: jobs this one comes to mind after, each `{job_id, because (per language), quote_ref}`; e.g. check the pressure after bleeding the radiators. It never moves a due date; it adds the history clause to a reminder, and its quote is citation-checked |
| `needs` | Optional: what to have ready before the job starts (below). Absent, `null` or `[]`: no list |

### A need

One thing to have ready before a job starts, as the manual calls for it: a machine cleaner, a towel, a new filter.
The app shows the list before the first step and the person says they have them ready. A card never invents one:
each need names a quote, and a need whose quote fails the citation check is dropped.

| Field | What it is |
|---|---|
| `id` | A slug, unique within its job |
| `what` | What to have ready, per language, in our own short words from the quote ("machine cleaner"). It goes through G0 at load, like step text |
| `kind` | Optional: `product` (a named cleaner or agent), `tool` (kept and reused: a screwdriver, a towel) or `consumable` (used up: salt, a new filter) |
| `quote_ref` | The `quotes` id of the manual's words that call for it. That quote carries the page, and the check record holds its hash (`sha256` of `<page>\n<quote text>`), so the citation check covers it like any other quote |

On the session socket (`contracts/ws.schema.json`), when hello names a job, the server sends `job_preview` after
`hello_ok` and before the first step: the job's title, its needs (each item's `id`, `what` already passed by G0,
`kind`, and the quote's `page` and `quote`, with `quote_en` where the card has one) and every step in order (its
line, page and quote, power, camera mode, and warnings: each active red zone's manual quote with its rule, and the
fixed power line). The phone answers `job_ready` with the need ids the person says they have; an id left out is one
they go ahead without. The first step follows. Readiness is recorded as said by the person, never as checked, and it
never clears a hold, a stop or a safety rule, or changes a step's power, zones or camera.

### A troubleshooting row

One line of the manual's fault table. `GET /appliances/{id}/meaning?code=` answers from these rows and the jobs'
`fault_codes`, and from nothing else. Every quote goes through the citation check like any other.

| Field | What it is |
|---|---|
| `id` | A slug, unique among the rows |
| `codes`, `indicator`, `symptom` | What a person sees: the codes as printed, a light (per language), or what happens (per language). At least one of the three |
| `symbol` | Where the code's symbol is drawn: `page` and `bbox`, four page fractions `[x0, y0, x1, y1]` with the origin top left, found by the code's text on the page. `null` when the manual has no figure |
| `meaning`, `cause` | Per language, as the manual says |
| `remedy` | What to do, per language; always present |
| `remedy_kind` | Who acts: `user`, `technician` or `installer`. A renter's landlord is never on a card: the server decides that from the household and the rules file |
| `escalate_to` | Only with `user`: who to call if it persists (`technician` or `installer`), else `null` |
| `job_ref` | The fix job that carries out the remedy, or `null`. Must name a fix job on the card; a dropped job means `null`. `null` whenever `remedy_kind` is not `user` |
| `prevented_by` | Upkeep jobs that prevent it, each `{job_id, quote_ref}` |
| `repeat` | When the manual says a repeat means a call: `{count, days, quote_ref}`, else `null` |
| `page`, `quote_ref` | The table's page and the row's quote |

### A step

| Field | What it is |
|---|---|
| `id` | A slug, **unique in the whole card** (across all jobs). The schema cannot say this; `contracts.load_card` and `validate("card", …)` check it, and a card that breaks it does not load |
| `text` | What the app says, per language, in its own short words |
| `page`, `quote` | The manual's own words for this step |
| `done_when` | The visible clauses that make the step done, each with an `id`, `text` and an optional `quote_ref`. The step check judges each clause; code decides the verdict |
| `parts` | The part ids the step works on (outlined in the camera view) |
| `power` | `off` (passes only when the camera sees a plug out of a socket, plus 0 W where Home Assistant has a power sensor), `on` or `any` |
| `zones` | The red-zone ids active during the step |
| `zone_rings` | Optional step-level ring overrides, `{zone_id, ring_cm}`, for an allowed part that sits inside a zone (the boiler's fill valve beside the gas tap). Never below 3 cm. A PR that adds one needs the safety reviewers |
| `view` | `front`, or `side` for a step near a red zone: the app will not start it from a front view |
| `look_only_lines` | Lines of this step that name a red-zone part only to look at it ("check the gas tap is open: look, do not touch"). Their hashes are listed as exemptions in the rule, so G0 lets exactly these lines through and nothing else |
| `witness` | What the SAM witness looks for: `part` (a part id), `expect` (`present` or `absent`) and `anchor` (the part it sits on or in, e.g. a drain cap's `drain`; `null` if none). Omit it, or `null`, when the step has no visible part to witness: then the judge and code decide alone |
| `camera` | Optional, what the camera does on this step: `check` (absent means check: the step check confirms it done on camera), `find` (the camera outlines the step's parts to help find them, with no done check; the person confirms) or `none` (no camera; the person confirms). Never `none` on a step with zones, zone rings, a red-zone part or `power` `on` or `off`, and `find` needs at least one part; `contracts.load_card` checks both. The mode never changes a safety gate: a power-off step still waits for the plug seen out |

### Our own step

A card's steps are the maker's. One exception: a step that only undoes what an earlier power-off step of the same
job left undone, which manuals often leave out: plug it back in, switch it back on, turn the water back on, or put
back a part the step took out ("Put the filter back in place"). It has `origin: fixit` and `restores` (that earlier step's id),
no `page` or `quote`, `power: any`, and no parts, zones, zone rings, look-only lines, witness or done-when clauses.
It comes after every power-off step of its job, and restores each step at most once; `contracts.load_card` checks
both. A person writes it, in a reviewed card, or ingest code appends a fixed one and marks it `added_by: ingest`
(absent means a person); no model writes it. A card with an ingest step stays private until a person accepts each one (the record's
`private_reason: steps_unaccepted`). G0 checks its text like any line, the citation
check skips it, and the phone marks it "Our step, not from the manual".

There is nothing on it for a camera to check, so the person ends it with a tap or by saying done. Its verdict has
`witnesses.by_person: true`, result `done`, no judge, `sam: none` and no clauses (`judge.schema.json`); the server
allows that only on a fixit step.

## Adding a card

1. Download the manual from the maker's own support site (never a mirror).
2. `fixit ingest manual.pdf --pr` drafts the card and opens a pull request.
3. Check every step against the page it cites. `make check-pr` checks the quotes on your machine with your copy of
   the manual; CI checks the schema, that every step cites a page, and the record.

# The rules file

A rules file holds one country's safety rules: its emergency scripts, who may work on gas and mains, the actions a
user may do on a sealed boiler, and the table the sentence filter (G0) and the zone stop (G1) enforce. It lives at
`rules/<country>.yaml`. The machine-checked form is `contracts/rules.schema.json`; this page says the same thing in
words, and where they differ the schema wins.

An example for a test country is `contracts/examples/rules/xx-test-country.json`.

**A country with no rules file fails closed:** no gas or mains guidance, the international emergency advice, and a
prompt to contribute the file. **An answer language with no rule table fails closed** the same way.

Safety stays code and stays on. A rules file adds rules; nothing in it, or anywhere else, turns a gate off.

## Rule IDs

Every rule has an ID of the form **`<country>.<family>.<zone>`**, all lower case:

- `country`: ISO 3166-1 alpha-2, the same as the file's `country`, e.g. `nl`;
- `family`: one of `gas`, `mains`, `water`, `heat`, `pressure`, `co`, `chemical`, `mechanical`;
- `zone`: letters and underscores, e.g. `casing`, `gas_tap`, `consumer_unit`.

Examples: `nl.mains.casing`, `nl.gas.tap`, `uk.gas.appliance`. The pattern is `^[a-z]{2}\.[a-z]+\.[a-z_]+$`. The
ID is what the amber bar shows ("Held back · rule nl.mains.casing · …"), what the log records, and what cards cite in
`red_zones[].rule_id`.

## The file

| Field | What it is |
|---|---|
| `country` | ISO 3166-1 alpha-2, lower case |
| `version` | `MAJOR.MINOR.PATCH` |
| `languages` | The answer languages this file has a rule table for, e.g. `[nl, en]` |
| `emergency` | The emergency script per language: who to call, and what to do first |
| `allow_lists.sealed_boiler` | The manufacturers' user-serviceable actions on a sealed gas boiler (read the pressure, top up at the filling point, bleed a radiator, reset per manual), each with an `id`, `names` per language and `sources`. **Everything else on a sealed boiler is a STOP** |
| `rules` | The rule table (below) |

## A rule

| Field | What it is |
|---|---|
| `id` | `<country>.<family>.<zone>` |
| `family`, `zone` | As in the ID |
| `parts` | Part names per language: `{nl: [achterwand, behuizing], en: [back panel, casing]}`. **Any red-zone part name in a sentence is held, whatever the verb** |
| `verbs` | Action verbs per language: `{nl: [openen, losdraaien], en: [open, unscrew]}`. A sentence that pairs one of the parts with one of the verbs matches the rule |
| `action` | `STOP` (stop the repair and say the script), `HOLD` (hold the sentence and say the line) or `ALLOW` (an explicit allowed pairing that a broader rule would otherwise catch) |
| `line` | The fixed line the app says in place of a held sentence, per language: "Behind the back panel is mains power. That's a technician." |
| `script` | The stop script, per language: "Hands back. Close the tap on the water hose." |
| `ring_cm` | The zone's ring around the part: **at least 3**. 3 cm is a floor in code; no data file can go below it, and a test fails the build if one tries |
| `landlord` | Whether this is the landlord's job for a renter |
| `sources` | At least one: the user guide or the law the rule rests on, with `title` and optionally `url`, `section` and `page` |
| `look_only_exemptions` | sha256 hashes of card lines that name the part only to look at it (a card's `look_only_lines`). G0 passes exactly those lines, and only those |

## How the filter reads a sentence

Every sentence the agent writes is checked against the table for the answer language **before** it is drawn or
spoken. A held sentence is never sent to the phone: the phone gets the rule ID and the line. The model can raise a
stop; it can never clear one. Code clears, on its own rules.

## Review

A pull request that widens a gas or mains allow-list, or adds a step-level zone override, needs two approvals from
the `safety-reviewers` team, and its author cannot merge it alone. (Until a second reviewer exists, the team is one
person, and the README says so.)

# fixit-rules

The safety rule engine: the country files, G0 (the sentence filter), `check_step`, the stop scripts and the G1
constants. It is code, not a model, and it has no off switch. Schema: `contracts/rules.schema.json`; in words:
`schema/rules.md`.

## Country files

| File | Answer languages | Smell gas | Who works on gas |
|---|---|---|---|
| `nl.yaml` (tested, filmed) | Dutch, English | 0800-9009 | a CO-vrij certified company |
| `uk.yaml` (`gb` loads it) | English | 0800 111 999; Northern Ireland 0800 002 001 | a Gas Safe registered engineer |
| `us.yaml` | English | leave first, then 911 or your gas utility | a contractor licensed in your state |
| `de.yaml` | English | your gas network operator's emergency line, or 112 | a firm on the network operator's installer register (NDAV §13) |

All four share the same zones. A country with no file fails closed: no gas or mains guidance, only the
international emergency advice and a prompt to contribute the file.

Every source in the UK, US and DE files was looked up when the file was written. A source that could not be found
says `(unverified)`, and so does anything that differs by state or brand.

## What a German-language user gets

There is no German rule table yet. If the answer language is German, in Germany or anywhere else, the app gives
no repair steps. It shows the rule's line in English with the emergency advice, and asks for a contributed table.
The user's own words are still checked: `de.yaml` lists German phrases for a gas smell, a CO alarm, smoke or fire, a
burning smell, water on the power and a breaker that trips again, and saying one stops the session. These phrases
are machine-checked, not native-checked. Since German answers already fail closed, a phrase the list misses costs
only a refusal.

## Changing a rule

Gas and mains rules, the sealed-boiler allow-list, ring sizes and look-only line hashes need two approvals from
the safety reviewers, the owners of the `/rules/` lines in `.github/CODEOWNERS`. There is no GitHub team: today that is
one person, so a change like this waits for a second reviewer.
